"""Edit and approve (an approver's own version of a protocol) and per-run settings ("Run with…")."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from labnet.automation_runner import agent_config
from labnet.control_center.automation_protocol import build_review, canonical, protocol_sha256
from labnet.control_center.automation_runs import MAX_CHANGES, RunProtocol, protocol_changes

from .test_automation import library  # noqa: F401  (a fixture)
from .test_automation_protocol import (
    FakeGrants,
    _check,
    _good,
    _prepare,
    _client,
    _run,
    _sha,
    _submit,
    _upload,
    desk,  # noqa: F401  (a fixture)
)
from .test_automation_runner import RUNNER, _bearer, _claim, _project, _queue, lab  # noqa: F401  (lab too)


class TokenGrants(FakeGrants):
    """FakeGrants that also hands out the token, with the protocol create() got (as the GrantManager does)."""

    def take_token(self, run_id: str) -> tuple[str, dict[str, Any]]:
        created = next(item for item in reversed(self.created) if item["run_id"] == run_id)
        grant = {"id": "grant-0123456789abcdef", "status": "active", "approved_by": created["approved_by"]}
        return "lng_" + "T" * 43, grant

    def protocol_of(self, grant_id: str) -> dict[str, Any]:
        assert grant_id == "grant-0123456789abcdef"
        return copy.deepcopy(self.created[-1]["protocol"])


def _waiting(client: TestClient) -> str:
    """Submit the contract example; the submission's sha256."""

    _upload(client)
    assert _submit(client).status_code == 200
    return _sha(client)


def _submitted() -> dict[str, Any]:
    return _check(_good())[0]


def _narrowed() -> dict[str, Any]:
    edit = _submitted()
    methods = edit["devices"][0]["methods"]
    methods["set_frequency"]["args"]["value"].update(max=85, max_step=1)
    methods["set_frequency"]["max_calls"] = 100
    del methods["read_diffraction_efficiency"]
    methods["stream_diffraction_efficiency"]["stream"]["max_samples"] = 500
    edit["duration_minutes"] = 15
    return edit


NARROWED_CHANGES = [
    "duration_minutes: 20 → 15",
    "dev01-aom.read_diffraction_efficiency: removed (may not be called)",
    "dev01-aom.set_frequency.value.max: 90 → 85",
    "dev01-aom.set_frequency.value.max_step: 2 → 1",
    "dev01-aom.set_frequency.max_calls: 200 → 100",
    "dev01-aom.stream_diffraction_efficiency.stream.max_samples: 1000 → 500",
]


def _preview(client: TestClient, protocol: Any, sha256: str | None = None):
    return client.post(f"{client.api}/protocol/preview", json={"sha256": sha256 or _sha(client), "protocol": protocol})


def _approve_edit(client: TestClient, protocol: Any, *, sha256: str | None = None, minutes: float = 15, **extra):
    body = {"sha256": sha256 or _sha(client), "duration_minutes": minutes, "approved_by": "Rob", "protocol": protocol,
            **extra}
    return client.post(f"{client.api}/protocol/approve", json=body)


def _state(tmp_path: Path, client: TestClient) -> Path:
    return tmp_path / "automation" / "state" / client.project["id"]


# ---- the list of changes ------------------------------------------------------------------------


def test_the_changes_name_each_edit_in_plain_words() -> None:
    before = _submitted()
    assert protocol_changes(before, copy.deepcopy(before)) == []
    assert protocol_changes(before, _check(_narrowed())[0]) == NARROWED_CHANGES

    edit = _submitted()
    methods = edit["devices"][0]["methods"]
    methods["set_frequency"]["args"]["value"]["max_step"] = None  # any step
    methods["stream_diffraction_efficiency"]["args"]["channel"]["choices"] = ["CH1", "CH2"]
    methods["set_rf_power"] = {"args": {"value": {"min": 0, "max": 0.5, "max_step": None}}, "max_calls": 5,
                               "max_rate_hz": 1}
    edit["safe_state"] = [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": True}}]
    assert protocol_changes(before, edit) == [
        "dev01-aom.set_frequency.value.max_step: 2 → none",
        "dev01-aom.set_rf_power: added (at most 5 calls, 1/s; value 0 to 0.5)",
        'dev01-aom.stream_diffraction_efficiency.channel.choices: ["CH1"] → ["CH1", "CH2"]',
        "safe_state[0] dev01-aom.set_rf_enabled.enabled: false → true",
    ]

    calls = _submitted()
    calls["safe_state"] = [{"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": 70.0}},
                           {"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}]
    assert protocol_changes(before, calls) == ["safe_state: added dev01-aom.set_frequency(value=70)"]
    reordered = copy.deepcopy(calls)
    reordered["safe_state"].reverse()
    assert protocol_changes(calls, reordered) == [
        "safe_state: reordered, now dev01-aom.set_rf_enabled(enabled=false); dev01-aom.set_frequency(value=70)"]
    emptied = _submitted()
    emptied["safe_state"] = []
    assert protocol_changes(before, emptied) == ["safe_state: removed dev01-aom.set_rf_enabled(enabled=false)"]

    two = _submitted()
    two["devices"].append({"device_id": "dev02-awg", "device_type": "dummy_awg", "methods": {
        "read_amplitude": {"args": {"channel": {"choices": ["CH1"]}}, "max_calls": 1, "max_rate_hz": 1.0}}})
    assert protocol_changes(before, two) == ["dev02-awg: added, with read_amplitude"]
    assert protocol_changes(two, before) == ["dev02-awg: removed (no calls to it at all)"]
    # Numbers keep their precision; booleans aren't numbers.
    precise = _submitted()
    precise["devices"][0]["methods"]["set_frequency"]["args"]["value"]["min"] = 70.125
    assert protocol_changes(before, precise) == ["dev01-aom.set_frequency.value.min: 70 → 70.125"]


def test_the_run_record_keeps_a_bounded_list_of_changes() -> None:
    many = [f"dev01-aom.m{index}: removed (may not be called)" for index in range(MAX_CHANGES + 5)] + ["x" * 500]
    from labnet.control_center.automation_runs import _clip_changes

    clipped = _clip_changes(many)
    assert len(clipped) == MAX_CHANGES + 1 and clipped[-1] == "… and 6 more changes"
    assert _clip_changes(["y" * 500])[0].endswith("…") and len(_clip_changes(["y" * 500])[0]) == 240
    record = RunProtocol(submissions=1, status="approved", sha256="a" * 64, duration_minutes=5,
                         submitted_at="2026-10-08T00:00:00Z", approved_sha256="b" * 64, edited_by="Rob", changes=clipped)
    assert record.changes == clipped


# ---- preview -----------------------------------------------------------------------------------


def test_preview_checks_an_edit_and_lists_the_changes_without_approving(desk: TestClient) -> None:  # noqa: F811
    sha = _waiting(desk)
    reply = _preview(desk, _narrowed(), sha)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    expected = _check(_narrowed())[0]
    assert body["problems"] == [] and body["normalized"] == expected
    assert body["sha256"] == protocol_sha256(expected) != sha
    assert body["changes"] == NARROWED_CHANGES
    assert body["review"] == build_review(expected, {"dev01-aom": {"type": "dummy_aom", "state": "available",
                                                                    "hints": {"limits": {"set_frequency.value": [60.0, 100.0],
                                                                                         "set_rf_power.value": [0.0, 2.0]}}}})
    # Nothing was approved.
    run = _run(desk)
    assert run["phase"] == "awaiting_approval" and run["protocol"]["status"] == "submitted" and desk.fake.created == []

    # An edit that doesn't pass: the problems, with paths, and nothing normalized.
    wide = _submitted()
    wide["devices"][0]["methods"]["set_frequency"]["args"]["value"]["max"] = 150  # the driver allows up to 100
    refused = _preview(desk, wide, sha).json()
    assert refused["normalized"] is None and refused["sha256"] is None and refused["changes"] == []
    assert [problem["path"] for problem in refused["problems"]] == ["devices[0].methods.set_frequency.args.value.max"]

    # A stale base, a missing or malformed protocol, a cross-site request.
    stale = _preview(desk, _narrowed(), "0" * 64)
    assert stale.status_code == 409 and stale.json()["sha256"] == sha
    assert desk.post(f"{desk.api}/protocol/preview", json={"sha256": sha}).status_code == 422
    assert desk.post(f"{desk.api}/protocol/preview", json={"sha256": sha, "protocol": [1]}).status_code == 422
    assert desk.post(f"{desk.api}/protocol/preview", json={"protocol": _narrowed()}).status_code == 422
    assert desk.post(f"{desk.api}/protocol/preview", json={"sha256": sha, "protocol": _narrowed()},
                     headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_preview_needs_a_network_central_confirms(desk: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    sha = _waiting(desk)

    async def down() -> dict:
        raise ConnectionError("Central is down")

    monkeypatch.setattr(desk.app.state.status, "snapshot", down)
    assert _preview(desk, _narrowed(), sha).status_code == 503


# ---- approving an edit --------------------------------------------------------------------------


def test_an_edited_protocol_is_approved_stored_and_given_to_the_grant(desk: TestClient, tmp_path: Path) -> None:  # noqa: F811
    desk.app.state.grants = desk.fake = TokenGrants(desk.app.state.runs)
    sha = _waiting(desk)
    preview = _preview(desk, _narrowed(), sha).json()
    edited_sha = preview["sha256"]

    reply = _approve_edit(desk, preview["normalized"], sha256=sha, minutes=12, edited_sha256=edited_sha)
    assert reply.status_code == 200, reply.text
    run = reply.json()["run"]
    assert run["phase"] == "executing"
    record = run["protocol"]
    assert record["status"] == "approved" and record["sha256"] == sha  # the agent's submission, as before
    assert record["approved_sha256"] == edited_sha and record["edited_by"] == "Rob"
    assert record["changes"] == NARROWED_CHANGES and record["approved_minutes"] == 12.0

    # The grant enforces the edited version, under its own sha256.
    [created] = desk.fake.created
    assert created["protocol"] == preview["normalized"] == _check(_narrowed())[0]
    assert created["sha256"] == edited_sha and created["duration_minutes"] == 12.0 and created["approved_by"] == "Rob"

    # Stored as its own normalized file, whose sha256 is the approved one; the submission is untouched.
    folder = _state(tmp_path, desk) / "protocols"
    edited_file = folder / f"{desk.run['id']}-1-edited.json"
    assert edited_file.read_text(encoding="utf-8") == canonical(preview["normalized"])
    assert hashlib.sha256(edited_file.read_bytes()).hexdigest() == edited_sha
    assert hashlib.sha256((folder / f"{desk.run['id']}-1.json").read_bytes()).hexdigest() == sha

    # People see both versions and the changes.
    shown = desk.get(f"{desk.api}/protocol").json()
    assert shown["sha256"] == sha and shown["protocol"] == _submitted()
    assert shown["approved_sha256"] == edited_sha and shown["approved_protocol"] == preview["normalized"]
    assert shown["edited_by"] == "Rob" and shown["changes"] == NARROWED_CHANGES
    assert shown["approved_review"]["duration_minutes"] == 15.0
    assert [method["name"] for method in shown["approved_review"]["devices"][0]["methods"]] == [
        "set_frequency", "set_rf_enabled", "stream_diffraction_efficiency"]
    events = desk.get(desk.api).json()["events"]
    assert any(event["kind"] == "approval" and "EDITED (6 changes" in event["text"]
               and "dev01-aom.set_frequency.value.max: 90 → 85" in event["text"] for event in events)

    # The runner sees the approved sha256 and the changes, and gets the edited protocol with the token.
    seen = desk.get(desk.runner, headers=desk.token).json()["run"]["protocol"]
    assert seen["approved_sha256"] == edited_sha and seen["changes"] == NARROWED_CHANGES and seen["edited_by"] == "Rob"
    token = desk.post(f"{desk.runner}/grant/token", json={}, headers=desk.token)
    assert token.status_code == 200, token.text
    assert token.json()["protocol"] == preview["normalized"]
    assert protocol_sha256(token.json()["protocol"]) == seen["approved_sha256"]

    # The agent is told it was edited, and what changed.
    prompt = agent_config.approval_prompt({"protocol": seen}, {"approved_by": "Rob"}, token.json()["protocol"])
    assert "APPROVED WITH EDITS" in prompt and "Rob EDITED your protocol" in prompt
    assert "- dev01-aom.set_frequency.value.max: 90 → 85" in prompt and edited_sha in prompt
    assert "set_frequency(value: 70 … 85, each change ≤ 1)" in prompt

    # A tampered edited file can't be shown.
    edited_file.write_text(edited_file.read_text(encoding="utf-8").replace('"max":85.0', '"max":95.0'), encoding="utf-8")
    assert desk.get(f"{desk.api}/protocol").status_code == 500


def test_an_approver_may_widen_within_the_limits_but_an_invalid_edit_is_refused(desk: TestClient) -> None:  # noqa: F811
    sha = _waiting(desk)
    wide = _submitted()
    wide["devices"][0]["methods"]["set_frequency"]["args"]["value"].update(min=60, max=100)  # the driver's limits
    wide["duration_minutes"] = 30  # longer than the agent asked for: the approver's call
    wide["devices"][0]["methods"]["stream_diffraction_efficiency"]["args"]["channel"]["choices"] = ["CH1", "CH2"]

    too_wide = copy.deepcopy(wide)
    too_wide["devices"][0]["methods"]["set_frequency"]["args"]["value"]["max"] = 101
    too_wide["devices"][0]["methods"]["set_rf_enabled"]["max_rate_hz"] = 0
    too_wide["duration_minutes"] = 500
    refused = _approve_edit(desk, too_wide, sha256=sha, minutes=10)
    assert refused.status_code == 422 and "3 problems" in refused.json()["detail"]
    assert sorted(problem["path"] for problem in refused.json()["problems"]) == [
        "devices[0].methods.set_frequency.args.value.max", "devices[0].methods.set_rf_enabled.max_rate_hz",
        "duration_minutes"]
    # A device or a safe state outside what the protocol allows is refused as the agent's would be.
    stranger = copy.deepcopy(wide)
    stranger["devices"].append({"device_id": "dev09-free", "device_type": "dummy_aom", "methods": {
        "read_diffraction_efficiency": {"max_calls": 1, "max_rate_hz": 1}}})
    stranger["safe_state"] += [{"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": 150}},
                               {"device_id": "dev01-aom", "method": "set_rf_power", "args": {"value": 0}}]
    problems = _approve_edit(desk, stranger, sha256=sha, minutes=10).json()["problems"]
    assert {problem["path"] for problem in problems} == {"devices[1].device_id", "safe_state[1].args.value",
                                                         "safe_state[2].method"}
    not_object = _approve_edit(desk, "everything", sha256=sha)
    assert not_object.status_code == 422
    assert desk.fake.created == [] and _run(desk)["phase"] == "awaiting_approval"

    # The grant may not be longer than the edited protocol asks for; then within it, it's approved.
    too_long = _approve_edit(desk, wide, sha256=sha, minutes=31)
    assert too_long.status_code == 422 and "30 minutes" in too_long.json()["detail"]
    approved = _approve_edit(desk, wide, sha256=sha, minutes=30)
    assert approved.status_code == 200, approved.text
    [created] = desk.fake.created
    assert created["duration_minutes"] == 30.0
    assert created["protocol"]["devices"][0]["methods"]["set_frequency"]["args"]["value"] == {
        "min": 60.0, "max": 100.0, "max_step": 2.0}
    assert "duration_minutes: 20 → 30" in approved.json()["run"]["protocol"]["changes"]


def test_an_edit_of_a_stale_submission_or_unlike_its_preview_is_refused(desk: TestClient) -> None:  # noqa: F811
    sha = _waiting(desk)
    # The base sha256 must still be the submission waiting for approval.
    stale = _approve_edit(desk, _narrowed(), sha256="0" * 64)
    assert stale.status_code == 409 and stale.json()["sha256"] == sha
    # The approval must be the version previewed.
    preview = _preview(desk, _narrowed(), sha).json()
    other = _narrowed()
    other["devices"][0]["methods"]["set_frequency"]["max_calls"] = 99
    mismatch = _approve_edit(desk, other, sha256=sha, edited_sha256=preview["sha256"])
    assert mismatch.status_code == 409 and "previewed" in mismatch.json()["detail"]
    assert _approve_edit(desk, other, sha256=sha, edited_sha256="nope").status_code == 422
    assert desk.fake.created == [] and _run(desk)["phase"] == "awaiting_approval"

    # Rejected and submitted again: an edit of the old submission is stale.
    assert desk.post(f"{desk.api}/protocol/reject", json={"note": "Slower please.", "sha256": sha}).status_code == 200
    second = _good()
    second["devices"][0]["methods"]["set_frequency"]["max_rate_hz"] = 2
    _upload(desk, second)
    assert _submit(desk).status_code == 200
    late = _approve_edit(desk, _narrowed(), sha256=sha)
    assert late.status_code == 409 and late.json()["sha256"] == _sha(desk) != sha
    assert _preview(desk, _narrowed(), sha).status_code == 409
    assert desk.fake.created == []


def test_an_edit_that_changes_nothing_is_a_plain_approval(desk: TestClient, tmp_path: Path) -> None:  # noqa: F811
    sha = _waiting(desk)
    reply = _approve_edit(desk, _good(), sha256=sha, minutes=20)  # as written, normalizes to the submission
    assert reply.status_code == 200, reply.text
    record = reply.json()["run"]["protocol"]
    assert record["approved_sha256"] == sha and record["edited_by"] is None and record["changes"] is None
    assert desk.fake.created[0]["sha256"] == sha
    assert not (_state(tmp_path, desk) / "protocols" / f"{desk.run['id']}-1-edited.json").exists()
    shown = desk.get(f"{desk.api}/protocol").json()
    assert "approved_protocol" not in shown and shown["approved_sha256"] == sha


def test_a_plain_approval_records_the_approved_sha256(desk: TestClient) -> None:  # noqa: F811
    sha = _waiting(desk)
    reply = desk.post(f"{desk.api}/protocol/approve", json={"sha256": sha, "duration_minutes": 20, "approved_by": "Rob"})
    assert reply.status_code == 200
    record = reply.json()["run"]["protocol"]
    assert record["approved_sha256"] == sha and record["changes"] is None


def test_safe_state_edits_are_checked_and_listed(desk: TestClient) -> None:  # noqa: F811
    sha = _waiting(desk)
    edit = _submitted()
    edit["safe_state"] = [
        {"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": 80}},
        {"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}},
    ]
    preview = _preview(desk, edit, sha).json()
    assert preview["changes"] == ["safe_state: added dev01-aom.set_frequency(value=80)"]
    # Removing the method a safe-state call uses, but not the call: refused, with the path.
    broken = _submitted()
    del broken["devices"][0]["methods"]["set_rf_enabled"]
    problems = _preview(desk, broken, sha).json()["problems"]
    assert [problem["path"] for problem in problems] == ["safe_state[0].method"]
    broken["safe_state"] = []
    fixed = _preview(desk, broken, sha).json()
    assert fixed["changes"] == ["dev01-aom.set_rf_enabled: removed (may not be called)",
                                "safe_state: removed dev01-aom.set_rf_enabled(enabled=false)"]


def test_with_logins_the_editor_is_the_signed_in_person(tmp_path: Path, library: Path) -> None:  # noqa: F811
    from .test_automation_runner import SECRET

    signed_in = {"X-LabNet-Proxy": SECRET, "X-LabNet-User": "alice"}
    with _client(tmp_path, library, proxy_secret=SECRET) as client:
        _prepare(client, signed_in)
        _upload(client)
        _submit(client)
        sha = client.get(f"{client.api}/protocol", headers=signed_in).json()["sha256"]
        reply = client.post(f"{client.api}/protocol/approve", headers=signed_in, json={
            "sha256": sha, "duration_minutes": 5, "approved_by": "mallory", "protocol": _narrowed()})
        assert reply.status_code == 200, reply.text
        record = reply.json()["run"]["protocol"]
        assert record["reviewed_by"] == "alice" and record["edited_by"] == "alice"


# ---- per-run settings ("Run with…") ----------------------------------------------------------------


def test_a_run_may_have_its_own_settings(lab: TestClient) -> None:  # noqa: F811
    project = _project(lab, run_model="opus", run_effort="low", run_max_turns=12, run_budget_usd=2.5)
    api = f"/api/automation/projects/{project['id']}"
    for name in ("Task2", "Task3", "Task4"):
        lab.put(f"{api}/files/Tasks/{name}.md", content=f"# {name}".encode())

    own = lab.post(f"{api}/runs", json={"task": "Tasks/Task1.md", "settings": {
        "model": "sonnet", "effort": "max", "max_turns": 150, "max_budget_usd": 20}})
    assert own.status_code == 201, own.text
    expected = {"model": "sonnet", "effort": "max", "max_turns": 150, "max_budget_usd": 20.0}
    assert own.json()["run"]["settings"] == expected
    # Only some: the rest are the project's; null keeps the project's too.
    some = lab.post(f"{api}/runs", json={"task": "Tasks/Task2.md", "settings": {"max_turns": 5, "effort": None}})
    assert some.json()["run"]["settings"] == {"model": "opus", "effort": "low", "max_turns": 5, "max_budget_usd": 2.5}
    # Haiku has no effort levels, as for a project.
    haiku = lab.post(f"{api}/runs", json={"task": "Tasks/Task3.md", "settings": {"model": "haiku", "effort": "high"}})
    assert haiku.json()["run"]["settings"]["effort"] is None and haiku.json()["run"]["settings"]["model"] == "haiku"
    # Plain Run: the project's.
    plain = lab.post(f"{api}/runs", json={"task": "Tasks/Task4.md", "settings": None})
    assert plain.json()["run"]["settings"] == {"model": "opus", "effort": "low", "max_turns": 12, "max_budget_usd": 2.5}

    # The project is untouched.
    again = lab.get(api).json()["project"]
    assert (again["run_model"], again["run_effort"], again["run_max_turns"], again["run_budget_usd"], again["version"]) == (
        "opus", "low", 12, 2.5, project["version"])
    # The runner gets the run's own settings.
    run = _claim(lab)
    assert run["id"] == own.json()["run"]["id"]
    reply = lab.get(f"{RUNNER}/runs/{run['id']}/inputs", headers=_bearer(lab.tokens["lab-laptop"]))
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(reply.content)).read("run.json"))
    assert manifest["settings"] == expected and manifest["run"]["settings"] == expected


def test_a_runs_own_settings_are_checked_like_the_projects(lab: TestClient) -> None:  # noqa: F811
    project = _project(lab)
    api = f"/api/automation/projects/{project['id']}/runs"
    for bad, field in (
        ({"model": "gpt-4"}, "settings.model"), ({"model": "claude-unknown-9"}, "settings.model"),
        ({"model": ""}, "settings.model"), ({"model": 5}, "settings.model"), ({"effort": "huge"}, "settings.effort"),
        ({"effort": "High"}, "settings.effort"), ({"max_turns": 0}, "settings.max_turns"),
        ({"max_turns": 201}, "settings.max_turns"), ({"max_turns": 5.5}, "settings.max_turns"),
        ({"max_turns": True}, "settings.max_turns"), ({"max_budget_usd": 0}, "settings.max_budget_usd"),
        ({"max_budget_usd": 100.01}, "settings.max_budget_usd"), ({"max_budget_usd": "5"}, "settings.max_budget_usd"),
        ({"temperature": 1}, "settings.temperature"),
    ):
        reply = lab.post(api, json={"task": "Tasks/Task1.md", "settings": bad})
        assert reply.status_code == 422, bad
        assert reply.json()["detail"].startswith(field), (bad, reply.json())
    assert lab.post(api, json={"task": "Tasks/Task1.md", "settings": "opus"}).status_code == 422
    assert lab.get(api).json()["runs"] == []  # nothing was queued
    assert _queue(lab, project)["settings"]["model"] == "sonnet"
