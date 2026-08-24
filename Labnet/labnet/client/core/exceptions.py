"""Stable client exceptions for Central HTTP and direct device RPC calls."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import grpc


class LabClientError(Exception):
    """Base exception for client-library failures."""


class _CentralResourceError(LabClientError):
    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        self.message = message
        self.error_code = error_code
        suffix = f" [{error_code}]" if error_code else ""
        super().__init__(f"{message}{suffix}")


class AuthenticationError(_CentralResourceError):
    """Raised when a Central API credential is missing or invalid."""


class PermissionDeniedError(_CentralResourceError):
    """Raised when the authenticated Central user lacks permission."""


class ResourceNotFoundError(_CentralResourceError):
    """Raised when a requested Central resource does not exist."""


class ResourceConflictError(_CentralResourceError):
    """Raised when a device is busy or another Central conflict occurs."""


class ServerConnectionError(LabClientError):
    """Raised when Central or a Lab PC cannot be reached."""


class ServerResponseError(LabClientError):
    """Raised for an unexpected Central Server response."""

    def __init__(self, status_code: int, message: str, *, error_code: str | None = None):
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        suffix = f" [{error_code}]" if error_code else ""
        super().__init__(f"Central Server returned HTTP {status_code}{suffix}: {message}")


class NoAvailableDeviceError(LabClientError):
    """Raised when no available device of the requested type exists."""


class LeaseAuthorityError(LabClientError):
    """Raised after automatic renewal can no longer preserve lease authority."""


class DeviceRpcError(LabClientError):
    """Base for canonical gRPC failures with Labnet metadata preserved."""

    def __init__(
        self,
        status_code: grpc.StatusCode,
        message: str,
        *,
        error_code: str | None = None,
        operation_id: str | None = None,
        retryable: bool | None = None,
        operation_may_have_completed: bool | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        self.operation_id = operation_id
        self.retryable = retryable
        self.operation_may_have_completed = operation_may_have_completed
        label = error_code or status_code.name
        super().__init__(f"Device RPC failed [{label}]: {message}")


class DeviceAuthenticationError(DeviceRpcError):
    pass


class DevicePermissionDeniedError(DeviceRpcError):
    pass


class DeviceNotFoundError(DeviceRpcError):
    pass


class InvalidDeviceArgumentError(DeviceRpcError):
    pass


class DeviceDeadlineExceededError(DeviceRpcError):
    pass


class DeviceUnavailableError(DeviceRpcError):
    pass


class DeviceResourceExhaustedError(DeviceRpcError):
    pass


class DeviceFailedPreconditionError(DeviceRpcError):
    pass


class DeviceCancelledError(DeviceRpcError):
    pass


class DeviceInternalError(DeviceRpcError):
    pass


class DeviceAbortedError(DeviceRpcError):
    pass


class DeviceAlreadyExistsError(DeviceRpcError):
    pass


class DeviceDataLossError(DeviceRpcError):
    pass


class DeviceOutOfRangeError(DeviceRpcError):
    pass


class DeviceUnimplementedError(DeviceRpcError):
    pass


_RPC_EXCEPTION_TYPES: dict[grpc.StatusCode, type[DeviceRpcError]] = {
    grpc.StatusCode.UNAUTHENTICATED: DeviceAuthenticationError,
    grpc.StatusCode.PERMISSION_DENIED: DevicePermissionDeniedError,
    grpc.StatusCode.NOT_FOUND: DeviceNotFoundError,
    grpc.StatusCode.INVALID_ARGUMENT: InvalidDeviceArgumentError,
    grpc.StatusCode.DEADLINE_EXCEEDED: DeviceDeadlineExceededError,
    grpc.StatusCode.UNAVAILABLE: DeviceUnavailableError,
    grpc.StatusCode.RESOURCE_EXHAUSTED: DeviceResourceExhaustedError,
    grpc.StatusCode.FAILED_PRECONDITION: DeviceFailedPreconditionError,
    grpc.StatusCode.CANCELLED: DeviceCancelledError,
    grpc.StatusCode.INTERNAL: DeviceInternalError,
    grpc.StatusCode.UNKNOWN: DeviceInternalError,
    grpc.StatusCode.ABORTED: DeviceAbortedError,
    grpc.StatusCode.ALREADY_EXISTS: DeviceAlreadyExistsError,
    grpc.StatusCode.DATA_LOSS: DeviceDataLossError,
    grpc.StatusCode.OUT_OF_RANGE: DeviceOutOfRangeError,
    grpc.StatusCode.UNIMPLEMENTED: DeviceUnimplementedError,
}


def _metadata_dict(items: Iterable[Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or ():
        if hasattr(item, "key"):
            key, value = item.key, item.value
        else:
            try:
                key, value = item
            except (TypeError, ValueError):
                continue
        if isinstance(key, bytes):
            key = key.decode("ascii", errors="replace")
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        result[str(key).lower()] = str(value)
    return result


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def translate_grpc_error(exc: grpc.RpcError) -> DeviceRpcError:
    """Convert sync or aio gRPC errors into stable Labnet exceptions."""

    code = exc.code()
    if not isinstance(code, grpc.StatusCode):
        code = grpc.StatusCode.UNKNOWN
    details = exc.details() or "The device RPC failed."
    metadata = _metadata_dict(exc.trailing_metadata())
    exception_type = _RPC_EXCEPTION_TYPES.get(code, DeviceRpcError)
    return exception_type(
        code,
        details,
        error_code=metadata.get("labnet-error-code"),
        operation_id=metadata.get("labnet-operation-id"),
        retryable=_optional_bool(metadata.get("labnet-retryable")),
        operation_may_have_completed=_optional_bool(
            metadata.get("labnet-operation-may-have-completed")
        ),
    )


__all__ = [
    "AuthenticationError",
    "DeviceAbortedError",
    "DeviceAlreadyExistsError",
    "DeviceAuthenticationError",
    "DeviceCancelledError",
    "DeviceDataLossError",
    "DeviceDeadlineExceededError",
    "DeviceFailedPreconditionError",
    "DeviceInternalError",
    "DeviceNotFoundError",
    "DeviceOutOfRangeError",
    "DevicePermissionDeniedError",
    "DeviceResourceExhaustedError",
    "DeviceRpcError",
    "DeviceUnavailableError",
    "DeviceUnimplementedError",
    "InvalidDeviceArgumentError",
    "LabClientError",
    "LeaseAuthorityError",
    "NoAvailableDeviceError",
    "PermissionDeniedError",
    "ResourceConflictError",
    "ResourceNotFoundError",
    "ServerConnectionError",
    "ServerResponseError",
    "translate_grpc_error",
]
