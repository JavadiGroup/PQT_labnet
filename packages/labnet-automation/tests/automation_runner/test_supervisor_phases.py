"""The supervisor's Phase 3 loop: submit → awaiting approval → approved (token, relay, resumed session) → the
grant ended → the analysis; rejection with a note; the review cap; cancel/stop while waiting; the grant ended
on every way out."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from labnet_automation.client import RunnerApiError
from labnet_automation.protocol_text import protocol_sha256
from labnet_automation.supervisor import MAX_REVIEWS, Supervisor

from .supervisor_fakes import (
    APPROVED, GRANT, GRANT_TOKEN, PROTOCOL, RUN_ID, SHA, FakeBackend, FakeRelay, PhaseClient, ToolsFactory,
    approved, awaiting, passing_selfcheck, rejected, run_record, runner_settings,
)


def _supervisor(tmp_path: Path, client, backend, *, submissions=((SHA, PROTOCOL),), **settings):
    tools = ToolsFactory(list(submissions))
    FakeRelay.made.clear()
    runner = Supervisor(runner_settings(tmp_path, **settings), client, backend, tools_factory=tools,
                        relay_factory=FakeRelay, selfcheck=passing_selfcheck, tick_seconds=0.01, flush_seconds=0.05)
    return runner, tools


def _flag(argv, name: str) -> str:
    return argv[argv.index(name) + 1]


def test_the_approval_cycle_resumes_the_same_session_with_the_bounds_then_ends_the_grant(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), awaiting(), awaiting(), approved()])
    backend = FakeBackend(tmp_path, ["submit", "measure", "work"])
    runner, tools = _supervisor(tmp_path, client, backend, heartbeat_seconds=0.01)
    devices_seen = []
    backend.on_start = lambda _spec: devices_seen.append(tools.made[0].kwargs["experiment"].devices_dir())

    assert runner.run_once(run_record()) == "done"

    # Three sessions of one conversation: started, resumed after approval, resumed for the analysis.
    first, second, third = (list(spec.argv) for spec in backend.specs)
    session = _flag(first, "--session-id")
    assert "--resume" not in first
    for resumed in (second, third):
        assert _flag(resumed, "--resume") == session and "--session-id" not in resumed
        assert resumed[:resumed.index("--max-budget-usd")] == first[:first.index("--max-budget-usd")]
    assert _flag(first, "--max-budget-usd") == "5"
    assert _flag(second, "--max-budget-usd") == "4.5" and _flag(third, "--max-budget-usd") == "4"  # what is left
    assert backend.started(2)["argv"] == second[1:]

    approval = backend.specs[1].stdin
    assert "Your protocol was APPROVED" in approval and "Approved by alice" in approval and SHA in approval
    assert GRANT["ends_at"] in approval
    assert "`dev01-aom` (dummy_aom)" in approval
    assert "set_frequency(value: 70 … 90, each change ≤ 2): at most 200 calls, ≤ 5 calls/s" in approval
    assert "dev01-aom.set_rf_enabled(enabled=false)" in approval and "end_experiment" in approval
    assert "The experiment is over" in backend.specs[2].stdin
    assert backend.started(2)["prompt"] == approval

    # The grant: its token taken once, the relay up only for the experiment's session, ended once.
    assert client.tokens_taken == 1
    assert client.ended == ["The agent's session ended."]
    [relay] = FakeRelay.made
    root = tmp_path / "runner" / "runs" / RUN_ID
    assert relay.started and relay.stopped and relay.socket_path == root / "devices" / "gateway.sock"
    assert relay.client is client and relay.run_id == RUN_ID
    experiment = tools.made[0].kwargs["experiment"]
    assert relay.experiment is experiment and experiment.closed and experiment.token() is None
    assert devices_seen == [None, root / "devices", None]  # run containers get devices only while executing
    assert experiment.protocol == APPROVED and experiment.grant["status"] == "ended"

    # Waiting: heartbeats and polls; the person sees what happens.
    assert client.heartbeats and client.run_reads == 4
    log = client.events_sent("log")
    assert any("awaits a person's approval" in text for text in log)
    assert any("approved by alice" in text and GRANT["ends_at"] in text for text in log)
    assert any(text.startswith("The grant ended (ended;") for text in log)
    assert any("missing: resuming the agent once more" in text for text in log)

    # One transcript for the three sessions; the results of all of them uploaded; the run done.
    transcript = client.uploads[f"ScriptsUtilized/logs/{RUN_ID}/transcript.jsonl"].decode().splitlines()
    assert sum(json.loads(line).get("subtype") == "init" for line in transcript) == 3
    assert {"Data/new_data/scan.csv", "Analysis/Analysis.md", "Protocols/Task1.protocol.json",
            "ScriptsUtilized/src/scan.py"} <= set(client.uploads)
    assert GRANT_TOKEN not in client.uploads[f"ScriptsUtilized/logs/{RUN_ID}/runner.log"].decode()
    assert GRANT_TOKEN not in json.dumps(client.sent)
    assert client.finished == [("done", "The fit is in Analysis/Analysis.md.")]


def test_no_analysis_resume_when_the_analysis_exists_and_no_second_end_after_end_experiment(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), approved()])
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, tools = _supervisor(tmp_path, client, backend)

    def on_start(_spec) -> None:
        if len(backend.specs) == 2:  # the agent calls end_experiment: the tools server ends the grant
            tools.made[0].kwargs["experiment"].end("The agent ended the experiment: done", confirmed=True)

    backend.on_start = on_start
    assert runner.run_once(run_record()) == "done"
    assert len(backend.specs) == 2 and client.ended == []  # already ended at the Control Center
    assert FakeRelay.made[0].stopped


def test_a_rejection_resumes_the_agent_with_the_reviewers_note(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), rejected(1, "Too fast: at most 1 call/s.\nAlso keep RF off between steps.")])
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert len(backend.specs) == 2  # the agent didn't submit again: the same rejection isn't answered twice
    resumed = list(backend.specs[1].argv)
    assert _flag(resumed, "--resume") == _flag(list(backend.specs[0].argv), "--session-id")
    note = backend.specs[1].stdin
    assert "Your protocol was REJECTED" in note and "Reviewed by bob" in note
    assert "> Too fast: at most 1 call/s.\n> Also keep RF off between steps." in note
    assert "2 submission(s) left" in note
    assert client.tokens_taken == 0 and client.ended == [] and FakeRelay.made == []
    assert any("rejected by bob" in text for text in client.events_sent("log"))


def test_at_most_three_reviews_are_answered(tmp_path: Path) -> None:
    phases = []
    for n in range(1, 5):  # a Control Center that (wrongly) allowed a fourth submission
        phases += [awaiting(n), rejected(n, f"note {n}")]
    client = PhaseClient(phases)
    backend = FakeBackend(tmp_path, "submit")
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert len(backend.specs) == 1 + MAX_REVIEWS == 4
    for number, spec in enumerate(backend.specs[1:], 1):
        assert f"> note {number}" in spec.stdin
    assert "1 submission(s) left" in backend.specs[2].stdin
    assert "no submissions left" in backend.specs[3].stdin
    assert any("not resuming (at most 3 reviews)" in text for text in client.events_sent("log"))
    assert client.tokens_taken == 0


@pytest.mark.parametrize("how", ["cancelled", "failed", "409"])
def test_a_run_cancelled_or_timed_out_while_awaiting_approval_stops(tmp_path: Path, how: str) -> None:
    client = PhaseClient([awaiting()])
    backend = FakeBackend(tmp_path, "submit")
    runner, _tools = _supervisor(tmp_path, client, backend)

    def on_read(count: int) -> None:
        if count >= 3:
            if how == "409":
                raise RunnerApiError(409, "This run is cancelled.", run_status="cancelled")
            client.status = how  # "failed": the approval timed out (or the run went stale)

    client.on_read = on_read
    assert runner.run_once(run_record()) == "cancelled"
    assert len(backend.specs) == 1 and client.finished == [] and client.tokens_taken == 0 and client.ended == []


def test_shutting_down_while_awaiting_approval_finishes_the_run_stopped(tmp_path: Path) -> None:
    client = PhaseClient([awaiting()])
    backend = FakeBackend(tmp_path, "submit")
    runner, _tools = _supervisor(tmp_path, client, backend)
    client.on_read = lambda count: runner.request_stop() if count >= 2 else None

    assert runner.run_once(run_record()) == "stopped"
    [(status, summary)] = client.finished
    assert status == "stopped" and "while the protocol awaited approval" in summary


def test_stop_during_the_experiment_ends_the_grant_and_the_relay(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), approved()])
    backend = FakeBackend(tmp_path, ["submit", "slow"])
    runner, _tools = _supervisor(tmp_path, client, backend)

    def on_start(_spec) -> None:
        if len(backend.specs) == 2:
            threading.Timer(0.3, lambda: setattr(client, "status", "cancelled")).start()

    backend.on_start = on_start
    began = time.monotonic()
    assert runner.run_once(run_record()) == "cancelled"
    assert time.monotonic() - began < 20
    assert len(client.ended) == 1 and client.ended[0].startswith("The run is ending")
    assert FakeRelay.made[0].stopped and client.finished == []


def test_an_error_while_executing_still_ends_the_grant(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), approved()])
    backend = FakeBackend(tmp_path, ["submit", "measure"])
    runner, _tools = _supervisor(tmp_path, client, backend)
    original = backend.start_agent

    def start_agent(spec):
        if not backend.specs:
            return original(spec)
        raise RuntimeError("docker died")

    backend.start_agent = start_agent
    assert runner.run_once(run_record()) == "failed"
    assert client.ended == ["The run ended."] and FakeRelay.made[0].stopped
    assert client.finished[0][0] == "failed" and "docker died" in client.finished[0][1]


def test_a_grant_whose_token_cant_be_taken_is_ended(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), approved()])
    client.token_error = RunnerApiError(409, "The grant's token was already taken.")
    backend = FakeBackend(tmp_path, "submit")
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert len(backend.specs) == 1 and FakeRelay.made == []
    assert client.ended == ["The runner couldn't take the grant's token."]


def test_a_failing_grant_end_is_logged_and_the_run_still_finishes(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), approved()])
    client.end_error = RunnerApiError(503, "busy")
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert len(client.ended) == 2  # after the session, and once more on the way out
    assert any("Ending the grant failed" in text for text in client.events_sent("log"))


def test_a_protocol_that_doesnt_match_the_approved_sha_tells_the_agent_the_devices_only(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(sha256="b" * 64), approved(sha256="b" * 64)])
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, _tools = _supervisor(tmp_path, client, backend)  # the protocol sent with the token hashes to SHA

    assert runner.run_once(run_record()) == "done"
    assert "`dev01-aom`" in backend.specs[1].stdin and "set_frequency(" not in backend.specs[1].stdin
    assert any("doesn't match the approved sha256" in text for text in client.events_sent("log"))


def test_without_an_approved_protocol_the_agent_is_told_the_devices_only(tmp_path: Path) -> None:
    client = PhaseClient([awaiting(), approved()])
    client.approved = None  # an older Control Center
    backend = FakeBackend(tmp_path, ["submit", "work"])
    runner, tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert "`dev01-aom`" in backend.specs[1].stdin and "set_frequency(" not in backend.specs[1].stdin
    assert "weren't received" in backend.specs[1].stdin
    assert tools.made[0].kwargs["experiment"].protocol is None


def test_the_agent_is_told_the_approved_bounds_not_its_own_file(tmp_path: Path) -> None:
    # The agent's file said 70 … 90 MHz in steps of 2; what was approved (and is enforced) is narrower.
    narrower = json.loads(json.dumps(APPROVED))
    narrower["devices"][0]["methods"]["set_frequency"]["args"]["value"] = {"min": 75.0, "max": 85.0, "max_step": 1.0}
    narrower["safe_state"] = []
    sha = protocol_sha256(narrower)
    client = PhaseClient([awaiting(sha256=sha), approved(sha256=sha)])
    client.approved = narrower
    backend = FakeBackend(tmp_path, ["submit", "measure", "work"])
    runner, tools = _supervisor(tmp_path, client, backend, submissions=((sha, PROTOCOL),))
    statuses = []
    backend.on_start = lambda _spec: statuses.append(tools.made[0].kwargs["experiment"].summary())

    assert runner.run_once(run_record()) == "done"
    approval = backend.specs[1].stdin
    assert "set_frequency(value: 75 … 85, each change ≤ 1)" in approval
    assert "70 … 90" not in approval and "dev01-aom.set_rf_enabled(enabled=false)" not in approval
    assert "must be passed in every call" in approval and "current reading" in approval
    # grant_status during the experiment shows the approved bounds too.
    during = statuses[1]
    assert during["active"] and during["devices"]["dev01-aom"]["methods"]["set_frequency"]["args"]["value"] == {
        "min": 75.0, "max": 85.0, "max_step": 1.0}
    assert during["safe_state"] == []


def test_no_waiting_when_the_budget_is_spent(tmp_path: Path) -> None:
    client = PhaseClient([awaiting()])
    backend = FakeBackend(tmp_path, "submit", cost=4.99)
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"
    assert len(backend.specs) == 1 and client.run_reads == 1 and client.tokens_taken == 0
    assert any("budget is spent" in text for text in client.events_sent("log"))


def test_a_run_that_never_submits_finishes_after_one_session(tmp_path: Path) -> None:
    client = PhaseClient([{"phase": "planning"}])
    backend = FakeBackend(tmp_path, "work")
    runner, _tools = _supervisor(tmp_path, client, backend, submissions=())

    assert runner.run_once(run_record()) == "done"
    assert len(backend.specs) == 1 and client.run_reads == 1 and client.ended == []


def test_an_approved_experiment_whose_grant_ended_before_it_started_fails_not_done(tmp_path: Path) -> None:
    """Seen live: the Control Center died after the approval, the watchdog applied the safe state, and
    when it came back the run was 'analyzing' with no experiment run. That must not finish as done."""

    ended_early = {**approved(), "phase": "analyzing",
                   "grant": {**GRANT, "status": "ended", "calls": 0,
                             "ended_reason": "applied by the watchdog: the Control Center stopped answering"}}
    client = PhaseClient([awaiting(), ended_early])
    backend = FakeBackend(tmp_path, "submit")
    runner, _tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "failed"
    [(status, summary)] = client.finished
    assert status == "failed" and "never started" in summary and "watchdog" in summary
    assert client.tokens_taken == 0 and len(backend.specs) == 1  # never resumed for an experiment
