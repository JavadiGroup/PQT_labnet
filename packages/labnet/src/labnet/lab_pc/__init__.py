"""Lab-PC runtime: serve physical devices to the network (``labnet-lab-pc``).

Server dependencies are imported lazily so the base client install does not
need FastAPI or Uvicorn.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "LabPCSettings": ".config",
    "LabPCRuntime": ".app",
    "create_app": ".app",
    "load_device_factory": ".app",
    "main": ".app",
}
__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
