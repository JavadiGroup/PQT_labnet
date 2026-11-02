"""LabNet drivers for the PQT lab's instruments, wrapping PQT_devices.

``Ws7Wavemeter`` and friends are the facades a client uses (locally, or
remotely through a lease); ``PhysicalWs7Wavemeter`` and friends are the
drivers a Lab PC inventory creates. Both are imported only when first used.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_FAMILIES = {
    "HoribaSpectrometer": "horiba_spectrometer",
    "SacherLaser": "sacher_laser",
    "Ws7Wavemeter": "ws7_wavemeter",
}
_EXPORTS = {
    **{name: "._generated.facades" for name in _FAMILIES},
    **{f"Physical{name}": f".{module}.physical" for name, module in _FAMILIES.items()},
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
