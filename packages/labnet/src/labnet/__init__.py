"""LabNet: device control for a lab network. The client API is exported here;
device classes come from driver packages (for example ``labnet_sim``)."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_CLIENT_EXPORTS = {
    "AsyncContractDeviceClient",
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
    "LabPCInfo",
    "DeviceInternalError",
    "DeviceNotFoundError",
    "DeviceOutOfRangeError",
    "DevicePermissionDeniedError",
    "DeviceResourceExhaustedError",
    "DeviceRpcError",
    "DeviceUnavailableError",
    "DeviceUnimplementedError",
    "EndpointDescriptor",
    "GrpcTlsConfig",
    "InvalidDeviceArgumentError",
    "LabClient",
    "LabClientError",
    "LeaseAuthorityError",
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
__all__ = sorted(_CLIENT_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _CLIENT_EXPORTS:
        core = import_module(".client.core", __name__)
        if hasattr(core, name):
            value = getattr(core, name)
        else:
            value = getattr(import_module(".client.core.exceptions", __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
