"""Test helpers for LabNet code and device drivers.

* :func:`check_driver` / :func:`acheck_driver`: would a Lab PC accept this
  device? (contract, methods, lifecycle, metadata, hints and site limits)
* :func:`local_network`: a real Central and Lab PC in this process
  (needs ``labnet[central,lab-pc]``).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "DriverCheckError": ".drivers",
    "acheck_driver": ".drivers",
    "check_driver": ".drivers",
    "LocalNetwork": ".network",
    "local_network": ".network",
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
