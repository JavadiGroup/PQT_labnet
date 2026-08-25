from __future__ import annotations

import grpc
import pytest

from labnet.device_manager.errors import (
    DeviceDeadlineExceededError,
    DeviceNotFoundError,
    DeviceQueueFullError,
    DeviceTypeMismatchError,
    DeviceUnavailableError,
    InvalidDeviceArgumentError,
    LeaseInvalidError,
    LeaseValidationUnavailableError,
)
from labnet.grpc.status import (
    OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY,
    RETRYABLE_METADATA_KEY,
    abort_from_exception,
    map_exception,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LeaseInvalidError("invalid"), grpc.StatusCode.UNAUTHENTICATED),
        (
            LeaseValidationUnavailableError("central unavailable"),
            grpc.StatusCode.UNAVAILABLE,
        ),
        (DeviceNotFoundError("missing"), grpc.StatusCode.NOT_FOUND),
        (
            DeviceQueueFullError("full"),
            grpc.StatusCode.RESOURCE_EXHAUSTED,
        ),
        (
            DeviceTypeMismatchError("wrong service family"),
            grpc.StatusCode.FAILED_PRECONDITION,
        ),
        (
            DeviceDeadlineExceededError("late"),
            grpc.StatusCode.DEADLINE_EXCEEDED,
        ),
        (
            InvalidDeviceArgumentError("bad value"),
            grpc.StatusCode.INVALID_ARGUMENT,
        ),
        (
            DeviceUnavailableError("offline"),
            grpc.StatusCode.FAILED_PRECONDITION,
        ),
    ],
)
def test_transport_errors_have_canonical_statuses(error, expected) -> None:
    assert map_exception(error).status_code is expected


@pytest.mark.asyncio
async def test_deadline_reports_ambiguous_physical_completion() -> None:
    class RecordingContext:
        trailing_metadata: tuple[tuple[str, str], ...] = ()

        async def abort(self, _code, _message, *, trailing_metadata) -> None:
            self.trailing_metadata = trailing_metadata

    context = RecordingContext()
    error = DeviceDeadlineExceededError(
        "late",
        details={"operation_may_have_completed": True},
    )

    await abort_from_exception(context, error, operation_id="write-1")  # type: ignore[arg-type]

    metadata = dict(context.trailing_metadata)
    assert metadata[OPERATION_MAY_HAVE_COMPLETED_METADATA_KEY] == "true"
    assert metadata[RETRYABLE_METADATA_KEY] == "false"
