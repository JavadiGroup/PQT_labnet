from __future__ import annotations

from types import MappingProxyType
from typing import Any, ClassVar, Mapping


class DeviceManagerError(Exception):
    """Base class for stable, transport-neutral device-manager failures."""

    code: ClassVar[str] = "DEVICE_MANAGER_ERROR"
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.details = MappingProxyType(dict(details or {}))
        super().__init__(message)


class InvalidConfigurationError(DeviceManagerError):
    code = "INVALID_CONFIGURATION"


class DuplicateDeviceIdError(InvalidConfigurationError):
    code = "DUPLICATE_DEVICE_ID"


class DeviceNotFoundError(DeviceManagerError):
    code = "DEVICE_NOT_FOUND"


class DeviceTypeMismatchError(DeviceManagerError):
    """The leased device does not implement the requested service family."""

    code = "DEVICE_TYPE_MISMATCH"


class DeviceUnavailableError(DeviceManagerError):
    code = "DEVICE_UNAVAILABLE"
    retryable = True


class DeviceStartupError(DeviceUnavailableError):
    code = "DEVICE_STARTUP_FAILED"


class DeviceShutdownError(DeviceManagerError):
    code = "DEVICE_SHUTDOWN_FAILED"


class DeviceQueueFullError(DeviceUnavailableError):
    code = "DEVICE_QUEUE_FULL"


class DeviceDeadlineExceededError(DeviceManagerError):
    code = "DEVICE_DEADLINE_EXCEEDED"


class MethodNotAllowedError(DeviceManagerError):
    code = "METHOD_NOT_ALLOWED"


class InvalidDeviceArgumentError(DeviceManagerError):
    """May be raised by a physical wrapper for stable argument failures."""

    code = "INVALID_DEVICE_ARGUMENT"


class DeviceOperationError(DeviceManagerError):
    code = "DEVICE_OPERATION_FAILED"


class ManagerClosedError(DeviceManagerError):
    code = "DEVICE_MANAGER_CLOSED"


class LeaseError(DeviceManagerError):
    code = "LEASE_ERROR"


class LeaseInvalidError(LeaseError):
    code = "LEASE_INVALID"


class LeaseValidationUnavailableError(LeaseError):
    code = "LEASE_VALIDATION_UNAVAILABLE"
    retryable = True


class CentralCommunicationError(DeviceManagerError):
    code = "CENTRAL_SERVER_UNAVAILABLE"
    retryable = True


__all__ = [
    "CentralCommunicationError",
    "DeviceDeadlineExceededError",
    "DeviceManagerError",
    "DeviceNotFoundError",
    "DeviceOperationError",
    "DeviceQueueFullError",
    "DeviceShutdownError",
    "DeviceStartupError",
    "DeviceTypeMismatchError",
    "DeviceUnavailableError",
    "DuplicateDeviceIdError",
    "InvalidConfigurationError",
    "InvalidDeviceArgumentError",
    "LeaseError",
    "LeaseInvalidError",
    "LeaseValidationUnavailableError",
    "ManagerClosedError",
    "MethodNotAllowedError",
]
