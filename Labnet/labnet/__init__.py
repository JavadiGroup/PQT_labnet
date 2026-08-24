"""Public package for Labnet device and network-control components.

Legacy client exports remain available lazily while the client migration is
pending. Importing a Lab-PC server module therefore does not load the retired
WebSocket client transport.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_CLIENT_EXPORTS = {
    "AuthenticationError",
    "LabClient",
    "LabClientError",
    "NoAvailableDeviceError",
    "PermissionDeniedError",
    "ResourceConflictError",
    "ResourceNotFoundError",
    "ServerConnectionError",
    "ServerResponseError",
    "retrieve_client_info",
    "setup_client",
}
_DEVICE_EXPORTS = {
    "DummyDevice": ".devices.DummyDevice.device",
    "DummyDeviceInterface": ".devices.DummyDevice.interface",
    "PhysicalDummyDevice": ".devices.DummyDevice.physical",
    "RemoteDummyDevice": ".devices.DummyDevice.remote",
}
_RPC_EXPORTS = {"RpcConnectionClosedError", "RpcError"}

__all__ = sorted(_CLIENT_EXPORTS | set(_DEVICE_EXPORTS) | _RPC_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _CLIENT_EXPORTS:
        core = import_module(".client.core", __name__)
        if hasattr(core, name):
            value = getattr(core, name)
        else:
            value = getattr(import_module(".client.core.exceptions", __name__), name)
    elif name in _DEVICE_EXPORTS:
        value = getattr(import_module(_DEVICE_EXPORTS[name], __name__), name)
    elif name in _RPC_EXPORTS:
        value = getattr(import_module(".devices.core.rpc", __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
