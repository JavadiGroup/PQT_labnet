"""Automation runs and the runner API: tokens, claiming, the agent's upload zones, quotas, inputs, events."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient

from labnet_control_center import admin, automation_runs
from labnet_control_center.app import create_app
from labnet.safe_paths import NotWritable, check_agent_write, clean_relative
from labnet_control_center.automation_runners import RunnerRegistry
from labnet_control_center.automation_store import AutomationError

from .test_automation import EXPERIMENT, FakeCentral, _settings, library  # noqa: F401  (library is a fixture)


SECRET = "s3cret-" + "x" * 41
SIGNED_IN = {"X-LabNet-Proxy": SECRET, "X-LabNet-User": "alice"}
SPIN = "spin_qubits_and_control/Optical spin locking of a solid-state qubit.pdf"
PDH = "lasers_and_stabilization/cavities_and_locking/An introduction to Pound–Drever–Hall laser frequency stabilization.pdf"
RUNNER = "/api/automation/runner"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def lab(tmp_path: Path, library: Path):  # noqa: F811
    """A Control Center without logins, ~10 KB files and a ~20 KB run quota, and two runners."""

    settings = _settings(
        tmp_path, agent_literature_dir=library, automation_max_file_mb=0.01, automation_run_quota_mb=0.02
    )
    with TestClient(create_app(settings, client_factory=lambda _: FakeCentral())) as client:
        client.tokens = {name: client.app.state.runners.add(name) for name in ("lab-laptop", "central-docker")}
        yield client


def _project(client: TestClient, headers: dict | None = None, **fields) -> dict:
    reply = client.post("/api/automation/projects", json={"name": "AOM study", **fields}, headers=headers or {})
    assert reply.status_code == 201, reply.text
    return reply.json()["project"]


def _queue(client: TestClient, project: dict, task: str = "Tasks/Task1.md", **extra) -> dict:
    reply = client.post(f"/api/automation/projects/{project['id']}/runs", json={"task": task, **extra})
    assert reply.status_code == 201, reply.text
    return reply.json()["run"]


def _claim(client: TestClient, runner: str = "lab-laptop") -> dict | None:
    reply = client.post(f"{RUNNER}/claim", headers=_bearer(client.tokens[runner]))
    if reply.status_code == 204:
        return None
    assert reply.status_code == 200, reply.text
    return reply.json()["run"]


def _put(client: TestClient, run: dict, path: str, content=b"x", runner: str = "lab-laptop"):
    return client.put(f"{RUNNER}/runs/{run['id']}/files/{path}", content=content, headers=_bearer(client.tokens[runner]))


def _folder(tmp_path: Path, project: dict) -> Path:
    return tmp_path / "automation" / "projects" / project["id"]


# ---- runner tokens -------------------------------------------------------------------------


def test_runner_tokens_are_kept_only_as_hashes(tmp_path: Path) -> None:
    registry = RunnerRegistry(tmp_path / "runners")
    token = registry.add("lab-laptop")
    assert token.startswith("lnr_") and len(token) == 47
    record = (tmp_path / "runners" / "lab-laptop.json").read_text(encoding="utf-8")
    assert token not in record and token[4:] not in record
    assert json.loads(record)["sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert set(json.loads(record)) == {"name", "sha256", "created_at"}

    assert registry.authenticate(token) == "lab-laptop"
    for wrong in ("", token[:-1], token + "x", token.replace("lnr_", "lnx_"), "lnr_" + "A" * 43, None):
        assert registry.authenticate(wrong) is None
    (tmp_path / "runners" / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "runners" / "liar.json").write_text(json.dumps({"name": "lab-laptop", "sha256": "0" * 64}))
    assert registry.authenticate(token) == "lab-laptop"  # unreadable or mismatched records are ignored
    assert [item["name"] for item in registry.list()] == ["lab-laptop"]

    with pytest.raises(AutomationError) as exists:
        registry.add("lab-laptop")
    assert exists.value.status_code == 409
    for bad in ("Lab-Laptop", "-x", "a" * 33, "../x", "a b", ""):
        with pytest.raises(AutomationError) as refused:
            registry.add(bad)
        assert refused.value.status_code == 422

    registry.revoke("lab-laptop")
    assert registry.authenticate(token) is None
    with pytest.raises(AutomationError) as gone:
        registry.revoke("lab-laptop")
    assert gone.value.status_code == 404


def test_the_admin_cli_adds_lists_and_revokes_runners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LABNET_API_KEY", "key")
    monkeypatch.setenv("LABNET_AUTOMATION_DIR", str(tmp_path / "automation"))
    env = tmp_path / "control-center.env"
    env.write_text("# settings come from the environment in this test\n", encoding="utf-8")
    arguments = ["--env-file", str(env), "runner"]

    assert admin.main([*arguments, "add", "lab-laptop"]) == 0
    printed = capsys.readouterr().out
    token = next(word for word in printed.split() if word.startswith("lnr_"))
    assert "shown only now" in printed
    registry = RunnerRegistry(tmp_path / "automation" / "state" / "runners")
    assert registry.authenticate(token) == "lab-laptop"
    assert token not in (tmp_path / "automation" / "state" / "runners" / "lab-laptop.json").read_text(encoding="utf-8")

    assert admin.main([*arguments, "list"]) == 0
    assert "lab-laptop" in capsys.readouterr().out
    assert admin.main([*arguments, "add", "lab-laptop"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert admin.main([*arguments, "add", "Not Valid"]) == 1
    assert admin.main([*arguments, "revoke", "lab-laptop"]) == 0
    assert registry.authenticate(token) is None
    assert admin.main([*arguments, "revoke", "lab-laptop"]) == 1
    assert admin.main([*arguments, "list"]) == 0
    assert "No runners" in capsys.readouterr().out
    assert admin.main(["--env-file", str(tmp_path / "missing.env"), "runner", "list"]) == 2


# ---- who may use the runner API -------------------------------------------------------------


def test_runner_endpoints_need_a_valid_runner_token(lab: TestClient) -> None:
    whoami = f"{RUNNER}/whoami"
    missing = lab.get(whoami)
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
    assert lab.get(whoami, headers={"Authorization": "Bearer lnr_nonsense"}).status_code == 401
    assert lab.get(whoami, headers={"Authorization": f"Basic {lab.tokens['lab-laptop']}"}).status_code == 401
    assert lab.post(f"{RUNNER}/claim").status_code == 401
    assert lab.put(f"{RUNNER}/runs/run-20261007-120000-abcd/files/Analysis/x.md", content=b"x").status_code == 401
    assert lab.get(whoami, headers=_bearer(lab.tokens["lab-laptop"])).json() == {"runner": "lab-laptop"}
    lab.app.state.runners.revoke("lab-laptop")
    assert lab.get(whoami, headers=_bearer(lab.tokens["lab-laptop"])).status_code == 401


def test_browsers_cannot_use_the_runner_api(lab: TestClient) -> None:
    token = _bearer(lab.tokens["lab-laptop"])
    for site in ("same-origin", "cross-site", "none"):
        reply = lab.get(f"{RUNNER}/whoami", headers={**token, "Sec-Fetch-Site": site})
        assert reply.status_code == 403 and "not browsers" in reply.json()["detail"]
    assert lab.post(f"{RUNNER}/claim", headers={**token, "Sec-Fetch-Site": "same-origin"}).status_code == 403


def test_with_logins_a_runner_token_reaches_only_the_runner_api(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, proxy_secret=SECRET), client_factory=lambda _: FakeCentral())) as client:
        token = _bearer(client.app.state.runners.add("lab-laptop"))
        assert client.get("/api/automation/projects", headers=token).status_code == 403
        assert client.post("/api/automation/projects", json={"name": "x"}, headers=token).status_code == 403
        assert client.get(f"{RUNNER}/whoami", headers=token).json() == {"runner": "lab-laptop"}  # no proxy header
        # A person signed in through Caddy has no runner token.
        assert client.get(f"{RUNNER}/whoami", headers=SIGNED_IN).status_code == 401
        assert client.get("/api/automation/projects", headers=SIGNED_IN).status_code == 200
        # The signed-in person is who queued a run, whatever name the page sends.
        project = _project(client, SIGNED_IN)
        queued = client.post(
            f"/api/automation/projects/{project['id']}/runs", json={"task": "Tasks/Task1.md", "queued_by": "mallory"},
            headers=SIGNED_IN,
        )
        assert queued.status_code == 201 and queued.json()["run"]["queued_by"] == "alice"


# ---- people queue, list, look at and cancel runs ---------------------------------------------------


def test_people_queue_list_and_cancel_runs(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    api = f"/api/automation/projects/{project['id']}/runs"
    run = _queue(lab, project, queued_by="Rob")
    assert automation_runs.RUN_ID.fullmatch(run["id"])
    assert run["status"] == "queued" and run["queued_by"] == "Rob" and run["task"] == "Tasks/Task1.md"
    assert run["quota_bytes"] == int(0.02 * 2**20) and run["bytes_uploaded"] == 0
    assert (tmp_path / "automation" / "state" / project["id"] / "runs" / f"{run['id']}.json").is_file()

    again = lab.post(api, json={"task": "tasks/task1.md"})
    assert again.status_code == 409 and again.json()["run"]["id"] == run["id"]
    assert lab.post(api, json={"task": "Tasks/Task9.md"}).status_code == 404
    for wrong in ("Literature/x.md", "Tasks", "Tasks/notes.txt", "../Tasks/Task1.md", "Tasks/.x.md", "/Tasks/Task1.md"):
        assert lab.post(api, json={"task": wrong}).status_code == 422, wrong
    assert lab.post(api, json={}).status_code == 422
    assert lab.post(api, content=b'{"task": "Tasks/Task1.md"}', headers={"content-type": "text/plain"}).status_code == 415
    assert lab.post(api, json={"task": "Tasks/Task1.md"}, headers={"sec-fetch-site": "cross-site"}).status_code == 403

    lab.put(f"/api/automation/projects/{project['id']}/files/Tasks/Task2.md", content=b"# Task 2")
    second = _queue(lab, project, "Tasks/Task2.md")
    listed = lab.get(api).json()["runs"]
    assert [item["id"] for item in listed] == [second["id"], run["id"]]  # newest first

    shown = lab.get(f"{api}/{run['id']}").json()
    assert shown["run"] == run and shown["events"] == []
    assert lab.get(f"{api}/run-bad").status_code == 404
    assert lab.get(f"{api}/run-20200101-000000-abcd").status_code == 404

    # A project with an open run can't be deleted.
    deleted = lab.delete(f"/api/automation/projects/{project['id']}")
    assert deleted.status_code == 409 and "Cancel it first" in deleted.json()["detail"]
    cancel = lab.post(f"{api}/{run['id']}/cancel", json={"cancelled_by": "Rob"})
    assert cancel.status_code == 200
    assert cancel.json()["run"]["status"] == "cancelled" and cancel.json()["run"]["cancelled_by"] == "Rob"
    again = lab.post(f"{api}/{run['id']}/cancel", json={})
    assert again.status_code == 409 and again.json()["status"] == "cancelled"
    assert lab.post(f"{api}/{run['id']}/cancel", content=b"{}", headers={"content-type": "text/plain"}).status_code == 415
    assert lab.post(f"{api}/{second['id']}/cancel", json={}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert lab.post(f"{api}/{second['id']}/cancel", json={}).status_code == 200
    requeued = _queue(lab, project)  # a cancelled task can be queued again
    assert requeued["id"] != run["id"]
    assert lab.post(f"{api}/{requeued['id']}/cancel", json={}).status_code == 200
    assert lab.delete(f"/api/automation/projects/{project['id']}").status_code == 204


# ---- claiming ------------------------------------------------------------------------------------


def test_runners_claim_the_oldest_queued_run_and_only_they_may_touch_it(lab: TestClient) -> None:
    first, second = _project(lab, name="First"), _project(lab, name="Second")
    lab.put(f"/api/automation/projects/{first['id']}/files/Tasks/Task2.md", content=b"# Task 2")
    queued = [_queue(lab, first), _queue(lab, second), _queue(lab, first, "Tasks/Task2.md")]

    assert _claim(lab)["id"] == queued[0]["id"]
    taken = _claim(lab, "central-docker")
    assert taken["id"] == queued[1]["id"] and taken["status"] == "active" and taken["claimed_by"] == "central-docker"
    assert _claim(lab)["id"] == queued[2]["id"]
    assert _claim(lab) is None

    # lab-laptop can't see, read, write or finish central-docker's run.
    token = _bearer(lab.tokens["lab-laptop"])
    base = f"{RUNNER}/runs/{taken['id']}"
    assert lab.get(base, headers=token).status_code == 404
    assert lab.get(f"{base}/inputs", headers=token).status_code == 404
    assert _put(lab, taken, "Analysis/Analysis.md").status_code == 404
    assert lab.post(f"{base}/events", json={"events": []}, headers=token).status_code == 404
    assert lab.post(f"{base}/finish", json={"status": "done"}, headers=token).status_code == 404
    assert lab.get(base, headers=_bearer(lab.tokens["central-docker"])).json()["run"]["id"] == taken["id"]
    assert lab.get(f"{RUNNER}/runs/not-a-run", headers=token).status_code == 404


# ---- what the agent may write ---------------------------------------------------------------------


ALLOWED = [
    "Analysis/Analysis.md", "Analysis/outputs/fig.png", "Data/new_data/x.csv", "ScriptsUtilized/logs/r1/out.log",
    "Protocols/Task1.protocol.json", "ScriptsUtilized/src/fit.py", "Analysis/outputs/report.html",
]
REFUSED = [
    "Tasks/x.md", "Literature/x.pdf", "Data/example_data/x.csv", "Experiment_layout/x.json",
    "Experiment_layout/LAYOUT.md", "Experiment_layout/DEVICES.md", ".claude/settings.json", ".mcp.json",
    "Analysis/.hidden", "Analysis/CLAUDE.md", "Analysis/sub/claude-notes.md", "Analysis/Claude.local.MD",
    "Analysis/claude.md/x.txt", "x.md", "Data/x.csv", "Analysis", "Data/new_data", "ScriptsUtilized/logs",
]


@pytest.mark.parametrize("path", ALLOWED)
def test_the_agent_writes_in_its_own_folders(path: str) -> None:
    assert check_agent_write(clean_relative(path)).owner == "agent"


@pytest.mark.parametrize("path", REFUSED)
def test_the_agent_writes_nowhere_else(path: str) -> None:
    with pytest.raises(NotWritable):
        check_agent_write(PurePosixPath(path))


def test_agent_folders_can_be_named_but_not_written_as_files() -> None:
    assert check_agent_write(clean_relative("Analysis"), folder=True).owner == "agent"
    assert check_agent_write(clean_relative("ScriptsUtilized/logs/r1"), folder=True).owner == "agent"
    with pytest.raises(NotWritable):
        check_agent_write(clean_relative("Tasks/sub"), folder=True)


def test_runner_uploads_land_only_in_the_agents_folders(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    folder = _folder(tmp_path, project)
    for path in ALLOWED:
        reply = _put(lab, run, path, content=path.encode())
        assert reply.status_code == 200, (path, reply.text)
        assert reply.json()["file"]["owner"] == "agent" and reply.json()["file"]["path"] == path
        assert (folder / path).read_bytes() == path.encode()
    # Replacing needs no If-Match: the agent's files have one writer.
    replaced = _put(lab, run, "Analysis/Analysis.md", content=b"# Analysis v2")
    assert replaced.status_code == 200 and (folder / "Analysis" / "Analysis.md").read_bytes() == b"# Analysis v2"
    assert replaced.json()["quota_bytes"] == run["quota_bytes"]

    before = sorted(str(item.relative_to(folder)) for item in folder.rglob("*"))
    for path in REFUSED + ["%2E%2E/x.md", "Analysis/%2E%2E/%2E%2E/escape.md", "/Analysis/x.md", "Analysis%5Cx.md",
                           "Analysis/a:b.md", "Analysis/CON.md", "Analysis/" + "x/" * 8 + "y.md"]:
        reply = _put(lab, run, path)
        assert reply.status_code in (403, 404, 422), (path, reply.status_code)
    assert sorted(str(item.relative_to(folder)) for item in folder.rglob("*")) == before  # nothing new, not even temp files
    assert not (tmp_path / "automation" / "x.md").exists() and not (tmp_path / "automation" / "projects" / "x.md").exists()
    assert lab.get(f"{RUNNER}/runs/{run['id']}", headers=_bearer(lab.tokens["lab-laptop"])).json()["run"][
        "files_uploaded"] == len(ALLOWED) + 1


def test_a_folder_in_the_way_is_a_conflict(lab: TestClient) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    assert _put(lab, run, "Analysis/outputs/fig/a.png").status_code == 200
    assert _put(lab, run, "Analysis/outputs/fig").status_code == 409
    assert _put(lab, run, "Analysis/outputs/fig/a.png/b.png").status_code == 409


def test_the_run_quota_and_file_size_hold(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    folder = _folder(tmp_path, project)
    assert _put(lab, run, "Data/new_data/big.csv", content=b"x" * 11_000).status_code == 413  # one file: ~10 KB
    eight = b"x" * 8000
    assert _put(lab, run, "Data/new_data/a.csv", content=eight).json()["bytes_uploaded"] == 8000
    assert _put(lab, run, "Data/new_data/b.csv", content=eight).json()["bytes_uploaded"] == 16000
    over = _put(lab, run, "Data/new_data/c.csv", content=eight)  # 24 000 > the ~20 KB quota
    assert over.status_code == 413 and over.json()["quota_bytes"] == run["quota_bytes"]

    def chunks():
        for _ in range(4):
            yield b"x" * 2000  # 8 KB without Content-Length

    assert _put(lab, run, "Data/new_data/d.csv", content=chunks()).status_code == 413
    assert not (folder / "Data" / "new_data" / "c.csv").exists() and not (folder / "Data" / "new_data" / "d.csv").exists()
    assert not list(folder.rglob(".upload-*"))
    small = _put(lab, run, "Data/new_data/e.csv", content=b"y" * 4000)  # what's left still fits
    assert small.status_code == 200 and small.json()["bytes_uploaded"] == 20000


def test_uploads_still_streaming_count_against_the_quota(lab: TestClient) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    runs = lab.app.state.runs
    runs.begin_upload(run["id"], "lab-laptop", expected=None)
    runs.reserve(run["id"], 12_000)  # one upload, still streaming
    with pytest.raises(AutomationError) as over:
        runs.reserve(run["id"], 12_000)  # a second one alongside it
    assert over.value.status_code == 413
    runs.release(run["id"], 12_000)  # the first one failed
    runs.reserve(run["id"], 12_000)
    with pytest.raises(AutomationError) as known:
        runs.begin_upload(run["id"], "lab-laptop", expected=12_000)  # a Content-Length that can't fit
    assert known.value.status_code == 413


def test_nothing_is_accepted_after_a_run_ends(lab: TestClient) -> None:
    project = _project(lab)
    lab.put(f"/api/automation/projects/{project['id']}/files/Tasks/Task2.md", content=b"# Task 2")
    _queue(lab, project)
    _queue(lab, project, "Tasks/Task2.md")
    finished, cancelled = _claim(lab), _claim(lab)
    token = _bearer(lab.tokens["lab-laptop"])

    done = lab.post(f"{RUNNER}/runs/{finished['id']}/finish", json={"status": "done", "summary": "All good."}, headers=token)
    assert done.status_code == 200 and done.json()["run"]["status"] == "done" and done.json()["run"]["finished_at"]
    human = f"/api/automation/projects/{project['id']}/runs"
    assert lab.post(f"{human}/{cancelled['id']}/cancel", json={}).status_code == 200

    for run, status in ((finished, "done"), (cancelled, "cancelled")):
        base = f"{RUNNER}/runs/{run['id']}"
        upload = _put(lab, run, "Analysis/late.md")
        assert upload.status_code == 409 and upload.json()["status"] == status
        assert lab.post(f"{base}/events", json={"events": []}, headers=token).json()["status"] == status
        assert lab.post(f"{base}/finish", json={"status": "failed"}, headers=token).status_code == 409
        assert lab.get(f"{base}/inputs", headers=token).status_code == 409
        assert lab.get(base, headers=token).json()["run"]["status"] == status  # how the runner notices
    assert lab.post(f"{human}/{finished['id']}/cancel", json={}).status_code == 409


def test_finishing_checks_the_status_and_summary(lab: TestClient) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    finish = f"{RUNNER}/runs/{run['id']}/finish"
    token = _bearer(lab.tokens["lab-laptop"])
    for body in ({"status": "cancelled"}, {"status": "queued"}, {}, {"status": "done", "summary": "x" * 4001},
                 {"status": "done", "summary": 5}, {"status": "done", "summary": "bell\x07"}):
        assert lab.post(finish, json=body, headers=token).status_code == 422, body
    lease = "lease_" + "ab" * 16
    reply = lab.post(finish, json={"status": "failed", "summary": f"Lost {lease}.\nSee the log."}, headers=token)
    assert reply.status_code == 200
    assert reply.json()["run"]["summary"] == "Lost lease_….\nSee the log." and reply.json()["run"]["status"] == "failed"


# ---- events ------------------------------------------------------------------------------------------


def test_events_are_capped_redacted_and_shown_to_people(lab: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    url = f"{RUNNER}/runs/{run['id']}/events"
    token = _bearer(lab.tokens["lab-laptop"])
    token_text = lab.tokens["central-docker"]
    sent = lab.post(url, json={"events": [
        {"kind": "status", "text": "planning"},
        {"kind": "transcript", "text": f"used {token_text} and lease_{'0' * 32}"},
    ]}, headers=token)
    assert sent.status_code == 200 and sent.json() == {"status": "active"}

    shown = lab.get(f"/api/automation/projects/{project['id']}/runs/{run['id']}").json()["events"]
    assert [event["kind"] for event in shown] == ["status", "transcript"]
    assert shown[1]["text"] == "used lnr_… and lease_…" and shown[0]["at"]

    too_many = {"events": [{"kind": "status", "text": "x"}] * 101}
    assert lab.post(url, json=too_many, headers=token).status_code == 413
    for event in ({"kind": "Status", "text": "x"}, {"kind": "status"}, {"kind": "status", "text": 1},
                  {"kind": "status", "text": "x", "html": "<b>"}, {"kind": "status", "text": "é" * 9000}, "status"):
        assert lab.post(url, json={"events": [event]}, headers=token).status_code == 422, event
    assert lab.post(url, json={"events": "x"}, headers=token).status_code == 422
    assert lab.post(url, content=b'{"events": []}', headers={**token, "content-type": "text/plain"}).status_code == 415
    assert lab.post(url, content=b'{"events": []}' + b" " * 300_000,
                    headers={**token, "content-type": "application/json"}).status_code == 413

    monkeypatch.setattr(automation_runs, "MAX_EVENTS_FILE_BYTES", 2000)
    assert lab.post(url, json={"events": [{"kind": "log", "text": "y" * 3000}]}, headers=token).status_code == 413
    assert len(lab.get(f"/api/automation/projects/{project['id']}/runs/{run['id']}").json()["events"]) == 2


def test_people_see_the_last_events(lab: TestClient) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    url = f"{RUNNER}/runs/{run['id']}/events"
    token = _bearer(lab.tokens["lab-laptop"])
    for batch in range(6):
        events = [{"kind": "log", "text": f"{batch}-{index}"} for index in range(100)]
        assert lab.post(url, json={"events": events}, headers=token).status_code == 200
    shown = lab.get(f"/api/automation/projects/{project['id']}/runs/{run['id']}").json()["events"]
    assert len(shown) == 500 and shown[0]["text"] == "1-0" and shown[-1]["text"] == "5-99"


# ---- inputs and the library ---------------------------------------------------------------------------


def test_inputs_are_what_people_gave_the_project(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab, library_collections=["GRFP_2027"])
    experiment = lab.post("/api/experiments", json=EXPERIMENT).json()["experiment"]
    api = f"/api/automation/projects/{project['id']}"
    assert lab.post(f"{api}/experiments", json={"experiment_id": experiment["id"]}).status_code == 200
    lab.put(f"{api}/files/Literature/paper.pdf", content=b"%PDF-1.4 project paper")
    lab.put(f"{api}/files/Data/example_data/trace.csv", content=b"t,v\n0,1\n")
    folder = _folder(tmp_path, project)
    # Things a person could only place by hand: none of them reaches a runner.
    (folder / "Tasks" / ".secret.md").write_text("hidden", encoding="utf-8")
    (folder / "Literature" / "CLAUDE.md").write_text("instructions", encoding="utf-8")
    (folder / ".claude").mkdir()
    (folder / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (folder / "notes.md").write_text("outside every folder", encoding="utf-8")
    (folder / "Data" / "example_data" / "original.csv").write_text("a,b\n", encoding="utf-8")
    os.link(folder / "Data" / "example_data" / "original.csv", folder / "Data" / "example_data" / "linked.csv")

    _queue(lab, project)
    run = _claim(lab)
    assert _put(lab, run, "Analysis/Analysis.md", content=b"# earlier analysis").status_code == 200
    reply = lab.get(f"{RUNNER}/runs/{run['id']}/inputs", headers=_bearer(lab.tokens["lab-laptop"]))
    assert reply.status_code == 200 and reply.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(reply.content))
    names = set(archive.namelist())
    assert {
        "run.json", "Tasks/Task1.md", "Literature/paper.pdf", "Data/example_data/trace.csv",
        "Experiment_layout/LAYOUT.md", "Experiment_layout/DEVICES.md", f"Experiment_layout/{experiment['id']}.json",
    } <= names
    assert archive.read("Literature/paper.pdf") == b"%PDF-1.4 project paper"
    for name in names:
        top = name.split("/", 1)[0]
        assert top in ("run.json", "Tasks", "Literature", "Data", "Experiment_layout"), name
        assert not name.startswith("Data/new_data") and not any(part.startswith(".") for part in name.split("/"))
        assert "claude" not in name.casefold() and "project.json" not in name
    assert "Data/example_data/linked.csv" not in names and "Data/example_data/original.csv" not in names  # hard links

    manifest = json.loads(archive.read("run.json"))
    assert manifest["run"]["id"] == run["id"] and manifest["run"]["status"] == "active"
    assert manifest["project"]["id"] == project["id"] and manifest["project"]["library_collections"] == ["GRFP_2027"]
    assert manifest["project"]["linked_experiments"][0]["id"] == experiment["id"]
    assert manifest["library"] == [SPIN]


def test_runners_read_only_the_projects_part_of_the_library(lab: TestClient) -> None:
    narrowed = _project(lab, name="Narrow", library_collections=["GRFP_2027"])
    everything = _project(lab, name="Everything")
    _queue(lab, narrowed)
    _queue(lab, everything)
    narrow_run, wide_run = _claim(lab), _claim(lab)
    token = _bearer(lab.tokens["lab-laptop"])

    def fetch(run: dict, path: str):
        return lab.get(f"{RUNNER}/runs/{run['id']}/library/{path}", headers=token)

    paper = fetch(narrow_run, SPIN)
    assert paper.status_code == 200 and paper.content == b"%PDF-1.4 spin"
    assert fetch(narrow_run, PDH).status_code == 404
    assert fetch(wide_run, PDH).status_code == 200
    for path in ("_duplicates/copy.pdf", "collections/GRFP_2027.txt", "REORGANIZATION.csv", "%2E%2E/automation"):
        assert fetch(wide_run, path).status_code == 404, path
    store = lab.app.state.automation
    assert store.library_scope(store.get(everything["id"])) == [PDH, SPIN]
    folders = {**store.get(narrowed["id"]), "library_folders": ["lasers_and_stabilization"], "library_collections": []}
    assert store.library_scope(folders) == [PDH]


def test_a_chunked_file_is_held_to_the_file_size_too(lab: TestClient, tmp_path: Path) -> None:
    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)

    def chunks():
        for _ in range(4):
            yield b"x" * 3000  # 12 KB without Content-Length: over the ~10 KB file size, under the quota

    assert _put(lab, run, "Data/new_data/big.csv", content=chunks()).status_code == 413
    folder = _folder(tmp_path, project)
    assert not (folder / "Data" / "new_data" / "big.csv").exists() and not list(folder.rglob(".upload-*"))
    assert _put(lab, run, "Data/new_data/ok.csv", content=b"y" * 9000).json()["bytes_uploaded"] == 9000


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_runner_uploads_never_follow_a_junction(lab: TestClient, tmp_path: Path) -> None:
    import _winapi

    project = _project(lab)
    _queue(lab, project)
    run = _claim(lab)
    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(_folder(tmp_path, project) / "Analysis" / "outputs" / "link"))
    assert _put(lab, run, "Analysis/outputs/link/planted.md").status_code in (404, 422)
    assert _put(lab, run, "Analysis/outputs/link/sub/planted.md").status_code in (404, 422)
    assert _put(lab, run, "Analysis/outputs/link").status_code in (404, 409, 422)
    assert list(outside.iterdir()) == []
