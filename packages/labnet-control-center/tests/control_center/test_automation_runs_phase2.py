"""Automation runs, Phase 2 groundwork: run settings, heartbeats, and failing runs whose runner went quiet."""

from __future__ import annotations

import io
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labnet_control_center import automation_runs
from labnet_control_center.app import create_app
from labnet_control_center.automation_runs import RunStore
from labnet_control_center.automation_store import AutomationError
from labnet_control_center.config import ControlCenterSettings

from .test_automation import FakeCentral, _settings, library  # noqa: F401  (library is a fixture)
from .test_automation_runner import RUNNER, _bearer, _claim, _project, _put, _queue, lab  # noqa: F401  (lab too)


DEFAULTS = {"run_model": None, "run_effort": None, "run_max_turns": 60, "run_budget_usd": 5.0}
QUIET = "The runner stopped reporting (nothing for 10 minutes)."


def _later(client: TestClient, monkeypatch: pytest.MonkeyPatch, minutes: float) -> None:
    """Move the run store's clock ``minutes`` ahead of the real one."""

    monkeypatch.setattr(client.app.state.runs, "_now", lambda: datetime.now(timezone.utc) + timedelta(minutes=minutes))


def _record(tmp_path: Path, run: dict) -> dict:
    """The run's record as it is on disk (reading it through the API would sweep it)."""

    path = tmp_path / "automation" / "state" / run["project_id"] / "runs" / f"{run['id']}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _save_project(client: TestClient, project: dict, **fields) -> dict:
    reply = client.put(f"/api/automation/projects/{project['id']}", json={
        "version": project["version"], "name": project["name"], **fields,
    })
    assert reply.status_code == 200, reply.text
    return reply.json()["project"]


# ---- run settings -------------------------------------------------------------------------------


def test_projects_have_run_settings_with_defaults(lab: TestClient) -> None:
    project = _project(lab)
    assert {key: project[key] for key in DEFAULTS} == DEFAULTS

    chosen = _project(lab, name="Chosen", run_model="opus", run_effort="max", run_max_turns=120, run_budget_usd=12)
    assert (chosen["run_model"], chosen["run_effort"], chosen["run_max_turns"], chosen["run_budget_usd"]) == (
        "opus", "max", 120, 12.0)

    api = "/api/automation/projects"
    for bad in ({"run_model": "gpt-4"}, {"run_model": ""}, {"run_model": "a b"}, {"run_model": 5},
                {"run_effort": "huge"}, {"run_effort": "High"}, {"run_max_turns": 0}, {"run_max_turns": 201},
                {"run_max_turns": 5.5}, {"run_max_turns": True}, {"run_budget_usd": 0}, {"run_budget_usd": -1},
                {"run_budget_usd": 100.01}, {"run_budget_usd": "5"}):
        reply = lab.post(api, json={"name": "x", **bad})
        assert reply.status_code == 422, bad
        assert next(iter(bad)) in reply.json()["detail"], (bad, reply.json())
    unknown = lab.put(f"{api}/{project['id']}", json={"version": 1, "name": "x", "run_model": "claude-unknown-9"})
    assert unknown.status_code == 422 and "sonnet" in unknown.json()["detail"]


def test_a_save_without_run_settings_keeps_them(lab: TestClient) -> None:
    project = _project(lab, run_model="haiku", run_max_turns=10)
    renamed = _save_project(lab, project, description="An older page sends only these.")
    assert renamed["run_model"] == "haiku" and renamed["run_max_turns"] == 10
    reset = _save_project(lab, renamed, run_model=None, run_budget_usd=2.5)
    assert reset["run_model"] is None and reset["run_budget_usd"] == 2.5 and reset["run_max_turns"] == 10


def test_projects_saved_before_run_settings_still_load(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    path = tmp_path / "automation" / "state" / project["id"] / "project.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    for key in DEFAULTS:
        del saved[key]
    path.write_text(json.dumps(saved), encoding="utf-8")

    loaded = lab.get(f"/api/automation/projects/{project['id']}").json()["project"]
    assert {key: loaded[key] for key in DEFAULTS} == DEFAULTS
    assert [item["id"] for item in lab.get("/api/automation/projects").json()["projects"]] == [project["id"]]
    run = _queue(lab, project)
    assert run["settings"] == {"model": "sonnet", "effort": "high", "max_turns": 60, "max_budget_usd": 5.0}


def test_a_run_keeps_the_settings_it_was_queued_with(lab: TestClient) -> None:
    project = _project(lab, run_model="opus", run_effort="low", run_max_turns=12, run_budget_usd=2.5)
    lab.put(f"/api/automation/projects/{project['id']}/files/Tasks/Task2.md", content=b"# Task 2")
    queued = _queue(lab, project)
    expected = {"model": "opus", "effort": "low", "max_turns": 12, "max_budget_usd": 2.5}
    assert queued["settings"] == expected

    # Changing the project afterwards changes only later runs; Haiku has no effort levels.
    _save_project(lab, project, run_model="haiku", run_effort="max", run_max_turns=3, run_budget_usd=1)
    api = f"/api/automation/projects/{project['id']}/runs"
    assert lab.get(f"{api}/{queued['id']}").json()["run"]["settings"] == expected
    later = _queue(lab, project, "Tasks/Task2.md")
    assert later["settings"] == {"model": "haiku", "effort": None, "max_turns": 3, "max_budget_usd": 1.0}

    run = _claim(lab)
    assert run["id"] == queued["id"] and run["settings"] == expected
    reply = lab.get(f"{RUNNER}/runs/{run['id']}/inputs", headers=_bearer(lab.tokens["lab-laptop"]))
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(reply.content)).read("run.json"))
    assert manifest["settings"] == expected and manifest["run"]["settings"] == expected


def test_the_control_centers_defaults_fill_in_and_a_model_no_longer_offered_is_refused(
    tmp_path: Path, library: Path  # noqa: F811
) -> None:
    settings = _settings(tmp_path, agent_literature_dir=library, agent_models=("opus", "haiku"), agent_model="opus",
                         agent_effort="medium")
    with TestClient(create_app(settings, client_factory=lambda _: FakeCentral())) as client:
        project = _project(client)
        assert _queue(client, project)["settings"] == {
            "model": "opus", "effort": "medium", "max_turns": 60, "max_budget_usd": 5.0}
        assert client.post("/api/automation/projects", json={"name": "x", "run_model": "sonnet"}).status_code == 422
        # A project saved while another model was offered: its runs can't be queued until it picks one.
        store = client.app.state.automation
        saved = store.get(project["id"])
        store._save({**saved, "run_model": "sonnet"})
        client.put(f"/api/automation/projects/{project['id']}/files/Tasks/Task2.md", content=b"# Task 2")
        refused = client.post(f"/api/automation/projects/{project['id']}/runs", json={"task": "Tasks/Task2.md"})
        assert refused.status_code == 422 and "Settings" in refused.json()["detail"]


# ---- heartbeats --------------------------------------------------------------------------------------


def test_every_runner_call_about_a_run_says_the_runner_is_alive(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    assert run["last_seen_at"] == run["claimed_at"]
    token = _bearer(lab.tokens["lab-laptop"])
    base = f"{RUNNER}/runs/{run['id']}"
    seen = [run["last_seen_at"]]

    def stamped() -> None:
        now = _record(tmp_path, run)["last_seen_at"]
        assert now > seen[-1]
        seen.append(now)

    beat = lab.post(f"{base}/heartbeat", headers=token)
    assert beat.status_code == 200 and beat.json() == {"status": "active"}
    stamped()
    assert lab.post(f"{base}/events", json={"events": [{"kind": "status", "text": "planning"}]}, headers=token).status_code == 200
    stamped()
    assert lab.get(base, headers=token).status_code == 200
    stamped()
    assert lab.get(f"{base}/inputs", headers=token).status_code == 200
    stamped()
    assert _put(lab, run, "Analysis/Analysis.md", content=b"# Analysis").status_code == 200
    stamped()
    assert _record(tmp_path, run)["bytes_uploaded"] == len(b"# Analysis")  # one save, both changes
    finished = lab.post(f"{base}/finish", json={"status": "done"}, headers=token)
    assert finished.status_code == 200
    stamped()

    # Only the runner that claimed it, and only while it is active.
    assert lab.post(f"{base}/heartbeat", headers=_bearer(lab.tokens["central-docker"])).status_code == 404
    over = lab.post(f"{base}/heartbeat", headers=token)
    assert over.status_code == 409 and over.json()["status"] == "done"
    assert lab.post(f"{base}/heartbeat").status_code == 401
    assert lab.post(f"{RUNNER}/runs/run-20200101-000000-abcd/heartbeat", headers=token).status_code == 404
    assert lab.get(base, headers=token).status_code == 200 and _record(tmp_path, run)["last_seen_at"] == seen[-1]


# ---- runs whose runner went quiet --------------------------------------------------------------------


def test_a_run_whose_runner_went_quiet_is_failed(lab: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    token = _bearer(lab.tokens["lab-laptop"])
    base = f"{RUNNER}/runs/{run['id']}"
    api = f"/api/automation/projects/{project['id']}/runs"

    _later(lab, monkeypatch, 9)  # not yet
    assert lab.get(api).json()["runs"][0]["status"] == "active"
    assert lab.post(f"{base}/heartbeat", headers=token).json() == {"status": "active"}  # the clock starts again
    _later(lab, monkeypatch, 18)
    assert lab.get(f"{api}/{run['id']}").json()["run"]["status"] == "active"

    _later(lab, monkeypatch, 20)
    shown = lab.get(f"{api}/{run['id']}").json()
    assert shown["run"]["status"] == "failed" and shown["run"]["summary"] == QUIET and shown["run"]["finished_at"]
    assert [event["kind"] for event in shown["events"]] == ["timeout"]
    assert "lab-laptop" in shown["events"][0]["text"] and shown["events"][0]["at"]

    # The runner learns it on its next request.
    for reply in (
        lab.post(f"{base}/heartbeat", headers=token),
        lab.post(f"{base}/events", json={"events": []}, headers=token),
        lab.post(f"{base}/finish", json={"status": "done"}, headers=token),
        lab.get(f"{base}/inputs", headers=token),
        _put(lab, run, "Analysis/late.md"),
    ):
        assert reply.status_code == 409 and reply.json()["status"] == "failed"
    assert lab.get(base, headers=token).json()["run"]["status"] == "failed"
    assert lab.post(f"{api}/{run['id']}/cancel", json={}).status_code == 409
    assert lab.delete(f"/api/automation/projects/{project['id']}").status_code == 204  # nothing open any more


@pytest.mark.parametrize("how", ["claim", "list", "find", "sweep"])
def test_every_way_of_reading_runs_fails_the_quiet_ones(
    lab: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    _later(lab, monkeypatch, 11)
    runs = lab.app.state.runs
    if how == "claim":
        assert _claim(lab, "central-docker") is None
    elif how == "list":
        runs.list(project["id"])
    elif how == "find":
        runs.find(run["id"])
    else:
        assert [item["id"] for item in runs.sweep()] == [run["id"]]
        assert runs.sweep() == []
    record = _record(tmp_path, run)
    assert record["status"] == "failed" and record["summary"] == QUIET


def test_records_from_before_heartbeats_count_from_their_claim(
    lab: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    path = tmp_path / "automation" / "state" / project["id"] / "runs" / f"{run['id']}.json"
    old = _record(tmp_path, run)
    del old["last_seen_at"], old["settings"]
    path.write_text(json.dumps(old), encoding="utf-8")
    runs = lab.app.state.runs

    _later(lab, monkeypatch, 9)
    assert runs.sweep() == [] and runs.get(project["id"], run["id"])["last_seen_at"] is None
    _later(lab, monkeypatch, 11)
    assert [item["id"] for item in runs.sweep()] == [run["id"]]


def test_a_restarted_control_center_gives_runners_a_full_window(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    path = tmp_path / "automation" / "state" / project["id"] / "runs" / f"{run['id']}.json"
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps({**_record(tmp_path, run), "last_seen_at": long_ago}), encoding="utf-8")

    # A new process (the Control Center restarted): the runner couldn't reach it while it was down.
    restarted = RunStore(lab.app.state.automation, quota_bytes=1000, stale_minutes=10)
    assert restarted.sweep() == []
    restarted._now = lambda: datetime.now(timezone.utc) + timedelta(minutes=11)
    assert [item["id"] for item in restarted.sweep()] == [run["id"]]


def test_an_upload_still_streaming_keeps_a_run_alive_and_the_quota_stays_exact(
    lab: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    runs = lab.app.state.runs
    runs.begin_upload(run["id"], "lab-laptop", expected=None)
    _later(lab, monkeypatch, 9)
    runs.reserve(run["id"], 4000)  # a chunk arrives
    _later(lab, monkeypatch, 15)
    assert runs.sweep() == []  # last chunk 6 minutes ago
    _later(lab, monkeypatch, 25)
    assert [item["id"] for item in runs.sweep()] == [run["id"]]
    # The failed run's quota is forgotten, as for a cancel: the upload in flight can't go on.
    with pytest.raises(AutomationError) as ended:
        runs.reserve(run["id"], 10)
    assert ended.value.status_code == 409
    runs.release(run["id"], 4000)  # the upload gives its bytes back: nothing to give back to
    with pytest.raises(AutomationError) as again:
        runs.begin_upload(run["id"], "lab-laptop", expected=10)
    assert again.value.status_code == 409


def test_the_sweeper_runs_with_the_app_and_stops_with_it(
    tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(automation_runs, "SWEEP_SECONDS", 0.02)
    settings = _settings(tmp_path, agent_literature_dir=library, automation_run_stale_minutes=1)
    with TestClient(create_app(settings, client_factory=lambda _: FakeCentral())) as client:
        sweeper = client.app.state.run_sweeper
        assert not sweeper.done()
        client.tokens = {"lab-laptop": client.app.state.runners.add("lab-laptop")}
        project = _project(client)
        _queue(client, project)
        run = _claim(client)
        _later(client, monkeypatch, 2)
        deadline = time.monotonic() + 10
        while _record(tmp_path, run)["status"] == "active" and time.monotonic() < deadline:
            time.sleep(0.02)
        record = _record(tmp_path, run)  # failed without anyone asking about it
        assert record["status"] == "failed" and record["summary"] == "The runner stopped reporting (nothing for 1 minute)."
    assert sweeper.done() and sweeper.cancelled()


def test_the_sweeper_survives_a_failing_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(automation_runs, "SWEEP_SECONDS", 0.001)
    calls = []

    class Broken:
        def sweep(self):
            calls.append(1)
            raise OSError("the disk went away")

    async def main() -> None:
        task = asyncio.create_task(automation_runs.sweep_forever(Broken()))
        while len(calls) < 3:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())


# ---- the setting -------------------------------------------------------------------------------------


def test_the_stale_time_is_a_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABNET_API_KEY", "key")
    monkeypatch.setenv("LABNET_AUTOMATION_DIR", str(tmp_path / "automation"))
    monkeypatch.setenv("LABNET_CONTROL_CENTER_EXPERIMENTS_DIR", str(tmp_path / "experiments"))
    monkeypatch.setenv("LABNET_AGENT_WORKSPACE", str(tmp_path / "agent"))
    monkeypatch.delenv("LABNET_AUTOMATION_RUN_STALE_MINUTES", raising=False)
    assert ControlCenterSettings.from_env(search_cwd=False).automation_run_stale_minutes == 10
    monkeypatch.setenv("LABNET_AUTOMATION_RUN_STALE_MINUTES", "2.5")
    assert ControlCenterSettings.from_env(search_cwd=False).automation_run_stale_minutes == 2.5
    for bad in ("0", "-1", "nan", "inf"):
        monkeypatch.setenv("LABNET_AUTOMATION_RUN_STALE_MINUTES", bad)
        with pytest.raises(ValueError, match="LABNET_AUTOMATION_RUN_STALE_MINUTES"):
            ControlCenterSettings.from_env(search_cwd=False)
