"""Stable conversion from transport-neutral failures to gRPC status codes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import NoReturn

import grpc

from labnet.device_manager.errors import (
    CentralCommunicationError,
    DeviceDeadlineExceededError,
    DeviceManagerError,
    DeviceNotFoundError,
    DeviceOperationError,
    DeviceQueueFullError,
    DeviceTypeMismatchError,
    DeviceUnavailableError,
    InvalidConfigurationError,
    InvalidDeviceArgumentError,
    LeaseInvalidError,
    LeaseValidationUnavailableError,
    ManagerClosedError,
    MethodNotAllowedError,
)

from .auth import LeaseMetadataError


LOGGER = logging.getLogger(__name__)
ERROR_CODE_METADATA_KEY = "labnet-error-code"
OPERATION_ID_METADATA_KEY = "labnet-operation-id"
OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY = (
    "labnet-operation-may-have-completed"
)
RETRYABLE_METADATA_KEY = "labnet-retryable"


class GrpcServiceError(Exception):
    """A deliberate, safe-to-return failure raised by the gRPC adapter."""

    def __init__(
        self,
        status_code: grpc.StatusCode,
        error_code: str,
        message: str,
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class MappedStatus:
    status_code: grpc.StatusCode
    error_code: str
    message: str
    log_exception: bool = False


def map_exception(exc: BaseException) -> MappedStatus:
    """Map a server-side exception without exposing an implementation traceback."""

    if isinstance(exc, GrpcServiceError):
        return MappedStatus(exc.status_code, exc.error_code, exc.message)
    if isinstance(exc, LeaseMetadataError):
        return MappedStatus(
            grpc.StatusCode.UNAUTHENTICATED,
            exc.code,
            str(exc),
        )
    if isinstance(exc, LeaseInvalidError):
        return _manager_status(grpc.StatusCode.UNAUTHENTICATED, exc)
    if isinstance(exc, (LeaseValidationUnavailableError, CentralCommunicationError)):
        return _manager_status(grpc.StatusCode.UNAVAILABLE, exc)
    if isinstance(exc, DeviceNotFoundError):
        return _manager_status(grpc.StatusCode.NOT_FOUND, exc)
    if isinstance(exc, DeviceQueueFullError):
        return _manager_status(grpc.StatusCode.RESOURCE_EXHAUSTED, exc)
    if isinstance(exc, DeviceTypeMismatchError):
        return _manager_status(grpc.StatusCode.FAILED_PRECONDITION, exc)
    if isinstance(exc, DeviceDeadlineExceededError):
        return _manager_status(grpc.StatusCode.DEADLINE_EXCEEDED, exc)
    if isinstance(exc, InvalidDeviceArgumentError):
        return _manager_status(grpc.StatusCode.INVALID_ARGUMENT, exc)
    if isinstance(exc, MethodNotAllowedError):
        return _manager_status(grpc.StatusCode.PERMISSION_DENIED, exc)
    if isinstance(exc, ManagerClosedError):
        return _manager_status(grpc.StatusCode.UNAVAILABLE, exc)
    if isinstance(exc, DeviceUnavailableError):
        return _manager_status(grpc.StatusCode.FAILED_PRECONDITION, exc)
    if isinstance(exc, DeviceOperationError):
        return MappedStatus(
            grpc.StatusCode.UNKNOWN,
            exc.code,
            exc.message,
            log_exception=True,
        )
    if isinstance(exc, InvalidConfigurationError):
        return MappedStatus(
            grpc.StatusCode.INTERNAL,
            exc.code,
            "The Lab PC is not configured correctly.",
            log_exception=True,
        )
    if isinstance(exc, asyncio.TimeoutError):
        return MappedStatus(
            grpc.StatusCode.DEADLINE_EXCEEDED,
            "DEADLINE_EXCEEDED",
            "The operation deadline was exceeded.",
        )
    if isinstance(exc, ValueError):
        return MappedStatus(
            grpc.StatusCode.INVALID_ARGUMENT,
            "INVALID_ARGUMENT",
            str(exc),
        )
    if isinstance(exc, DeviceManagerError):
        return MappedStatus(
            grpc.StatusCode.UNKNOWN,
            exc.code,
            exc.message,
            log_exception=True,
        )

    return MappedStatus(
        grpc.StatusCode.INTERNAL,
        "INTERNAL",
        "An unexpected server error occurred.",
        log_exception=True,
    )


async def abort_from_exception(
    context: grpc.aio.ServicerContext,
    exc: BaseException,
    *,
    operation_id: str | None = None,
) -> NoReturn:
    """Abort a call using its canonical status and stable trailing metadata."""

    mapped = map_exception(exc)
    if mapped.log_exception:
        LOGGER.exception(
            "gRPC operation failed (operation_id=%s, error_code=%s)",
            operation_id or "<missing>",
            mapped.error_code,
            exc_info=exc,
        )

    trailing_metadata: list[tuple[str, str]] = [
        (ERROR_CODE_METADATA_KEY, mapped.error_code),
    ]
    if operation_id:
        trailing_metadata.append((OPERATION_ID_METADATA_KEY, operation_id))
    if isinstance(exc, DeviceManagerError):
        trailing_metadata.append(
            (RETRYABLE_METADATA_KEY, "true" if exc.retryable else "false")
        )
    if isinstance(exc, DeviceDeadlineExceededError):
        may_have_completed = exc.details.get("operation_may_have_completed")
        if isinstance(may_have_completed, bool):
            trailing_metadata.append(
                (
                    OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY,
                    "true" if may_have_completed else "false",
                )
            )

    await context.abort(
        mapped.status_code,
        mapped.message,
        trailing_metadata=tuple(trailing_metadata),
    )


def _manager_status(
    status_code: grpc.StatusCode,
    exc: DeviceManagerError,
) -> MappedStatus:
    return MappedStatus(status_code, exc.code, exc.message)


__all__ = [
    "ERROR_CODE_METADATA_KEY",
    "GrpcServiceError",
    "MappedStatus",
    "OPERATION_ID_METADATA_KEY",
    "OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY",
    "RETRYABLE_METADATA_KEY",
    "abort_from_exception",
    "map_exception",
]
