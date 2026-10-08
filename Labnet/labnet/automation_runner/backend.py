"""The containers a run uses, behind one small interface.

:class:`Backend` is what the supervisor and the tools server need; Docker's
implementation is ``docker_backend.DockerBackend``, and the tests use a fake. Paths are
paths on the Docker host (the runner container mounts its work folder at the
same path, so they mean the same thing to Docker).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One agent container: Claude Code in the working copy."""

    run_id: str
    work: Path  # the working copy, mounted at /work (Claude Code's cwd)
    config: Path  # read-only: run-settings.json, agents.json, mcp.json, the briefing; mounted at /labnet
    socket_dir: Path  # holds tools.sock, the supervisor's tools server; mounted at /run/labnet
    argv: Sequence[str]  # `claude -p ...`
    env: Mapping[str, str]
    stdin: str  # the task prompt


class AgentProcess(Protocol):
    def lines(self) -> Iterator[str]:
        """Claude Code's stdout, line by line, until it exits."""

    def stderr_tail(self) -> str: ...

    def kill(self) -> None:
        """Stop the container now (Stop pressed, the run cancelled, or the time limit)."""

    def wait(self) -> int:
        """The exit code."""


@dataclass(frozen=True, slots=True)
class PythonRequest:
    run_id: str
    work: Path  # mounted read-write at /work
    script: str  # relative to /work, e.g. ScriptsUtilized/analysis_scripts/fit.py
    args: Sequence[str]
    timeout_seconds: float
    # While a device grant is active: the folder holding the device relay's gateway.sock, mounted
    # at /run/labnet-devices (``labnet_devices.connect()``). None otherwise: no devices at all.
    devices_socket_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class PythonResult:
    exit_code: int | None  # None: killed (time limit or stopped)
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool


class Backend(Protocol):
    def start_agent(self, spec: AgentSpec) -> AgentProcess: ...

    def run_python(self, request: PythonRequest) -> PythonResult: ...

    def stop_run(self, run_id: str) -> None:
        """Kill every container of this run (agent and run_python)."""


__all__ = ["AgentProcess", "AgentSpec", "Backend", "PythonRequest", "PythonResult"]
