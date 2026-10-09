"""Background processes of the development network: started detached, found again by pid file and port.

A started server's pid is kept in ``.devnet/run/<name>.pid``. On Windows a
venv's ``labnet-central.exe`` is a launcher that starts a second python.exe,
so stopping kills the whole process tree (``taskkill /T``); elsewhere the
server runs in its own session and its process group gets SIGTERM, which
lets Uvicorn shut down cleanly. A port still held afterwards (a copy started
by hand) is traced to its listening process and stopped too, if it is Python.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path

WINDOWS = os.name == "nt"


class DevError(RuntimeError):
    """Something the developer must fix; the message says what."""


def command(name: str) -> Path | None:
    """An installed LabNet command (``labnet-central``), next to this Python first."""

    scripts = Path(sysconfig.get_path("scripts"))
    for candidate in (scripts / f"{name}.exe", scripts / name):
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def clean_environment() -> dict[str, str]:
    """This process's environment without LABNET_* (a stray value must not override the settings files)."""

    env = {key: value for key, value in os.environ.items() if not key.startswith("LABNET_")}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as reply:  # noqa: S310 - loopback URLs we build
            return 200 <= reply.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def tail(path: Path, lines: int = 25) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def wait_for_http(url: str, timeout: float, log_file: Path | None = None) -> None:
    deadline = time.monotonic() + timeout
    while not http_ok(url):
        if time.monotonic() > deadline:
            detail = f"\nLast lines of {log_file}:\n{tail(log_file)}" if log_file else ""
            raise DevError(f"{url} was not ready after {timeout:g} s.{detail}")
        time.sleep(0.5)


def start(name: str, argv: Sequence[str], *, log_file: Path, run_dir: Path, cwd: Path) -> int:
    """Start ``argv`` detached, logging to ``log_file``; remember its pid."""

    log_file.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as log:
        process = subprocess.Popen(
            [str(part) for part in argv],
            cwd=cwd,
            env=clean_environment(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            # One hidden console shared with the venv launcher's child: no windows pop up
            # (closing one would kill a server), and Ctrl+C here doesn't reach it.
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if WINDOWS else 0,
            start_new_session=not WINDOWS,
        )
    (run_dir / f"{name}.pid").write_text(str(process.pid), encoding="ascii")
    return process.pid


def alive(pid: int) -> bool:
    if WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True
        )
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def image(pid: int) -> str:
    """The program name of ``pid`` (python.exe, python3.12, ...), or ""."""

    if WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True
        )
        first = result.stdout.strip().splitlines()[:1]
        return first[0].split(",")[0].strip('"') if first and f'"{pid}"' in first[0] else ""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True)
        return result.stdout.strip()


def listener(port: int) -> int | None:
    """The pid listening on the local TCP ``port``, or None."""

    if WINDOWS:
        result = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            # Listening sockets have a foreign address ending in :0 (on any Windows locale).
            if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[2].endswith(":0") and parts[4].isdigit():
                return int(parts[4])
        return None
    for probe in (["ss", "-ltnpH", f"sport = :{port}"], ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"]):
        if shutil.which(probe[0]) is None:
            continue
        output = subprocess.run(probe, capture_output=True, text=True).stdout
        if probe[0] == "ss":
            marker = output.find("pid=")
            if marker >= 0:
                return int(output[marker + 4:].split(",")[0])
        elif output.strip():
            return int(output.split()[0])
    return None


def kill_tree(pid: int) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)  # started in its own session
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    for _ in range(30):
        if not alive(pid):
            return
        time.sleep(0.5)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop(name: str, ports: Iterable[int], run_dir: Path) -> bool:
    """Stop ``name``; True if something was running. Raises if a port stays held by Python."""

    stopped = False
    pid_file = run_dir / f"{name}.pid"
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
        except ValueError:
            pid = 0
        if pid and alive(pid):
            kill_tree(pid)
            stopped = True
        pid_file.unlink(missing_ok=True)
    for port in ports:
        deadline = time.monotonic() + 15
        while port_in_use(port) and time.monotonic() < deadline:
            holder = listener(port)
            if holder is None:
                time.sleep(0.5)
                continue
            if "python" not in image(holder).lower():
                print(f"warn port {port} is held by {image(holder) or 'another program'} (pid {holder}); "
                      "leaving it alone", file=sys.stderr)
                break
            kill_tree(holder)
            stopped = True
            time.sleep(0.5)
        if port_in_use(port) and "python" in image(listener(port) or 0).lower():
            raise DevError(f"Could not stop {name} on port {port}.")
    return stopped


__all__ = [
    "DevError",
    "alive",
    "clean_environment",
    "command",
    "http_ok",
    "kill_tree",
    "listener",
    "port_in_use",
    "start",
    "stop",
    "tail",
    "wait_for_http",
]
