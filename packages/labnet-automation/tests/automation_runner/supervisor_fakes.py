"""Fakes for the supervisor and client tests: a local "agent container", a tools server, a client, a clock."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from labnet_automation.backend import AgentSpec, PythonRequest, PythonResult
from labnet_automation.client import ControlCenterClient, RunnerApiError
from labnet_automation.config import RunnerSettings
from labnet_automation.protocol_text import protocol_sha256
from labnet_control_center.app import create_app
from labnet_control_center.config import ControlCenterSettings


FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")
TOKEN = "lnr_" + "T" * 43
RUN_ID = "run-20261007-120000-abcd"
SETTINGS = {"model": "sonnet", "effort": "high", "max_turns": 60, "max_budget_usd": 5.0}


def runner_settings(tmp_path: Path, **overrides: Any) -> RunnerSettings:
    values: dict[str, Any] = dict(
        control_center_url="http://testserver", token=TOKEN, work_dir=tmp_path / "runner", dev=True,
        poll_seconds=0.02, heartbeat_seconds=60.0, egress_proxy="http://labnet-automation-egress:3128",
    )
    values.update(overrides)
    return RunnerSettings(**values)


def run_record(run_id: str = RUN_ID, task: str = "Tasks/Task1.md") -> dict[str, Any]:
    return {"id": run_id, "project_id": "aom-study", "task": task, "status": "active", "settings": SETTINGS}


def inputs_zip(files: dict[str, bytes | str] | None = None, *, run: dict[str, Any] | None = None,
               settings: dict[str, Any] | None = None) -> bytes:
    """An inputs zip like the Control Center's: people's files and run.json."""

    run = run or run_record()
    files = {"Tasks/Task1.md": "# Task 1\nFit the trace in Data/example_data/trace.csv.\n",
             "Data/example_data/trace.csv": "t,v\n0,1\n1,2\n",
             "Experiment_layout/DEVICES.md": "# Devices\n"} if files is None else files
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("run.json", json.dumps({
            "run": run, "settings": settings or SETTINGS,
            "project": {"id": "aom-study", "name": "AOM study"}, "library": [], "truncated": False,
        }))
    return buffer.getvalue()


# ---- the agent container, as a local process ------------------------------------------------


class LocalAgent:
    """``AgentProcess`` for a local subprocess (fake_claude.py)."""

    def __init__(self, popen: subprocess.Popen[str]) -> None:
        self.popen = popen
        self.killed = False
        self._stderr: deque[str] = deque(maxlen=20)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

    def _read_stderr(self) -> None:
        assert self.popen.stderr is not None
        for line in self.popen.stderr:
            self._stderr.append(line)

    def lines(self) -> Iterator[str]:
        assert self.popen.stdout is not None
        yield from self.popen.stdout

    def stderr_tail(self) -> str:
        self._stderr_reader.join(timeout=5)
        return "".join(self._stderr)

    def kill(self) -> None:
        self.killed = True
        if self.popen.poll() is None:
            self.popen.kill()

    def wait(self) -> int:
        code = self.popen.wait(timeout=30)
        for pipe in (self.popen.stdout, self.popen.stderr):
            if pipe is not None:
                pipe.close()
        return code


class FakeBackend:
    """Runs fake_claude.py with cwd = the working copy, as the agent container would run claude.

    ``scenario`` is one scenario for every session, or a list: one per session (the last one repeats).
    """

    def __init__(self, tmp_path: Path, scenario: str | list[str] = "work", *, cost: float = 0.5) -> None:
        self.scenarios = [scenario] if isinstance(scenario, str) else list(scenario)
        self.cost = cost
        self.tmp_path = tmp_path
        self.record = tmp_path / "claude-record-1.json"
        self.specs: list[AgentSpec] = []
        self.config_files: dict[str, str] = {}
        self.stopped: list[str] = []
        self.agents: dict[str, LocalAgent] = {}
        self.on_start: Any = None

    @property
    def scenario(self) -> str:
        return self.scenarios[0]

    def start_agent(self, spec: AgentSpec) -> LocalAgent:
        self.specs.append(spec)
        self.config_files = {path.name: path.read_text(encoding="utf-8") for path in spec.config.iterdir()}
        scenario = self.scenarios[min(len(self.specs), len(self.scenarios)) - 1]
        self.record = self.tmp_path / f"claude-record-{len(self.specs)}.json"
        env = {
            **spec.env,
            # What a local Python needs to start; the container gets spec.env only.
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "FAKE_CLAUDE_RECORD": str(self.record), "FAKE_CLAUDE_SCENARIO": scenario,
            "FAKE_CLAUDE_COST": str(self.cost),
        }
        popen = subprocess.Popen(
            [sys.executable, str(FAKE_CLAUDE), *spec.argv[1:]], cwd=spec.work, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        assert popen.stdin is not None
        popen.stdin.write(spec.stdin)
        popen.stdin.close()
        agent = LocalAgent(popen)
        self.agents[spec.run_id] = agent
        if self.on_start is not None:
            self.on_start(spec)
        return agent

    def run_python(self, request: PythonRequest) -> PythonResult:
        raise AssertionError("the fake tools server never runs Python")

    def stop_run(self, run_id: str) -> None:
        self.stopped.append(run_id)
        agent = self.agents.get(run_id)
        if agent is not None and agent.popen.poll() is None:
            agent.kill()

    def started(self, session: int | None = None) -> dict[str, Any]:
        """What fake_claude was started with: the latest session, or session 1, 2, …"""

        record = self.record if session is None else self.tmp_path / f"claude-record-{session}.json"
        return json.loads(record.read_text(encoding="utf-8"))


class Clock:
    """Advances one second each time it is read (the supervisor reads it once per tick)."""

    def __init__(self) -> None:
        self.now = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            self.now += 1.0
            return self.now


class ScriptedAgent:
    """An in-process ``AgentProcess`` that waits on a Clock: for heartbeat and time-limit tests."""

    def __init__(self, clock: Clock, until: float) -> None:
        self.clock, self.until = clock, until
        self.killed = threading.Event()

    def lines(self) -> Iterator[str]:
        yield json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "tools": [],
                          "mcp_servers": []})
        while self.clock.now < self.until and not self.killed.is_set():
            time.sleep(0.0005)
        if not self.killed.is_set():
            yield json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
                              "result": "finished", "total_cost_usd": 0.01})

    def stderr_tail(self) -> str:
        return ""

    def kill(self) -> None:
        self.killed.set()

    def wait(self) -> int:
        return -9 if self.killed.is_set() else 0


class ScriptedBackend:
    def __init__(self, agent: ScriptedAgent) -> None:
        self.agent = agent
        self.stopped: list[str] = []

    def start_agent(self, spec: AgentSpec) -> ScriptedAgent:
        return self.agent

    def run_python(self, request: PythonRequest) -> PythonResult:
        raise AssertionError

    def stop_run(self, run_id: str) -> None:
        self.stopped.append(run_id)
        self.agent.kill()


# ---- the tools server, the self-check, the client -----------------------------------------------


class FakeTools:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = self.stopped = False

    def start(self) -> None:
        self.started = True
        self.kwargs["on_event"]("log", "tools server listening")

    def stop(self) -> None:
        self.stopped = True


class ToolsFactory:
    """Makes FakeTools; ``submissions`` are recorded into the run's Experiment at start, as the real tools
    server records what submit_protocol uploaded."""

    def __init__(self, submissions: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.made: list[FakeTools] = []
        self.submissions = submissions or []

    def __call__(self, **kwargs: Any) -> FakeTools:
        tools = FakeTools(**kwargs)
        for sha256, protocol in self.submissions:
            kwargs["experiment"].record_submission(sha256, protocol)
        self.made.append(tools)
        return tools


class FakeRelay:
    """Stands in for DeviceRelay (no Unix sockets needed): records how it was made, started and stopped."""

    made: list["FakeRelay"] = []

    def __init__(self, experiment: Any, client: Any, socket_path: Path, on_event: Any, *, run_id: str = "") -> None:
        self.experiment, self.client, self.socket_path, self.run_id = experiment, client, socket_path, run_id
        self.started = self.stopped = False
        self.fail = False
        FakeRelay.made.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class Check:
    def __init__(self, name: str, ok: bool, detail: str = "") -> None:
        self.name, self.ok, self.detail = name, ok, detail


def passing_selfcheck(_settings: Any, _backend: Any, _client: Any) -> list[Check]:
    return [Check("agent-egress", True, "only api.anthropic.com"), Check("run-network", True, "no network")]


class FakeClient:
    """Records what the supervisor sends; serves one inputs zip; can say the run was cancelled."""

    def __init__(self, archive: bytes | None = None, *, clock: Clock | None = None) -> None:
        self.archive = archive if archive is not None else inputs_zip()
        self.clock = clock
        self.queue: list[dict[str, Any]] = []
        self.sent: list[dict[str, str]] = []  # every event received
        self.event_calls = 0
        self.heartbeats: list[float] = []
        self.uploads: dict[str, bytes] = {}
        self.finished: list[tuple[str, str]] = []
        self.status = "active"  # what events() / heartbeat() answer
        self.raise_409_on_events = False
        self.claims = 0
        self.on_empty_queue: Any = None
        self.run_reads = 0

    def get_run(self, run_id: str) -> dict[str, Any]:
        self.run_reads += 1
        return {"id": run_id, "status": self.status, "phase": "planning", "protocol": None, "grant": None}

    def whoami(self) -> str:
        return "test-runner"

    def claim(self) -> dict[str, Any] | None:
        self.claims += 1
        if self.queue:
            return self.queue.pop(0)
        if self.on_empty_queue is not None:
            self.on_empty_queue()
        return None

    def download_inputs(self, run_id: str, dest: Path) -> None:
        dest.write_bytes(self.archive)

    def upload(self, run_id: str, relative: str, file: Path) -> dict[str, Any]:
        if self.status != "active":
            raise RunnerApiError(409, "This run is cancelled; it takes nothing more.", run_status=self.status)
        self.uploads[relative] = file.read_bytes()
        return {"file": {"path": relative}}

    def events_sent(self, kind: str | None = None) -> list[str]:
        return [item["text"] for item in self.sent if kind is None or item["kind"] == kind]

    def events(self, run_id: str, events: list[dict[str, str]]) -> str:
        self.event_calls += 1
        if self.raise_409_on_events:
            raise RunnerApiError(409, "This run is cancelled; it takes nothing more.", run_status="cancelled")
        self.sent.extend(events)
        return self.status

    def heartbeat(self, run_id: str) -> str:
        self.heartbeats.append(self.clock.now if self.clock else time.monotonic())
        return self.status

    def finish(self, run_id: str, status: str, summary: str) -> dict[str, Any]:
        self.finished.append((status, summary))
        return {"id": run_id, "status": status, "summary": summary}

    def close(self) -> None:
        pass


# ---- a real Control Center, in-process ----------------------------------------------------------


class FakeCentral:
    async def list_lab_pcs(self) -> list:
        return []

    async def aclose(self) -> None:
        pass


@contextmanager
def control_center(tmp_path: Path, **overrides: Any) -> Iterator[tuple[TestClient, str]]:
    """A Control Center (no logins) with a small library, and a runner token for "lab-runner"."""

    library = tmp_path / "literature"
    (library / "lasers").mkdir(parents=True)
    (library / "lasers" / "pdh-notes.txt").write_text(
        "The Pound Drever Hall technique locks a laser to a cavity using an error signal.", encoding="utf-8"
    )
    values: dict[str, Any] = dict(
        server_url="http://127.0.0.1:8008", api_key="key", ca_file=None, bind_host="127.0.0.1", port=8080,
        refresh_seconds=60.0, experiments_dir=tmp_path / "experiments", automation_dir=tmp_path / "automation",
        agent_workspace=tmp_path / "agent", agent_literature_dir=library,
    )
    values.update(overrides)
    with TestClient(create_app(ControlCenterSettings(**values), client_factory=lambda _: FakeCentral())) as people:
        token = people.app.state.runners.add("lab-runner")
        yield people, token


# ---- a Control Center with run phases (Phase 3), as the supervisor sees it -----------------------------

GRANT_TOKEN = "lng_" + "G" * 43
# The protocol as the agent wrote it (what the tools server records at submission)...
PROTOCOL = {
    "schema_version": 1, "task": "Tasks/Task1.md", "title": "AOM efficiency vs RF frequency", "duration_minutes": 20,
    "devices": [{"device_id": "dev01-aom", "device_type": "dummy_aom", "methods": {
        "set_frequency": {"args": {"value": {"min": 70, "max": 90, "max_step": 2}}, "max_calls": 200, "max_rate_hz": 5},
        "set_rf_enabled": {"args": {"enabled": {"choices": [True, False]}}, "max_calls": 10, "max_rate_hz": 1},
    }}],
    "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}],
}
# ...and as the Control Center normalized and a person approved it (sent with the grant's token).
APPROVED = {
    "schema_version": 1, "task": "Tasks/Task1.md", "title": "AOM efficiency vs RF frequency", "duration_minutes": 20.0,
    "devices": [{"device_id": "dev01-aom", "device_type": "dummy_aom", "methods": {
        "set_frequency": {"args": {"value": {"min": 70.0, "max": 90.0, "max_step": 2.0}}, "max_calls": 200,
                          "max_rate_hz": 5.0},
        "set_rf_enabled": {"args": {"enabled": {"choices": [True, False]}}, "max_calls": 10, "max_rate_hz": 1.0},
    }}],
    "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}],
}
SHA = protocol_sha256(APPROVED)
GRANT = {"id": "grant-7", "status": "active", "approved_by": "alice", "starts_at": "2026-10-07T12:00:00Z",
         "ends_at": "2026-10-07T12:20:00Z", "devices": ["dev01-aom"], "calls": 0, "refused_calls": 0}


def awaiting(submissions: int = 1, sha256: str = SHA) -> dict[str, Any]:
    return {"phase": "awaiting_approval", "protocol": {"submissions": submissions, "status": "submitted",
                                                       "sha256": sha256, "title": PROTOCOL["title"]}}


def approved(submissions: int = 1, sha256: str = SHA) -> dict[str, Any]:
    return {"phase": "executing", "grant": GRANT,
            "protocol": {"submissions": submissions, "status": "approved", "sha256": sha256,
                         "reviewed_by": "alice", "reviewed_at": "2026-10-07T12:00:00Z"}}


def rejected(submissions: int = 1, note: str = "Too fast: at most 1 call/s.") -> dict[str, Any]:
    return {"phase": "planning", "protocol": {"submissions": submissions, "status": "rejected", "sha256": SHA,
                                              "reviewed_by": "bob", "note": note}}


class PhaseClient(FakeClient):
    """FakeClient whose run moves through phases: ``phases`` are what get_run answers in turn (the last one
    repeats); plus the grant token and grant/end."""

    def __init__(self, phases: list[dict[str, Any]], archive: bytes | None = None) -> None:
        super().__init__(archive)
        self.phases = list(phases)
        self.tokens_taken = 0
        self.token_error: Exception | None = None
        self.ended: list[str] = []
        self.end_error: Exception | None = None
        self.on_read: Any = None  # called with the read count before answering
        self.approved: dict[str, Any] | None = APPROVED  # the protocol sent with the token

    def get_run(self, run_id: str) -> dict[str, Any]:
        self.run_reads += 1
        if self.on_read is not None:
            self.on_read(self.run_reads)
        phase = self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]
        return {"id": run_id, "status": self.status, "protocol": None, "grant": None, **phase}

    def take_grant_token(self, run_id: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        self.tokens_taken += 1
        if self.token_error is not None:
            raise self.token_error
        if self.tokens_taken > 1:
            raise RunnerApiError(409, "The grant's token was already taken.")
        return GRANT_TOKEN, dict(GRANT), self.approved

    def end_grant(self, run_id: str, reason: str) -> dict[str, Any]:
        self.ended.append(reason)
        if self.end_error is not None:
            raise self.end_error
        return {**GRANT, "status": "ended", "ended_reason": reason, "ended_at": "2026-10-07T12:10:00Z"}

    def gateway_call(self, token: str, body: dict[str, Any], *, timeout: float | None = None) -> tuple[int, dict]:
        raise AssertionError("the supervisor tests use a FakeRelay")


def runner_client(people: TestClient, token: str, tmp_path: Path, **kwargs: Any) -> ControlCenterClient:
    """A ControlCenterClient that reaches the in-process Control Center."""

    return ControlCenterClient(runner_settings(tmp_path, token=token), transport=people._transport, **kwargs)
