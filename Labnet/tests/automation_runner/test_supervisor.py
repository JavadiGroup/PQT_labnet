"""The supervisor: the claude argv/env/cwd contract, outputs only from agent folders, transcript, Stop,
refusals, heartbeats, time limit, shutdown, self-check, and a real end-to-end run against the Control Center."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from labnet.automation_runner import agent_config
from labnet.automation_runner.client import RunnerApiError
from labnet.automation_runner.supervisor import DEV_BANNER, Supervisor, main

from .supervisor_fakes import (
    RUN_ID, TOKEN, Check, Clock, FakeBackend, FakeClient, ScriptedAgent, ScriptedBackend, ToolsFactory,
    control_center, inputs_zip, passing_selfcheck, run_record, runner_client, runner_settings,
)


AGENT_OUTPUTS = {
    "Analysis/Analysis.md", "Analysis/outputs/fit.png", "Data/new_data/fit.csv", "Data/new_data/DATA_SUMMARY.md",
    "ScriptsUtilized/analysis_scripts/fit.py",
}
LOGS = {f"ScriptsUtilized/logs/{RUN_ID}/transcript.jsonl", f"ScriptsUtilized/logs/{RUN_ID}/runner.log"}


def _supervisor(tmp_path: Path, client, backend, **settings) -> tuple[Supervisor, ToolsFactory]:
    tools = ToolsFactory()
    return Supervisor(runner_settings(tmp_path, **settings), client, backend, tools_factory=tools,
                      selfcheck=passing_selfcheck, tick_seconds=0.02, flush_seconds=0.1), tools


# ---- one run, with a recording client --------------------------------------------------------


def test_a_run_starts_claude_exactly_as_agreed_and_uploads_only_the_agents_files(tmp_path: Path) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path)
    runner, tools = _supervisor(tmp_path, client, backend)

    assert runner.run_once(run_record()) == "done"

    # The command line.
    spec = backend.specs[0]
    argv = list(spec.argv)
    assert argv[:2] == ["claude", "-p"]
    flags = {argv[i]: argv[i + 1] for i in range(2, len(argv) - 1) if argv[i].startswith("--")}
    assert flags["--output-format"] == "stream-json" and "--verbose" in argv
    assert flags["--model"] == "sonnet" and flags["--effort"] == "high"
    assert flags["--max-turns"] == "60" and flags["--max-budget-usd"] == "5"
    assert flags["--tools"].split(",") == [
        "Read", "Glob", "Grep", "Edit", "Write", "Agent", "mcp__labnet__search_literature",
        "mcp__labnet__list_papers", "mcp__labnet__read_paper_pages", "mcp__labnet__run_python",
        "mcp__labnet__submit_protocol", "mcp__labnet__grant_status", "mcp__labnet__end_experiment",
    ]
    assert "Bash" not in flags["--tools"] and "WebFetch" not in flags["--tools"]
    assert flags["--settings"] == "/labnet/run-settings.json" and flags["--agents"] == "/labnet/agents.json"
    assert flags["--mcp-config"] == "/labnet/mcp.json" and "--strict-mcp-config" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""  # an empty argv element: no user/project settings
    assert flags["--permission-mode"] == "dontAsk"
    assert flags["--append-system-prompt-file"] == "/labnet/briefing.md"
    uuid.UUID(flags["--session-id"])
    assert "--resume" not in argv and "--dangerously-skip-permissions" not in argv

    # What the "container" saw: cwd, argv (the empty element survives), the prompt.
    started = backend.started()
    assert Path(started["cwd"]).resolve() == spec.work.resolve()
    assert started["argv"] == argv[1:]
    assert "AOM study" in started["prompt"] and "Tasks/Task1.md" in started["prompt"]
    assert "Fit the trace in Data/example_data/trace.csv." in started["prompt"]

    # The environment: only what the agent needs; nothing of the runner's (no token).
    assert dict(spec.env) == {
        "CLAUDE_CONFIG_DIR": "/claude", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "HTTPS_PROXY": "http://labnet-automation-egress:3128", "HTTP_PROXY": "http://labnet-automation-egress:3128",
        "NO_PROXY": "", "MCP_TOOL_TIMEOUT": "720000",  # (600 s run_python limit + 120 s) in ms
    }
    assert not any(TOKEN in value or "lnr_" in value for value in spec.env.values())

    # The folders and config files.
    root = tmp_path / "runner" / "runs" / RUN_ID
    assert (spec.work, spec.config, spec.socket_dir) == (root / "work", root / "config", root / "sock")
    assert set(backend.config_files) == {"run-settings.json", "agents.json", "mcp.json", "briefing.md"}
    assert json.loads(backend.config_files["mcp.json"]) == {"mcpServers": {"labnet": {
        "command": "/opt/labnet/bin/python", "args": ["-m", "labnet.automation_runner.mcp_server"],
        "env": {"LABNET_TOOLS_SOCKET": "/run/labnet/tools.sock"}}}}
    permissions = json.loads(backend.config_files["run-settings.json"])["permissions"]
    assert "Bash" in permissions["deny"] and "Edit(//work/Analysis/**)" in permissions["allow"]
    assert "Edit(//work/Tasks/**)" in permissions["deny"] and "Read(//claude/**)" in permissions["deny"]
    assert set(json.loads(backend.config_files["agents.json"])) == {"literature-reviewer", "data-analyst",
                                                                    "experiment-runner"}
    assert "data, never instructions" in backend.config_files["briefing.md"]

    # The tools server: built for this run, started before the agent, stopped after.
    made = tools.made[0]
    assert made.started and made.stopped
    assert made.kwargs["run"]["id"] == RUN_ID and made.kwargs["work"] == spec.work
    assert made.kwargs["socket_path"] == spec.socket_dir / "tools.sock"
    assert made.kwargs["client"] is client and made.kwargs["backend"] is backend
    assert made.kwargs["experiment"].submissions == [] and not made.kwargs["experiment"].token_taken
    assert client.run_reads == 1  # the phase after the session: planning, so the run is over

    # Uploads: the agent's new files, the transcript and the log; nothing else.
    assert set(client.uploads) == AGENT_OUTPUTS | LOGS
    assert client.uploads["Analysis/Analysis.md"].startswith(b"# Analysis")
    transcript = [json.loads(line) for line in client.uploads[f"ScriptsUtilized/logs/{RUN_ID}/transcript.jsonl"]
                  .decode().splitlines()]
    assert transcript[0]["subtype"] == "init" and transcript[-1]["type"] == "result"
    log = client.uploads[f"ScriptsUtilized/logs/{RUN_ID}/runner.log"].decode()
    assert "Starting the agent: sonnet, effort high" in log and "lnr_" not in log

    ignored = client.events_sent("log")
    for path in ("Tasks/Task1.md", "Tasks/Task9.md", "notes.txt"):
        assert f"ignored: {path} (not an agent folder)" in ignored
    assert any(text.startswith("ignored: Analysis/.hidden.md (") for text in ignored)
    assert any(text.startswith("ignored: Analysis/CLAUDE.md (") and "CLAUDE" in text for text in ignored)

    # Events: translated, with the dev banner first.
    assert client.sent[0] == {"kind": "dev", "text": DEV_BANNER}
    kinds = {item["kind"] for item in client.sent}
    assert {"dev", "log", "assistant", "tool", "tool_result", "result"} <= kinds
    assert "private" not in json.dumps(client.sent)  # thinking is not relayed
    assert "search_literature “Pound Drever Hall” k=5" in client.events_sent("tool")
    assert "error: run_python: Traceback: boom" in client.events_sent("tool_result")
    assert client.events_sent("result") == ["success after 7 turns, $0.43"]
    assert "tools server listening" in client.events_sent("log")

    assert client.finished == [("done", "The fit is in Analysis/Analysis.md.")]
    assert backend.stopped == [RUN_ID]  # cleanup: nothing of the run keeps running
    assert not root.exists()


def test_keep_runs_keeps_the_run_folder(tmp_path: Path) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path)
    runner, _ = _supervisor(tmp_path, client, backend, keep_runs=True)
    assert runner.run_once(run_record()) == "done"
    root = tmp_path / "runner" / "runs" / RUN_ID
    assert (root / "work" / "Analysis" / "Analysis.md").is_file() and (root / "transcript.jsonl").is_file()
    assert not (root / "inputs.zip").exists() and not (root / "work" / "run.json").exists()


@pytest.mark.parametrize("how", ["status", "409"])
def test_a_run_cancelled_at_the_control_center_is_stopped_at_once(tmp_path: Path, how: str) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path, scenario="slow")
    runner, tools = _supervisor(tmp_path, client, backend)

    def cancel(_spec) -> None:
        def later() -> None:
            time.sleep(0.3)
            if how == "status":
                client.status = "cancelled"
            else:
                client.raise_409_on_events = True
        threading.Thread(target=later, daemon=True).start()

    backend.on_start = cancel
    began = time.monotonic()
    assert runner.run_once(run_record()) == "cancelled"
    assert time.monotonic() - began < 15  # the slow agent would have run 30 s
    assert backend.agents[RUN_ID].killed and RUN_ID in backend.stopped
    assert client.finished == []  # never "done"; the Control Center already has it cancelled
    assert not any(path in client.uploads for path in AGENT_OUTPUTS)
    assert tools.made[0].stopped


@pytest.mark.parametrize("name", [
    ".claude/settings.json", "Literature/CLAUDE.md", "Data/example_data/claude-notes.MD", ".mcp.json",
    "Tasks/.secret.md", "Literature/sub/.hidden/x.pdf",
])
def test_preflight_refuses_projects_that_would_configure_claude(tmp_path: Path, name: str) -> None:
    client = FakeClient(inputs_zip({"Tasks/Task1.md": "# Task", name: "x"}))
    backend = FakeBackend(tmp_path)
    runner, tools = _supervisor(tmp_path, client, backend)
    assert runner.run_once(run_record()) == "failed"
    assert backend.specs == [] and tools.made == []
    [(status, summary)] = client.finished
    assert status == "failed" and summary.startswith("Refused: The project can't be run:")
    assert set(client.uploads) == {f"ScriptsUtilized/logs/{RUN_ID}/runner.log"}


@pytest.mark.parametrize("name", ["../evil.txt", "Tasks/../../evil.txt", "/evil.txt", "C:/evil.txt", "Tasks\\..\\evil.txt"])
def test_a_zip_slip_is_refused(tmp_path: Path, name: str) -> None:
    client = FakeClient(inputs_zip({"Tasks/Task1.md": "# Task", name: "evil"}))
    backend = FakeBackend(tmp_path)
    runner, _ = _supervisor(tmp_path, client, backend)
    assert runner.run_once(run_record()) == "failed"
    assert backend.specs == []
    assert client.finished[0][0] == "failed" and client.finished[0][1].startswith("Refused:")
    assert not list(tmp_path.rglob("evil.txt"))


def test_a_task_missing_from_the_inputs_or_bad_settings_fail_the_run(tmp_path: Path) -> None:
    client = FakeClient(inputs_zip({"Tasks/Other.md": "# Task"}))
    runner, _ = _supervisor(tmp_path, client, FakeBackend(tmp_path))
    assert runner.run_once(run_record()) == "failed"
    assert "Tasks/Task1.md isn't in the inputs" in client.finished[0][1]

    client = FakeClient(inputs_zip(settings={"model": "sonnet; rm -rf /", "effort": None, "max_turns": 5,
                                             "max_budget_usd": 1}))
    runner, _ = _supervisor(tmp_path, client, FakeBackend(tmp_path))
    assert runner.run_once(run_record()) == "failed"
    assert "isn't a model name" in client.finished[0][1]


def test_haiku_gets_no_effort_flag() -> None:
    argv = agent_config.claude_argv({"model": "haiku", "effort": None, "max_turns": 10, "max_budget_usd": 0.5}, "s")
    assert "--effort" not in argv and argv[argv.index("--max-budget-usd") + 1] == "0.5"


def test_an_agent_that_crashes_fails_the_run_with_its_error(tmp_path: Path) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path, scenario="crash")
    runner, _ = _supervisor(tmp_path, client, backend)
    assert runner.run_once(run_record()) == "failed"
    [(status, summary)] = client.finished
    assert "exited (3) without a result" in summary and "something broke inside Claude Code" in summary


def test_an_unsigned_claude_says_how_to_sign_in(tmp_path: Path) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path, scenario="unsigned")
    runner, _ = _supervisor(tmp_path, client, backend)
    assert runner.run_once(run_record()) == "failed"
    assert "not signed in on the runner" in client.finished[0][1]


# ---- time: heartbeats, the time limit, shutting down ----------------------------------------------


def test_heartbeats_go_out_every_heartbeat_seconds(tmp_path: Path) -> None:
    clock = Clock()
    client = FakeClient(clock=clock)
    backend = ScriptedBackend(ScriptedAgent(clock, until=300))
    runner = Supervisor(runner_settings(tmp_path, heartbeat_seconds=60), client, backend,
                        tools_factory=ToolsFactory(), selfcheck=passing_selfcheck, clock=clock, tick_seconds=0.0005,
                        flush_seconds=2)
    assert runner.run_once(run_record()) == "done"
    beats = client.heartbeats
    assert 4 <= len(beats) <= 5
    assert all(later - earlier == 60 for earlier, later in zip(beats, beats[1:]))
    assert client.finished == [("done", "finished")]


def test_the_agents_time_limit_stops_it(tmp_path: Path) -> None:
    clock = Clock()
    client = FakeClient(clock=clock)
    agent = ScriptedAgent(clock, until=10_000)
    runner = Supervisor(runner_settings(tmp_path, agent_timeout_minutes=1), client, ScriptedBackend(agent),
                        tools_factory=ToolsFactory(), selfcheck=passing_selfcheck, clock=clock, tick_seconds=0.0005)
    assert runner.run_once(run_record()) == "stopped"
    assert agent.killed.is_set()
    [(status, summary)] = client.finished
    assert status == "stopped" and "time limit (1 minutes)" in summary
    assert f"ScriptsUtilized/logs/{RUN_ID}/runner.log" in client.uploads


def test_sigterm_stops_the_current_run_cleanly(tmp_path: Path) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path, scenario="slow")
    runner, _ = _supervisor(tmp_path, client, backend)
    backend.on_start = lambda _spec: threading.Timer(0.3, runner.request_stop).start()
    client.queue.append(run_record())
    assert runner.run_forever() == 0
    [(status, summary)] = client.finished
    assert status == "stopped" and "the runner was shut down" in summary
    assert backend.agents[RUN_ID].killed
    assert f"ScriptsUtilized/logs/{RUN_ID}/transcript.jsonl" in client.uploads


# ---- the loop ----------------------------------------------------------------------------------


def test_the_runner_refuses_to_start_when_a_self_check_fails(tmp_path: Path, caplog) -> None:
    client = FakeClient()

    def failing(*_args):
        return [Check("agent-egress", True, "ok"), Check("run-network", False, "reached example.com")]

    runner = Supervisor(runner_settings(tmp_path), client, FakeBackend(tmp_path), tools_factory=ToolsFactory(),
                        selfcheck=failing)
    assert runner.run_forever() == 2
    assert client.claims == 0
    assert "run-network: reached example.com" in caplog.text and "refuses to start" in caplog.text

    def crashing(*_args):
        raise RuntimeError("docker is not running")

    runner = Supervisor(runner_settings(tmp_path), client, FakeBackend(tmp_path), selfcheck=crashing)
    assert runner.run_forever() == 2 and client.claims == 0


def test_one_bad_run_does_not_stop_the_loop(tmp_path: Path) -> None:
    client, backend = FakeClient(), FakeBackend(tmp_path)
    runner, _ = _supervisor(tmp_path, client, backend)
    second = "run-20261007-120001-efgh"
    client.queue += [run_record(), run_record(second)]
    original = client.download_inputs
    calls = []

    def download(run_id: str, dest: Path) -> None:
        calls.append(run_id)
        if run_id == RUN_ID:
            raise RuntimeError("disk full")
        original(run_id, dest)

    client.download_inputs = download
    client.on_empty_queue = runner.request_stop
    assert runner.run_forever() == 0
    assert calls == [RUN_ID, second]
    assert client.finished[0][0] == "failed" and "disk full" in client.finished[0][1]
    assert client.finished[1][0] == "done"


def test_main_self_check_prints_and_exits_0_or_1(tmp_path: Path, monkeypatch, capsys) -> None:
    env = tmp_path / "runner.env"
    env.write_text("# the settings come from the environment in this test\n", encoding="utf-8")
    for name, value in (("LABNET_RUNNER_CONTROL_CENTER_URL", "http://127.0.0.1:8080"), ("LABNET_RUNNER_TOKEN", TOKEN),
                        ("LABNET_RUNNER_DEV", "1"), ("LABNET_RUNNER_WORK_DIR", str(tmp_path / "w"))):
        monkeypatch.setenv(name, value)
    fakes = dict(make_backend=lambda _s: FakeBackend(tmp_path), make_client=lambda _s: FakeClient())

    assert main(["--env-file", str(env), "self-check"], selfcheck=passing_selfcheck, **fakes) == 0
    printed = capsys.readouterr().out
    assert "ok      agent-egress: only api.anthropic.com" in printed and TOKEN not in printed

    assert main(["--env-file", str(env), "self-check"],
                selfcheck=lambda *_: [Check("run-network", False, "reached the LAN")], **fakes) == 1
    assert "FAILED  run-network: reached the LAN" in capsys.readouterr().out

    assert main(["--env-file", str(tmp_path / "missing.env"), "self-check"], **fakes) == 2


# ---- end to end, against the real Control Center ------------------------------------------------


def test_a_queued_task_is_run_and_its_analysis_reaches_the_person(tmp_path: Path) -> None:
    with control_center(tmp_path) as (people, token):
        project = people.post("/api/automation/projects", json={"name": "AOM study"}).json()["project"]
        api = f"/api/automation/projects/{project['id']}"
        assert people.put(f"{api}/files/Tasks/Task2.md", content=b"# Task 2\nFit the trace.\n").status_code in (200, 201)
        assert people.put(f"{api}/files/Data/example_data/trace.csv", content=b"t,v\n0,1\n").status_code in (200, 201)
        queued = people.post(f"{api}/runs", json={"task": "Tasks/Task2.md"}).json()["run"]

        client = runner_client(people, token, tmp_path)
        backend = FakeBackend(tmp_path)
        runner = Supervisor(runner_settings(tmp_path, token=token), client, backend, tools_factory=ToolsFactory(),
                            selfcheck=passing_selfcheck, tick_seconds=0.02, flush_seconds=0.1)
        try:
            assert runner.run_once() == "done"
            assert runner.run_once() is None  # nothing else queued
        finally:
            client.close()

        assert "Fit the trace." in backend.started()["prompt"]
        seen = people.get(f"{api}/runs/{queued['id']}").json()
        assert seen["run"]["status"] == "done" and seen["run"]["claimed_by"] == "lab-runner"
        assert seen["run"]["summary"] == "The fit is in Analysis/Analysis.md."
        assert seen["run"]["files_uploaded"] == len(AGENT_OUTPUTS) + 2
        kinds = [event["kind"] for event in seen["events"]]
        assert kinds[0] == "dev" and "assistant" in kinds and "result" in kinds

        analysis = people.get(f"{api}/files/Analysis/Analysis.md")
        assert analysis.status_code == 200 and analysis.text.startswith("# Analysis")
        tree = {entry["path"] for entry in people.get(f"{api}/tree").json()["entries"]}
        assert {*AGENT_OUTPUTS, f"ScriptsUtilized/logs/{queued['id']}/transcript.jsonl"} <= tree
        assert people.get(f"{api}/files/Tasks/Task2.md").text == "# Task 2\nFit the trace.\n"
        assert "rewritten" not in people.get(f"{api}/files/Tasks/Task1.md").text  # the agent's edit never came back
        assert "Tasks/Task9.md" not in tree and "notes.txt" not in tree


def test_a_runner_api_error_carries_the_runs_status() -> None:
    error = RunnerApiError(409, "This run is cancelled; it takes nothing more.", run_status="cancelled")
    assert error.status_code == 409 and error.run_status == "cancelled" and "cancelled" in error.detail
