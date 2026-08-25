"""Typed gRPC APIs with server-only dependencies loaded lazily."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .auth import LEASE_METADATA_KEY
from .status import (
    ERROR_CODE_METADATA_KEY,
    OPERATION_ID_METADATA_KEY,
    OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY,
    RETRYABLE_METADATA_KEY,
)


_SERVER_EXPORTS = {
    "GrpcServerConfig",
    "LabnetGrpcServer",
    "TLSConfig",
    "TLSMode",
}
_SERVICE_EXPORTS = {
    "PatternDeviceGrpcService",
    "StreamPolicy",
    "UnsupportedRpcPatternError",
}

__all__ = [
    "ERROR_CODE_METADATA_KEY",
    "GrpcServerConfig",
    "LEASE_METADATA_KEY",
    "LabnetGrpcServer",
    "OPERATION_ID_METADATA_KEY",
    "OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY",
    "PatternDeviceGrpcService",
    "RETRYABLE_METADATA_KEY",
    "StreamPolicy",
    "TLSConfig",
    "TLSMode",
    "UnsupportedRpcPatternError",
]


def __getattr__(name: str) -> Any:
    if name in _SERVER_EXPORTS:
        value = getattr(import_module(".server", __name__), name)
    elif name in _SERVICE_EXPORTS:
        value = getattr(import_module(".services", __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
