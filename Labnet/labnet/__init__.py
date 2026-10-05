"""Public package for Labnet device, Central, and gRPC client components."""

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
_DEVICE_EXPORTS = {
    "CALIBRATIONS": ".devices.SacherLaser.calibration",
    "DEFAULT_DLL_PATH": ".devices.SacherLaser.MaxonEpos2_class",
    "DummyDevice": ".devices.DummyDevice.device",
    "EposCommandError": ".devices.SacherLaser.MaxonEpos2_class",
    "IR_CALIBRATION": ".devices.SacherLaser.calibration",
    "LaserCalibration": ".devices.SacherLaser.calibration",
    "MaxonEpos2": ".devices.SacherLaser.MaxonEpos2_class",
    "MovementTimeoutError": ".devices.SacherLaser.MaxonEpos2_class",
    "PhysicalDummyDevice": ".devices.DummyDevice.physical",
    "SimulatedWavemeter": ".devices.SimulatedWavemeter.device",
    "PhysicalSimulatedWavemeter": ".devices.SimulatedWavemeter.physical",
    "PhysicalSacherLaser": ".devices.SacherLaser.physical",
    "SacherLaser": ".devices.SacherLaser.device",
    "SacherLaserError": ".devices.SacherLaser.MaxonEpos2_class",
    "VIS_CALIBRATION": ".devices.SacherLaser.calibration",
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
