"""Local-or-remote device facades and physical implementations."""

from __future__ import annotations

from importlib import import_module
from typing import Any


# Public name -> family package that provides it.
_FAMILIES = {
    "DummyDevice": ".DummyDevice",
    "PhysicalDummyDevice": ".DummyDevice",
    "SimulatedWavemeter": ".SimulatedWavemeter",
    "PhysicalSimulatedWavemeter": ".SimulatedWavemeter",
}
__all__ = sorted(_FAMILIES)


def __getattr__(name: str) -> Any:
    if name not in _FAMILIES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_FAMILIES[name], __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
