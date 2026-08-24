"""Public package for Labnet device, Central, and gRPC client components."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_CLIENT_EXPORTS = {
    "AsyncDummyDeviceClient",
    "AsyncLabClient",
    "AuthenticatedUser",
    "AuthenticationError",
    "DeviceAbortedError",
    "DeviceAlreadyExistsError",
    "DeviceAuthenticationError",
    "DeviceCancelledError",
    "DeviceDataLossError",
    "DeviceDeadlineExceededError",
    "DeviceFailedPreconditionError",
    "DeviceInfo",
    "DeviceInternalError",
    "DeviceNotFoundError",
    "DeviceOutOfRangeError",
    "DevicePermissionDeniedError",
    "DeviceResourceExhaustedError",
    "DeviceRpcError",
    "DeviceUnavailableError",
    "DeviceUnimplementedError",
    "DummyDeviceGrpcClient",
    "EndpointDescriptor",
    "GrpcTlsConfig",
    "InvalidDeviceArgumentError",
    "LabClient",
    "LabClientError",
    "LeaseGrant",
    "LeaseInfo",
    "Measurement",
    "MeasurementQuality",
    "NoAvailableDeviceError",
    "PermissionDeniedError",
    "ResourceConflictError",
    "ResourceNotFoundError",
    "ServerConnectionError",
    "ServerResponseError",
}
_DEVICE_EXPORTS = {
    "DummyDevice": ".devices.DummyDevice.device",
    "DummyDeviceInterface": ".devices.DummyDevice.interface",
    "PhysicalDummyDevice": ".devices.DummyDevice.physical",
    "RemoteDummyDevice": ".devices.DummyDevice.remote",
}
__all__ = sorted(_CLIENT_EXPORTS | set(_DEVICE_EXPORTS))


def __getattr__(name: str) -> Any:
    if name in _CLIENT_EXPORTS:
        core = import_module(".client.core", __name__)
        if hasattr(core, name):
            value = getattr(core, name)
        else:
            value = getattr(import_module(".client.core.exceptions", __name__), name)
    elif name in _DEVICE_EXPORTS:
        value = getattr(import_module(_DEVICE_EXPORTS[name], __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
