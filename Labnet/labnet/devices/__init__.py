"""Device interfaces with lazy legacy-client exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "DummyDevice",
    "DummyDeviceInterface",
    "PhysicalDummyDevice",
    "RemoteDummyDevice",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".DummyDevice", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
