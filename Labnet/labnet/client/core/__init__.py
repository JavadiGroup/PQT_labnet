"""Core Central client with dotenv-based convenience helpers kept optional."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .lab_client import LabClient


__all__ = ["LabClient", "retrieve_client_info", "setup_client"]


def __getattr__(name: str) -> Any:
    if name not in {"retrieve_client_info", "setup_client"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".util", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
