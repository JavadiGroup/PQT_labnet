"""Local-or-remote device facades and physical implementations."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "DummyDevice": ".DummyDevice",
    "PhysicalDummyDevice": ".DummyDevice",
    "DummyAOM": ".DummyAOM",
    "PhysicalDummyAOM": ".DummyAOM",
    "DummyAWG": ".DummyAWG",
    "PhysicalDummyAWG": ".DummyAWG",
    "DummyEOM": ".DummyEOM",
    "PhysicalDummyEOM": ".DummyEOM",
    "DummyLaser": ".DummyLaser",
    "PhysicalDummyLaser": ".DummyLaser",
    "SimulatedTunableLaser": ".SimulatedTunableLaser",
    "PhysicalSimulatedTunableLaser": ".SimulatedTunableLaser",
    "SimulatedWavemeter": ".SimulatedWavemeter",
    "PhysicalSimulatedWavemeter": ".SimulatedWavemeter",
}
__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
