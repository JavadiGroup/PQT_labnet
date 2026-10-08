"""Run a validated script in a separate process and serve its device calls.

The script process (:mod:`labnet_control_center.script_worker`) never holds a
credential. A reader thread relays its device calls to the Control Center's
event loop, where they run with the session's lease, and sends the results
back. Stop or the time limit kills the process, so even a script stuck in a
long calculation ends.

The worker also starts with a cleaned environment (:func:`worker_environment`):
only the variables Python itself needs, never the Control Center's settings
(``LABNET_*``, with Central's key) or an Anthropic credential. Errors sent back
to the script, and the outcome shown on the page, pass through
:func:`~labnet.redaction.redact`, so a lease id quoted by a Lab PC
never reaches the script or the browser.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from labnet.redaction import redact
from .scripting import contract_method_names


CallHandler = Callable[[str, str, list[Any], dict[str, Any]], Awaitable[Any]]
LeaseHandler = Callable[[str], Awaitable[dict[str, Any]]]
EventHandler = Callable[[dict[str, Any]], None]

# One device call (including a short stream) may take this long.
CALL_TIMEOUT_SECONDS = 300.0

# What a Python process needs to start and run on Windows and POSIX: system
# folders, temp folders, the search paths for interpreters and packages (the
# user site lives under APPDATA / HOME), and the locale. Nothing else is passed.
_WORKER_ENV_NAMES = frozenset(
    {
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
        "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
        "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
        "TEMP", "TMP", "TMPDIR", "PATH",
        "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONNOUSERSITE", "PYTHONUTF8",
        "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX",
        "LANG", "LANGUAGE", "TZ",
    }
)


def worker_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The script worker's environment: an allow-list of ``environ`` (default: this process's)."""

    source = os.environ if environ is None else environ
    env = {
        name: value
        for name, value in source.items()
        if name.upper() in _WORKER_ENV_NAMES or name.upper().startswith("LC_")
    }
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class ScriptProcess:
    """One running script. Create, then ``start()``; ``stop()`` kills it."""

    def __init__(
        self,
        *,
        code: str,
        devices: dict[str, dict[str, Any]],
        on_call: CallHandler,
        on_lease: LeaseHandler,
        on_event: EventHandler,
        on_finished: Callable[[str | None], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        timeout_seconds: float,
    ) -> None:
        self._request = {
            "code": code,
            "devices": devices,
            "lease_methods": sorted(contract_method_names()),
        }
        self._on_call = on_call
        self._on_lease = on_lease
        self._on_event = on_event
        self._on_finished = on_finished
        self._loop = loop
        self._timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._stop_reason: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._process = subprocess.Popen(
            [sys.executable, "-m", "labnet_control_center.script_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=worker_environment(),
            # No console window on Windows; the Control Center may have none.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        self._timer = threading.Timer(
            self._timeout_seconds,
            self.stop,
            args=(f"Stopped: the script ran longer than {self._timeout_seconds:g} s.",),
        )
        self._timer.daemon = True
        self._timer.start()
        threading.Thread(target=self._serve, name="labnet-script", daemon=True).start()

    def stop(self, reason: str = "Stopped by user.") -> None:
        with self._lock:
            if self._stop_reason is None:
                self._stop_reason = reason
        if self._process is not None and self._process.poll() is None:
            self._process.kill()

    # ---- reader thread ------------------------------------------------------

    def _emit(self, event: dict[str, Any]) -> None:
        self._loop.call_soon_threadsafe(self._on_event, event)

    def _reply(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(message) + "\n")
            self._process.stdin.flush()
        except OSError:
            pass  # the process was killed while we were answering

    def _run_on_loop(self, coroutine: Awaitable[Any]) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return {"result": future.result(timeout=CALL_TIMEOUT_SECONDS)}
        except Exception as exc:  # the script sees any failure as an error
            future.cancel()
            return {"error": redact(f"{type(exc).__name__}: {exc}")}

    def _serve(self) -> None:
        process = self._process
        assert process is not None and process.stdin is not None and process.stdout is not None
        outcome: str | None = "The script ended without reporting a result."
        try:
            process.stdin.write(json.dumps(self._request) + "\n")
            process.stdin.flush()
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "output" in message:
                    self._emit({"kind": "output", "text": str(message["output"])})
                elif "call" in message:
                    call = message["call"]
                    self._reply(
                        self._run_on_loop(
                            self._on_call(call["handle"], call["method"], call["args"], call["kwargs"])
                        )
                    )
                elif "lease" in message:
                    self._reply(self._run_on_loop(self._on_lease(message["lease"])))
                elif "done" in message:
                    outcome = None
                elif "error" in message:
                    outcome = str(message["error"])
        except OSError:
            pass
        finally:
            process.wait()
            self._timer.cancel()
            if self._stop_reason is not None:
                outcome = self._stop_reason
            elif outcome is not None and process.returncode not in (0, None):
                stderr = (process.stderr.read() if process.stderr else "").strip()
                if stderr:
                    outcome = f"{outcome} {stderr.splitlines()[-1]}"
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            if outcome is not None:
                outcome = redact(outcome)
            asyncio.run_coroutine_threadsafe(self._on_finished(outcome), self._loop)


__all__ = ["ScriptProcess", "worker_environment"]
