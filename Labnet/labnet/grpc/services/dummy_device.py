"""Typed gRPC facade for scalar DummyDevice control and acquisition."""

from __future__ import annotations

import asyncio
import math
import numbers
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import grpc

from labnet.identifiers import LABNET_IDENTIFIER_REGEX, validate_labnet_identifier

from ..auth import extract_lease_id
from ..generated import common_pb2, dummy_device_pb2, dummy_device_pb2_grpc
from ..generated import transfer_pb2
from ..status import GrpcServiceError, abort_from_exception


_MAX_CHANNEL_LENGTH = 128
_MAX_UNIT_LENGTH = 32
_MAX_STATUS_LENGTH = 64


class AuthorizedDeviceSessionProtocol(Protocol):
    async def execute(
        self,
        method: str,
        args: tuple[Any, ...] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any: ...

    async def revalidate(self, *, deadline: float | None = None) -> Any: ...


class DeviceExecutorProtocol(Protocol):
    async def execute(
        self,
        *,
        lease_id: str,
        device_id: str,
        method: str,
        args: tuple[Any, ...] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
        operation_id: str | None = None,
    ) -> Any: ...

    async def authorize(
        self,
        *,
        lease_id: str,
        device_id: str,
        operation_id: str | None = None,
        deadline: float | None = None,
    ) -> AuthorizedDeviceSessionProtocol: ...


@dataclass(frozen=True, slots=True)
class StreamPolicy:
    """Resource and timing limits for feedback-oriented scalar streams."""

    minimum_sample_rate_hz: float = 0.1
    maximum_sample_rate_hz: float = 1_000.0
    maximum_sample_count: int = 10_000_000
    maximum_duration_seconds: float = 86_400.0
    lease_revalidation_interval_seconds: float = 5.0
    sample_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum_sample_rate_hz):
            raise ValueError("minimum_sample_rate_hz must be finite.")
        if not math.isfinite(self.maximum_sample_rate_hz):
            raise ValueError("maximum_sample_rate_hz must be finite.")
        if self.minimum_sample_rate_hz <= 0:
            raise ValueError("minimum_sample_rate_hz must be positive.")
        if self.maximum_sample_rate_hz < self.minimum_sample_rate_hz:
            raise ValueError(
                "maximum_sample_rate_hz must be at least minimum_sample_rate_hz."
            )
        if self.maximum_sample_count <= 0:
            raise ValueError("maximum_sample_count must be positive.")
        if (
            not math.isfinite(self.maximum_duration_seconds)
            or self.maximum_duration_seconds <= 0
        ):
            raise ValueError(
                "maximum_duration_seconds must be finite and positive."
            )
        if (
            not math.isfinite(self.lease_revalidation_interval_seconds)
            or self.lease_revalidation_interval_seconds <= 0
        ):
            raise ValueError(
                "lease_revalidation_interval_seconds must be finite and positive."
            )
        if (
            not math.isfinite(self.sample_timeout_seconds)
            or self.sample_timeout_seconds <= 0
        ):
            raise ValueError("sample_timeout_seconds must be finite and positive.")


@dataclass(frozen=True, slots=True)
class _StreamSpec:
    sample_rate_hz: float
    sample_count: int | None
    duration_seconds: float | None
    channel: str


class DummyDeviceGrpcService(
    dummy_device_pb2_grpc.DummyDeviceServiceServicer
):
    """gRPC adapter over the transport-neutral per-device executor.

    The executor owns hardware ordering and method allow-listing. This class
    owns transport validation, lease metadata extraction, stream scheduling,
    and protobuf conversion.
    """

    SERVICE_NAME = "labnet.devices.v1.DummyDeviceService"

    def __init__(
        self,
        executor: DeviceExecutorProtocol,
        *,
        stream_policy: StreamPolicy | None = None,
        default_unit: str = "arb",
        default_operation_timeout_seconds: float = 30.0,
    ) -> None:
        if (
            not math.isfinite(default_operation_timeout_seconds)
            or default_operation_timeout_seconds <= 0
        ):
            raise ValueError(
                "default_operation_timeout_seconds must be finite and positive."
            )
        self._executor = executor
        self._stream_policy = stream_policy or StreamPolicy()
        self._default_unit = _validate_unit(default_unit, field_name="default_unit")
        self._default_operation_timeout_seconds = (
            default_operation_timeout_seconds
        )
        self._active_streams: set[str] = set()
        self._active_streams_lock = asyncio.Lock()

    async def ReadValue(  # noqa: N802 - method name is defined by protobuf
        self,
        request: dummy_device_pb2.ReadValueRequest,
        context: grpc.aio.ServicerContext,
    ) -> dummy_device_pb2.ReadValueResponse:
        operation_id = _safe_operation_id(request.operation_id)
        try:
            lease_id = extract_lease_id(context)
            device_id = _validate_identifier(request.device_id, "device_id")
            operation_id = _validate_identifier(request.operation_id, "operation_id")
            channel = _optional_channel(request)
            result = await self._executor.execute(
                lease_id=lease_id,
                device_id=device_id,
                method="read_value",
                kwargs=_channel_kwargs(channel),
                deadline=_unary_deadline(
                    context,
                    self._default_operation_timeout_seconds,
                ),
                operation_id=operation_id,
            )
            measurement = _measurement_from_result(
                result,
                sequence=0,
                requested_channel=channel,
                default_unit=self._default_unit,
            )
            return dummy_device_pb2.ReadValueResponse(
                operation_id=operation_id,
                measurement=measurement,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await abort_from_exception(
                context,
                exc,
                operation_id=operation_id,
            )

    async def WriteValue(  # noqa: N802 - method name is defined by protobuf
        self,
        request: dummy_device_pb2.WriteValueRequest,
        context: grpc.aio.ServicerContext,
    ) -> dummy_device_pb2.WriteValueResponse:
        operation_id = _safe_operation_id(request.operation_id)
        try:
            lease_id = extract_lease_id(context)
            device_id = _validate_identifier(request.device_id, "device_id")
            operation_id = _validate_identifier(request.operation_id, "operation_id")
            channel = _optional_channel(request)
            value = _validate_write_value(request.value)
            unit = (
                _validate_unit(request.unit, field_name="unit")
                if request.HasField("unit")
                else self._default_unit
            )
            if unit != self._default_unit:
                raise ValueError(
                    f"unit must be {self._default_unit!r}; this service does not "
                    "perform implicit unit conversion."
                )

            result = await self._executor.execute(
                lease_id=lease_id,
                device_id=device_id,
                method="write_value",
                args=(value,),
                kwargs=_channel_kwargs(channel),
                deadline=_unary_deadline(
                    context,
                    self._default_operation_timeout_seconds,
                ),
                operation_id=operation_id,
            )
            measurement = _measurement_from_result(
                value if result is None else result,
                sequence=0,
                requested_channel=channel,
                default_unit=unit,
                default_status="WRITTEN",
            )
            return dummy_device_pb2.WriteValueResponse(
                operation_id=operation_id,
                measurement=measurement,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await abort_from_exception(
                context,
                exc,
                operation_id=operation_id,
            )

    async def StreamData(  # noqa: N802 - method name is defined by protobuf
        self,
        request: transfer_pb2.StreamDataRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[transfer_pb2.StreamDataResponse]:
        operation_id = _safe_operation_id(request.operation_id)
        try:
            lease_id = extract_lease_id(context)
            device_id = _validate_identifier(request.device_id, "device_id")
            operation_id = _validate_identifier(request.operation_id, "operation_id")
            spec = _validate_stream_request(request, self._stream_policy)

            session = await self._executor.authorize(
                lease_id=lease_id,
                device_id=device_id,
                operation_id=operation_id,
                deadline=_unary_deadline(
                    context,
                    self._default_operation_timeout_seconds,
                ),
            )

            async with self._claim_stream(device_id):
                loop = asyncio.get_running_loop()
                started_at = loop.time()
                next_sample_at = started_at
                next_revalidation_at = (
                    started_at
                    + self._stream_policy.lease_revalidation_interval_seconds
                )
                period = 1.0 / spec.sample_rate_hz
                sequence = 0

                while True:
                    if spec.sample_count is not None and sequence >= spec.sample_count:
                        break
                    if context.cancelled() or context.done():
                        break

                    now = loop.time()
                    if (
                        sequence > 0
                        and spec.duration_seconds is not None
                        and now - started_at >= spec.duration_seconds
                    ):
                        break
                    if now < next_sample_at:
                        await asyncio.sleep(next_sample_at - now)

                    now = loop.time()
                    if (
                        sequence > 0
                        and spec.duration_seconds is not None
                        and now - started_at >= spec.duration_seconds
                    ):
                        break

                    if now >= next_revalidation_at:
                        await session.revalidate(
                            deadline=_sample_deadline(
                                context,
                                self._stream_policy,
                            )
                        )
                        next_revalidation_at = (
                            loop.time()
                            + self._stream_policy.lease_revalidation_interval_seconds
                        )

                    result = await session.execute(
                        "read_value",
                        kwargs=_channel_kwargs(spec.channel),
                        deadline=_sample_deadline(context, self._stream_policy),
                    )
                    measurement = _measurement_from_result(
                        result,
                        sequence=sequence,
                        requested_channel=spec.channel,
                        default_unit=self._default_unit,
                    )
                    yield transfer_pb2.StreamDataResponse(
                        operation_id=operation_id,
                        measurement=measurement,
                    )

                    sequence += 1
                    # Do not send a catch-up burst after a slow hardware read.
                    next_sample_at = max(next_sample_at + period, loop.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await abort_from_exception(
                context,
                exc,
                operation_id=operation_id,
            )

    @asynccontextmanager
    async def _claim_stream(self, device_id: str) -> AsyncIterator[None]:
        async with self._active_streams_lock:
            if device_id in self._active_streams:
                raise GrpcServiceError(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    "STREAM_ALREADY_ACTIVE",
                    f"A data stream is already active for device {device_id}.",
                )
            self._active_streams.add(device_id)

        try:
            yield
        finally:
            async with self._active_streams_lock:
                self._active_streams.discard(device_id)


def _validate_stream_request(
    request: transfer_pb2.StreamDataRequest,
    policy: StreamPolicy,
) -> _StreamSpec:
    sample_rate_hz = float(request.sample_rate_hz)
    if not math.isfinite(sample_rate_hz):
        raise ValueError("sample_rate_hz must be finite.")
    if not (
        policy.minimum_sample_rate_hz
        <= sample_rate_hz
        <= policy.maximum_sample_rate_hz
    ):
        raise ValueError(
            "sample_rate_hz must be between "
            f"{policy.minimum_sample_rate_hz:g} and "
            f"{policy.maximum_sample_rate_hz:g}."
        )

    sample_count: int | None = None
    if request.HasField("sample_count"):
        sample_count = int(request.sample_count)
        if sample_count <= 0:
            raise ValueError("sample_count must be positive when supplied.")
        if sample_count > policy.maximum_sample_count:
            raise ValueError(
                f"sample_count cannot exceed {policy.maximum_sample_count}."
            )

    duration_seconds: float | None = None
    if request.HasField("duration"):
        if request.duration.seconds < 0 or not (
            0 <= request.duration.nanos < 1_000_000_000
        ):
            raise ValueError(
                "duration must use non-negative normalized seconds and nanoseconds."
            )
        duration_seconds = (
            float(request.duration.seconds)
            + float(request.duration.nanos) / 1_000_000_000.0
        )
        if duration_seconds <= 0:
            raise ValueError("duration must be positive when supplied.")
        if duration_seconds > policy.maximum_duration_seconds:
            raise ValueError(
                "duration cannot exceed "
                f"{policy.maximum_duration_seconds:g} seconds."
            )
        estimated_samples = math.ceil(sample_rate_hz * duration_seconds)
        if sample_count is None and estimated_samples > policy.maximum_sample_count:
            raise ValueError(
                "The requested rate and duration exceed the server's maximum "
                f"of {policy.maximum_sample_count} samples."
            )

    return _StreamSpec(
        sample_rate_hz=sample_rate_hz,
        sample_count=sample_count,
        duration_seconds=duration_seconds,
        channel=_optional_channel(request),
    )


def _measurement_from_result(
    result: Any,
    *,
    sequence: int,
    requested_channel: str,
    default_unit: str,
    default_status: str = "OK",
) -> common_pb2.ScalarMeasurement:
    if isinstance(result, bool):
        raise GrpcServiceError(
            grpc.StatusCode.INTERNAL,
            "INVALID_DEVICE_RESPONSE",
            "The device returned a non-numeric value.",
        )

    if isinstance(result, numbers.Real):
        raw_value = result
        raw_unit: Any = default_unit
        raw_quality: Any = common_pb2.MEASUREMENT_QUALITY_GOOD
        raw_status: Any = default_status
        raw_timestamp_ns: Any = time.time_ns()
        raw_channel: Any = requested_channel
    else:
        raw_value = _result_field(result, "value", None)
        raw_unit = _result_field(result, "unit", default_unit)
        raw_quality = _result_field(
            result,
            "quality",
            common_pb2.MEASUREMENT_QUALITY_GOOD,
        )
        raw_status = _result_field(result, "status", default_status)
        raw_timestamp_ns = _result_field(result, "timestamp_ns", time.time_ns())
        raw_channel = requested_channel or _result_field(result, "channel", "")

    try:
        value = float(raw_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GrpcServiceError(
            grpc.StatusCode.INTERNAL,
            "INVALID_DEVICE_RESPONSE",
            "The device returned a non-numeric value.",
        ) from exc

    unit = _bounded_result_text(raw_unit, default_unit, _MAX_UNIT_LENGTH)
    status = _bounded_result_text(raw_status, default_status, _MAX_STATUS_LENGTH)
    channel = _bounded_result_text(raw_channel, requested_channel, _MAX_CHANNEL_LENGTH)
    quality = _quality_value(raw_quality)
    try:
        timestamp_ns = int(raw_timestamp_ns)
    except (TypeError, ValueError, OverflowError):
        timestamp_ns = time.time_ns()
    if timestamp_ns <= 0:
        timestamp_ns = time.time_ns()

    if not math.isfinite(value):
        quality = common_pb2.MEASUREMENT_QUALITY_BAD
        status = "NON_FINITE"

    return common_pb2.ScalarMeasurement(
        sequence=sequence,
        timestamp_ns=timestamp_ns,
        value=value,
        unit=unit,
        quality=quality,
        status=status,
        channel=channel,
    )


def _result_field(result: Any, name: str, default: Any) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _quality_value(raw_quality: Any) -> int:
    if isinstance(raw_quality, str):
        normalized = raw_quality.strip().upper()
        by_name = {
            "GOOD": common_pb2.MEASUREMENT_QUALITY_GOOD,
            "OK": common_pb2.MEASUREMENT_QUALITY_GOOD,
            "SUSPECT": common_pb2.MEASUREMENT_QUALITY_SUSPECT,
            "WARNING": common_pb2.MEASUREMENT_QUALITY_SUSPECT,
            "BAD": common_pb2.MEASUREMENT_QUALITY_BAD,
            "ERROR": common_pb2.MEASUREMENT_QUALITY_BAD,
        }
        return by_name.get(normalized, common_pb2.MEASUREMENT_QUALITY_UNSPECIFIED)

    try:
        value = int(raw_quality)
    except (TypeError, ValueError, OverflowError):
        return common_pb2.MEASUREMENT_QUALITY_UNSPECIFIED
    if value in {
        common_pb2.MEASUREMENT_QUALITY_UNSPECIFIED,
        common_pb2.MEASUREMENT_QUALITY_GOOD,
        common_pb2.MEASUREMENT_QUALITY_SUSPECT,
        common_pb2.MEASUREMENT_QUALITY_BAD,
    }:
        return value
    return common_pb2.MEASUREMENT_QUALITY_UNSPECIFIED


def _bounded_result_text(raw: Any, default: str, maximum_length: int) -> str:
    if raw is None:
        return default
    value = str(raw).strip()
    if not value:
        return default
    return value[:maximum_length]


def _validate_identifier(value: str, field_name: str) -> str:
    return validate_labnet_identifier(value, name=field_name)


def _safe_operation_id(value: str) -> str | None:
    return value if LABNET_IDENTIFIER_REGEX.fullmatch(value) else None


def _optional_channel(request: Any) -> str:
    if not request.HasField("channel"):
        return ""
    channel = request.channel.strip()
    if not channel:
        raise ValueError("channel cannot be empty when supplied.")
    if len(channel) > _MAX_CHANNEL_LENGTH:
        raise ValueError(
            f"channel cannot exceed {_MAX_CHANNEL_LENGTH} characters."
        )
    if any(ord(character) < 0x20 for character in channel):
        raise ValueError("channel contains invalid control characters.")
    return channel


def _channel_kwargs(channel: str) -> Mapping[str, str] | None:
    return {"channel": channel} if channel else None


def _validate_unit(value: str, *, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} cannot be empty.")
    if len(value) > _MAX_UNIT_LENGTH:
        raise ValueError(
            f"{field_name} cannot exceed {_MAX_UNIT_LENGTH} characters."
        )
    if any(ord(character) < 0x20 for character in value):
        raise ValueError(f"{field_name} contains invalid control characters.")
    return value


def _validate_write_value(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("value must be finite.")
    return value


def _remaining_deadline(context: grpc.aio.ServicerContext) -> float | None:
    remaining = context.time_remaining()
    if remaining is None:
        return None
    remaining = float(remaining)
    if remaining <= 0:
        raise asyncio.TimeoutError
    return remaining


def _sample_deadline(
    context: grpc.aio.ServicerContext,
    policy: StreamPolicy,
) -> float:
    remaining = _remaining_deadline(context)
    if remaining is None:
        return policy.sample_timeout_seconds
    return min(remaining, policy.sample_timeout_seconds)


def _unary_deadline(
    context: grpc.aio.ServicerContext,
    maximum_seconds: float,
) -> float:
    remaining = _remaining_deadline(context)
    if remaining is None:
        return maximum_seconds
    return min(remaining, maximum_seconds)


__all__ = [
    "AuthorizedDeviceSessionProtocol",
    "DeviceExecutorProtocol",
    "DummyDeviceGrpcService",
    "StreamPolicy",
]
