"""Edit and approve, as the supervisor sees it: the approved protocol is the approver's edit (its own sha256,
``approved_sha256``), and the resumed agent is told it was edited and what changed."""

from __future__ import annotations

import json
from pathlib import Path

from labnet_automation.protocol_text import protocol_sha256

from .supervisor_fakes import APPROVED, FakeBackend, PhaseClient, approved, awaiting, run_record
from .test_supervisor_phases import _supervisor


def test_the_agent_is_told_its_protocol_was_edited_and_gets_the_edited_bounds(tmp_path: Path) -> None:
    edited = json.loads(json.dumps(APPROVED))
    edited["devices"][0]["methods"]["set_frequency"]["args"]["value"] = {"min": 70.0, "max": 85.0, "max_step": 1.0}
    edited_sha = protocol_sha256(edited)
    changes = ["dev01-aom.set_frequency.value.max: 90 → 85", "dev01-aom.set_frequency.value.max_step: 2 → 1"]
    decision = approved()  # the submission's sha256 is the agent's (SHA); the approved one is the edit's
    decision["protocol"].update(approved_sha256=edited_sha, edited_by="alice", changes=changes)
    client = PhaseClient([awaiting(), decision])
    client.approved = edited
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    prompt = backend.specs[1].stdin
    assert "APPROVED WITH EDITS" in prompt and "alice EDITED your protocol" in prompt
    assert "- dev01-aom.set_frequency.value.max: 90 → 85" in prompt and edited_sha in prompt
    assert "set_frequency(value: 70 … 85, each change ≤ 1)" in prompt and "70 … 90" not in prompt
    assert not any("doesn't match the approved sha256" in text for text in client.events_sent("log"))


def test_an_edit_whose_protocol_doesnt_match_the_approved_sha_is_not_quoted(tmp_path: Path) -> None:
    decision = approved()
    decision["protocol"].update(approved_sha256="c" * 64, edited_by="alice", changes=["duration_minutes: 20 → 10"])
    client = PhaseClient([awaiting(), decision])  # the protocol sent with the token is APPROVED, not the edit
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert "set_frequency(" not in backend.specs[1].stdin
    assert any("doesn't match the approved sha256" in text for text in client.events_sent("log"))
