"""Reusable Lab PC device lifecycle and transport-neutral execution core."""

from .catalog import DeviceCatalog
from .central import CentralRegistryClient, HttpCentralRegistryClient
from .errors import (
    ArgumentOutsideLimitsError,
    CentralCommunicationError,
    DeviceDeadlineExceededError,
    DeviceManagerError,
    DeviceNotFoundError,
    DeviceOperationError,
    DeviceQueueFullError,
    DeviceShutdownError,
    DeviceStartupError,
    DeviceTypeMismatchError,
    DeviceUnavailableError,
    DuplicateDeviceIdError,
    InvalidConfigurationError,
    InvalidDeviceArgumentError,
    InvalidDeviceLimitsError,
    LeaseError,
    LeaseInvalidError,
    LeaseValidationUnavailableError,
    ManagerClosedError,
    MethodNotAllowedError,
)
from .executor import AuthorizedDeviceSession, DeviceExecutor
from .leases import (
    DevelopmentLeaseValidator,
    HttpLeaseValidator,
    LeaseGrant,
    LeaseValidator,
    StaticLeaseValidator,
)
from .manager import DeviceManager
from .models import (
    DeviceAvailability,
    DeviceSnapshot,
    DeviceState,
    EndpointDescriptor,
    FailureInfo,
    ManagerState,
    StartupReport,
)
from .security import validate_central_server_url
from .worker import DeviceWorker

__all__ = [
    "ArgumentOutsideLimitsError",
    "AuthorizedDeviceSession",
    "CentralCommunicationError",
    "CentralRegistryClient",
    "DevelopmentLeaseValidator",
    "DeviceCatalog",
    "DeviceDeadlineExceededError",
    "DeviceAvailability",
    "DeviceExecutor",
    "DeviceManager",
    "DeviceManagerError",
    "DeviceNotFoundError",
    "DeviceOperationError",
    "DeviceQueueFullError",
    "DeviceShutdownError",
    "DeviceSnapshot",
    "DeviceStartupError",
    "DeviceState",
    "DeviceTypeMismatchError",
    "DeviceUnavailableError",
    "DeviceWorker",
    "DuplicateDeviceIdError",
    "EndpointDescriptor",
    "FailureInfo",
    "HttpCentralRegistryClient",
    "HttpLeaseValidator",
    "InvalidConfigurationError",
    "InvalidDeviceArgumentError",
    "InvalidDeviceLimitsError",
    "LeaseError",
    "LeaseGrant",
    "LeaseInvalidError",
    "LeaseValidationUnavailableError",
    "LeaseValidator",
    "ManagerClosedError",
    "ManagerState",
    "MethodNotAllowedError",
    "StartupReport",
    "StaticLeaseValidator",
    "validate_central_server_url",
]
