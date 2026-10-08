"""The Backend on Docker: the docker CLI, argv lists only, never a shell.

Every container a run starts is labelled ``labnet.run=<run id>`` so that Stop
(``stop_run``) finds and kills all of them, and named after the run so a
time limit can kill exactly one. What each kind of container gets:

agent container (``start_agent``): Claude Code in the working copy.
    * the agent network (``internal``: its only way out is the egress proxy);
    * uid 1000, all capabilities dropped, no-new-privileges, a read-only root
      file system with small tmpfs folders, pid / memory / CPU limits;
    * mounts: the working copy (rw, /work), the run's trusted config (ro,
      /labnet), the folder holding the tools socket (/run/labnet), and the
      Claude login volume (/claude, rw so token refreshes are saved);
    * only the environment variables the supervisor names (``-e K=V``); the
      docker CLI itself runs with a cleaned environment, so nothing of the
      runner's (its token above all) can reach a container by accident.

run container (``run_python``): one script, then gone.
    * ``--network none``: no network at all, no credentials, no config;
    * the working copy only, same user and hardening as the agent, and the
      run's CPU / memory (no swap) / pid limits and a time limit; on the time
      limit the container is killed by name;
    * only while a device grant is active: the device relay's folder at
      /run/labnet-devices (its gateway.sock; the supervisor adds the grant's
      token to each call, so the container still holds no credentials);
    * no logging driver: what it prints is captured here, with caps, instead
      of piling up in Docker's log files.

``--pull never`` everywhere: a missing image is an error, never a download
of whatever an image registry has under that name.
"""

from __future__ import annotations

import collections
import io
import itertools
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, IO

from .backend import AgentSpec, PythonRequest, PythonResult
from .config import RunnerSettings


LOGGER = logging.getLogger("labnet.automation_runner.docker")

# The Control Center's run ids (labnet.control_center.automation_runs.RUN_ID; a test keeps them equal).
RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[a-z2-9]{4}")
CONTAINER_USER = "1000:1000"  # "node" in the agent image, "runner" in the run image
LABEL = "labnet.run"
DEVICES_MOUNT = "/run/labnet-devices"  # labnet_devices.connect()'s default socket is in here

AGENT_MEMORY = "4g"
AGENT_CPUS = "2"
AGENT_PIDS = "512"

# run_python output kept in memory (the rest is counted, not kept); the tools server logs it all.
OUTPUT_CAP_BYTES = 4 * 1024 * 1024
STDERR_TAIL_BYTES = 64 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 60.0
KILL_GRACE_SECONDS = 15.0

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# What the docker CLI needs to find its daemon and config. Nothing of the runner's own settings.
_DOCKER_ENV_NAMES = frozenset({
    "PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "XDG_RUNTIME_DIR",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "DOCKER_API_VERSION",
    # Windows, so the CLI can start when the tests (or a developer) run it there.
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "TEMP", "TMP",
})

Popen = Callable[..., Any]


class DockerError(RuntimeError):
    pass


def docker_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The docker CLI's environment: an allow-list of ``environ`` (default: this process's)."""

    source = os.environ if environ is None else environ
    env = {name: value for name, value in source.items()
           if name.upper() in _DOCKER_ENV_NAMES or name.upper().startswith("LC_")}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    return env


def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        raise DockerError(f"Not a run id: {run_id!r}")
    return run_id


def host_path(path: Path | str) -> str:
    """A path on the Docker host as ``-v`` wants it: absolute, POSIX, nothing ``-v`` would misread."""

    text = path.as_posix() if isinstance(path, Path) else str(path)
    if not text.startswith("/") or any(c in text for c in ":,\n\r\0") or ".." in PurePosixPath(text).parts:
        raise DockerError(f"Not a usable host path for a mount: {text!r}")
    return text


def script_path(script: str) -> str:
    """``script`` (relative to /work) as the absolute path inside the run container."""

    relative = PurePosixPath(script)
    if (not script or "\\" in script or "\0" in script or relative.is_absolute()
            or any(part in ("", ".", "..") for part in relative.parts) or script.startswith("-")):
        raise DockerError(f"Not a script path inside the working copy: {script!r}")
    return f"/work/{relative.as_posix()}"


def env_flags(env: Mapping[str, str]) -> list[str]:
    """``-e NAME=value`` for each entry. Always with ``=``: a bare ``-e NAME`` would copy the CLI's own value."""

    flags: list[str] = []
    for name, value in env.items():
        if _ENV_NAME.fullmatch(name) is None:
            raise DockerError(f"Not an environment variable name: {name!r}")
        if not isinstance(value, str) or "\0" in value or "\n" in value:
            raise DockerError(f"The value of {name} can't be passed to a container.")
        flags += ["-e", f"{name}={value}"]
    return flags


def _hardening(tmpfs: Sequence[str], pids: str, memory: str, cpus: str) -> list[str]:
    flags = [
        "--pull", "never",
        "--init",  # a real PID 1 that reaps what the process leaves behind
        "--user", CONTAINER_USER,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", pids,
        "--memory", memory,
        "--memory-swap", memory,  # equal to --memory: no swap on top
        "--cpus", cpus,
        "--read-only",
    ]
    for mount in tmpfs:
        flags += ["--tmpfs", mount]
    return flags


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class _CappedReader(threading.Thread):
    """Reads a binary stream to the end, keeping the first ``cap`` bytes."""

    def __init__(self, stream: IO[bytes], cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self._buffer = io.BytesIO()
        self.dropped = 0

    def run(self) -> None:
        try:
            while chunk := self._stream.read(65536):
                room = self._cap - self._buffer.tell()
                if room > 0:
                    self._buffer.write(chunk[:room])
                self.dropped += max(0, len(chunk) - max(room, 0))
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()
            except OSError:
                pass

    def text(self) -> str:
        text = self._buffer.getvalue().decode("utf-8", "replace")
        if self.dropped:
            text += f"\n[… {self.dropped} more bytes of output were not kept]\n"
        return text


class _TailReader(threading.Thread):
    """Reads a binary stream to the end, keeping the last ``cap`` bytes."""

    def __init__(self, stream: IO[bytes], cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._chunks: collections.deque[bytes] = collections.deque()
        self._size = 0
        self._cap = cap
        self._lock = threading.Lock()

    def run(self) -> None:
        try:
            while chunk := self._stream.read1(65536) if hasattr(self._stream, "read1") else self._stream.read(65536):
                self.add(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()
            except OSError:
                pass

    def add(self, chunk: bytes) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size - len(self._chunks[0]) >= self._cap and len(self._chunks) > 1:
                self._size -= len(self._chunks.popleft())

    def text(self) -> str:
        with self._lock:
            data = b"".join(self._chunks)
        return data[-self._cap:].decode("utf-8", "replace")


class DockerAgentProcess:
    """A running agent container (``docker run -i``)."""

    def __init__(self, backend: "DockerBackend", name: str, process: Any, stdin: str) -> None:
        self._backend = backend
        self.name = name
        self._process = process
        self._stderr = _TailReader(process.stderr, STDERR_TAIL_BYTES)
        self._stderr.start()
        self._stdin = threading.Thread(target=self._feed, args=(stdin,), daemon=True)
        self._stdin.start()

    def _feed(self, text: str) -> None:
        try:
            self._process.stdin.write(text.encode("utf-8"))
            self._process.stdin.flush()
        except (OSError, ValueError):
            pass  # the container ended before reading it; its exit code says why
        finally:
            try:
                self._process.stdin.close()
            except OSError:
                pass

    def lines(self) -> Iterator[str]:
        stream = self._process.stdout
        skipping = False
        while True:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            complete = raw.endswith(b"\n")
            if skipping or len(raw) > MAX_LINE_BYTES:
                # One stream-json line larger than the cap: dropped whole, never cut into bad JSON.
                if not skipping:
                    self._stderr.add(f"\n[labnet: dropped an output line over {MAX_LINE_BYTES} bytes]\n".encode())
                skipping = not complete
                continue
            yield raw.decode("utf-8", "replace").rstrip("\r\n")
        try:
            stream.close()
        except OSError:
            pass

    def stderr_tail(self) -> str:
        return self._stderr.text()

    def kill(self) -> None:
        self._backend.kill_container(self.name)
        try:
            self._process.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()

    def wait(self) -> int:
        code = self._process.wait()
        self._stderr.join(timeout=5)
        return code


class DockerBackend:
    """:class:`~labnet.automation_runner.backend.Backend` with the docker CLI."""

    def __init__(
        self,
        settings: RunnerSettings,
        *,
        docker: str = "docker",
        popen: Popen = subprocess.Popen,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.docker = docker
        self._popen = popen
        self._env = docker_environment(environ)
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._stopped: set[str] = set()

    # ---- plain commands ----------------------------------------------------------

    def _spawn(self, args: Sequence[str], *, stdin: bool = False) -> Any:
        return self._popen(
            [self.docker, *args],
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(self._env),
            close_fds=True,
        )

    def command(self, args: Sequence[str], *, timeout: float = COMMAND_TIMEOUT_SECONDS) -> CommandResult:
        """``docker <args>`` to completion. A missing CLI or a hang is a DockerError."""

        try:
            process = self._spawn(args)
        except OSError as exc:
            raise DockerError(f"Cannot run {self.docker}: {exc}") from exc
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise DockerError(f"`docker {' '.join(args[:3])} …` took longer than {timeout:g} s.") from None
        return CommandResult(process.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))

    def kill_container(self, name: str) -> None:
        try:
            self.command(["kill", name], timeout=30)  # "No such container" once it's gone: fine
        except DockerError:
            pass

    # ---- the agent -----------------------------------------------------------------

    def agent_argv(self, spec: AgentSpec) -> list[str]:
        run_id = check_run_id(spec.run_id)
        return [
            "run", "--rm", "-i",
            "--name", f"labnet-agent-{run_id}",
            "--label", f"{LABEL}={run_id}",
            "--network", self.settings.agent_network,
            *_hardening(["/tmp:rw,nosuid,size=512m", "/home/node:rw,nosuid,size=256m"],
                        AGENT_PIDS, AGENT_MEMORY, AGENT_CPUS),
            "-v", f"{host_path(spec.work)}:/work",
            "-v", f"{host_path(spec.config)}:/labnet:ro",
            "-v", f"{host_path(spec.socket_dir)}:/run/labnet",
            "-v", f"{self.settings.claude_login_volume}:/claude",
            "-w", "/work",
            *env_flags(spec.env),
            self.settings.agent_image,
            *spec.argv,
        ]

    def start_agent(self, spec: AgentSpec) -> DockerAgentProcess:
        argv = self.agent_argv(spec)
        try:
            process = self._spawn(argv, stdin=True)
        except OSError as exc:
            raise DockerError(f"Cannot run {self.docker}: {exc}") from exc
        return DockerAgentProcess(self, f"labnet-agent-{spec.run_id}", process, spec.stdin)

    # ---- run_python ------------------------------------------------------------------

    def python_argv(self, name: str, request: PythonRequest) -> list[str]:
        run_id = check_run_id(request.run_id)
        settings = self.settings
        for arg in request.args:
            if not isinstance(arg, str) or "\0" in arg:
                raise DockerError("Script arguments must be text without NUL characters.")
        devices: list[str] = []
        if request.devices_socket_dir is not None:
            # Read-write, so the script can connect to the relay's socket; it holds nothing else.
            devices = ["-v", f"{host_path(request.devices_socket_dir)}:{DEVICES_MOUNT}"]
        return [
            "run", "--rm",
            "--name", name,
            "--label", f"{LABEL}={run_id}",
            "--network", "none",
            "--log-driver", "none",
            *_hardening(["/tmp:rw,nosuid,size=256m"], str(settings.python_pids),
                        f"{settings.python_memory_mb}m", f"{settings.python_cpus:g}"),
            "-v", f"{host_path(request.work)}:/work",
            *devices,
            "-w", "/work",
            settings.run_image,
            "python", "-I", script_path(request.script), *request.args,
        ]

    def run_python(self, request: PythonRequest) -> PythonResult:
        with self._lock:
            # stop_run is only ever called as a run ends: a request still on its way in may not start
            # a container after the sweep, where nothing would kill it.
            if check_run_id(request.run_id) in self._stopped:
                return PythonResult(exit_code=None, stdout="", stderr="[labnet: the run was stopped]\n",
                                    seconds=0.0, timed_out=False)
        name = f"labnet-py-{check_run_id(request.run_id)}-{next(self._counter)}"
        argv = self.python_argv(name, request)
        timeout = min(float(request.timeout_seconds), self.settings.python_timeout_seconds)
        started = time.monotonic()
        try:
            process = self._spawn(argv)
        except OSError as exc:
            raise DockerError(f"Cannot run {self.docker}: {exc}") from exc
        out = _CappedReader(process.stdout, OUTPUT_CAP_BYTES)
        err = _CappedReader(process.stderr, OUTPUT_CAP_BYTES)
        out.start()
        err.start()
        timed_out = False
        try:
            code: int | None = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.kill_container(name)
            try:
                process.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            code = None
        seconds = time.monotonic() - started
        out.join(timeout=10)
        err.join(timeout=10)
        stderr = err.text()
        if timed_out:
            stderr += f"\n[labnet: stopped after the time limit of {timeout:g} s]\n"
        elif request.run_id in self._stopped:
            code = None
            stderr += "\n[labnet: stopped with the run]\n"
        return PythonResult(exit_code=code, stdout=out.text(), stderr=stderr, seconds=seconds, timed_out=timed_out)

    # ---- stopping ------------------------------------------------------------------------

    def run_containers(self, run_id: str) -> list[str]:
        result = self.command(["ps", "-q", "--filter", f"label={LABEL}={check_run_id(run_id)}"], timeout=30)
        if not result.ok:
            raise DockerError(f"docker ps failed: {result.stderr.strip()[:300]}")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def stop_run(self, run_id: str) -> None:
        """Kill whatever still runs for ``run_id``. Idempotent: nothing left (a normal end) is fine, and so
        is a container that ends between ``ps`` and ``kill``. Never raises for Docker trouble (it's logged):
        it is called at the end of every run and from the supervisor's watch thread."""

        with self._lock:
            self._stopped.add(check_run_id(run_id))
        try:
            ids = self.run_containers(run_id)
            if ids:
                result = self.command(["kill", *ids], timeout=30)
                if not result.ok and "No such container" not in result.stderr and "is not running" not in result.stderr:
                    LOGGER.warning("docker kill for %s: %s", run_id, result.stderr.strip()[:300])
        except DockerError as exc:
            LOGGER.warning("Could not stop the containers of %s: %s", run_id, exc)


__all__ = [
    "CommandResult", "DockerAgentProcess", "DockerBackend", "DockerError", "RUN_ID",
    "check_run_id", "docker_environment", "env_flags", "host_path", "script_path",
]
