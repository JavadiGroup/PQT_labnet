"""Run one device script in its own process (started by the Control Center).

The parent sends one JSON line on stdin:

    {"code": "...", "devices": {"device": {"label": "dev02-wavemeter", "methods": [...]}}}

and the worker answers with JSON lines on stdout:

    {"output": "text"}                                    print()
    {"call": {"handle", "method", "args", "kwargs"}}      a device call; the
    {"lease": "device-id"}                                parent replies on stdin
    {"done": true}  or  {"error": "Line 3: ..."}          the script finished

This process holds no credentials: every device call is performed by the
parent with its lease. The parent kills it on Stop or at the time limit.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import traceback
from typing import Any

from labnet.control_center.scripting import SCRIPT_FUNCTIONS, ScriptError, validate_script


MAX_SLEEP_SECONDS = 3600.0


class ScriptRuntimeError(RuntimeError):
    """A device call or lease failed; shown to the user as the script's error."""


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _request(message: dict[str, Any]) -> Any:
    _send(message)
    line = sys.stdin.readline()
    if not line:  # the Control Center went away
        raise SystemExit(1)
    reply = json.loads(line)
    if "error" in reply:
        raise ScriptRuntimeError(reply["error"])
    return reply["result"]


class DeviceHandle:
    """What a script sees as ``device`` or ``lease("...")``: contract methods only."""

    def __init__(self, handle: str, label: str, methods: list[str]) -> None:
        object.__setattr__(self, "_handle", handle)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_methods", frozenset(methods))

    def __getattr__(self, name: str) -> Any:
        if name not in self._methods:
            raise AttributeError(f"{self._label} has no method {name!r}")

        def call(*args: Any, **kwargs: Any) -> Any:
            return _request(
                {"call": {"handle": self._handle, "method": name, "args": list(args), "kwargs": kwargs}}
            )

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Device handles are read-only.")

    def __repr__(self) -> str:
        return f"<device {self._label}>"


def _namespace(devices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def script_print(*values: Any, sep: str = " ") -> None:
        _send({"output": str(sep).join(str(value) for value in values)})

    def sleep(seconds: float) -> None:
        seconds = float(seconds)
        if not 0 <= seconds <= MAX_SLEEP_SECONDS:
            raise ValueError(f"sleep() accepts 0 to {MAX_SLEEP_SECONDS:g} seconds.")
        time.sleep(seconds)

    def lease(device_id: str) -> DeviceHandle:
        granted = _request({"lease": str(device_id)})
        return DeviceHandle(granted["handle"], str(device_id), granted["methods"])

    functions: dict[str, Any] = {
        "abs": abs, "bool": bool, "enumerate": enumerate, "float": float,
        "int": int, "len": len, "lease": lease, "list": list, "max": max,
        "mean": statistics.fmean, "min": min, "print": script_print,
        "range": range, "round": round, "sleep": sleep, "stdev": statistics.stdev,
        "str": str, "sum": sum, "zip": zip,
    }
    assert set(functions) == SCRIPT_FUNCTIONS
    namespace: dict[str, Any] = {"__builtins__": {}}
    namespace.update(functions)
    for name, spec in devices.items():
        namespace[name] = DeviceHandle(name, spec["label"], spec["methods"])
    return namespace


def _error_line(exc: BaseException) -> str:
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == "<script>":
            line = frame.lineno
    message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, ScriptRuntimeError) else str(exc)
    return f"Line {line}: {message}" if line else message


def main() -> None:
    request = json.loads(sys.stdin.readline())
    devices = request["devices"]
    method_names = {method for spec in devices.values() for method in spec["methods"]}
    method_names |= set(request.get("lease_methods", []))
    try:
        tree = validate_script(request["code"], method_names)
        code = compile(tree, "<script>", "exec")
        exec(code, _namespace(devices))  # noqa: S102 - validated, builtins removed
    except ScriptError as exc:
        _send({"error": str(exc)})
    except Exception as exc:  # report every script failure with its line
        _send({"error": _error_line(exc)})
    else:
        _send({"done": True})


if __name__ == "__main__":
    main()
