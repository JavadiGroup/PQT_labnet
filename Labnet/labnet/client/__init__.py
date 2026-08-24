"""Client-side Central Server APIs with optional helpers loaded lazily."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = ["LabClient", "retrieve_client_info", "setup_client"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".core", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
