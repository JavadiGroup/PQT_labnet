"""Reusable gRPC execution patterns for generated LabNet device contracts."""

from __future__ import annotations

import asyncio
import math
import numbers
import time
from collections.abc import AsyncIterator, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import grpc
from google.protobuf.descriptor import FieldDescriptor

from labnet.device_manager.admission import AdmissionGate
from labnet.device_manager.errors import DeviceUnavailableError
from labnet.identifiers import LABNET_IDENTIFIER_REGEX, validate_labnet_identifier

from ..auth import extract_lease_id
from ..generated import common_pb2
from ..generated.device_contracts import DeviceContract, RpcSpec
from ..status import GrpcServiceError, abort_from_exception


_MAX_CHANNEL_LENGTH = 128
_MAX_UNIT_LENGTH = 32
_MAX_STATUS_LENGTH = 64

_SCALAR_READ = "RPC_PATTERN_SCALAR_READ"
_SCALAR_WRITE = "RPC_PATTERN_SCALAR_WRITE"
_POLLED_SCALAR_STREAM = "RPC_PATTERN_POLLED_SCALAR_STREAM"
_SUPPORTED_PATTERNS = frozenset(
    {_SCALAR_READ, _SCALAR_WRITE, _POLLED_SCALAR_STREAM}
)


class AuthorizedDeviceSessionProtocol(Protocol):
    async def execute(
        self,
        method: str,
        args: Collection[Any] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any: ...

    async def revalidate(self, *, deadline: float | None = None) -> Any: ...


class DeviceExecutorProtocol(Protocol):
    async def execute(
        self,
        lease_id: str,
        device_id: str,
        method: str,
        args: Collection[Any] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
        operation_id: str | None = None,
        expected_device_type: str | None = None,
    ) -> Any: ...

    async def authorize(
        self,
        lease_id: str,
        device_id: str,
        operation_id: str | None = None,
        *,
        deadline: float | None = None,
        expected_device_type: str | None = None,
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


class UnsupportedRpcPatternError(ValueError):
    """A generated contract requests a pattern this runtime cannot serve."""


class PatternDeviceGrpcService:
    """Serve one generated device contract using shared LabNet patterns.

    Generated ``*_pb2_grpc.add_*_to_server`` functions use structural lookup
    of RPC methods on the servicer. This class supplies those methods from the
    generated :class:`RpcSpec` objects, keeping per-family server code at zero.
    """

    def __init__(
        self,
        executor: DeviceExecutorProtocol,
        contract: DeviceContract,
        *,
        stream_policy: StreamPolicy | None = None,
        default_operation_timeout_seconds: float = 30.0,
        admission_gate: AdmissionGate | None = None,
    ) -> None:
        if (
            not math.isfinite(default_operation_timeout_seconds)
            or default_operation_timeout_seconds <= 0
        ):
            raise ValueError(
                "default_operation_timeout_seconds must be finite and positive."
            )
        self._executor = executor
        self.contract = contract
        self.SERVICE_NAME = contract.service_full_name
        self._stream_policy = stream_policy or StreamPolicy()
        self._default_operation_timeout_seconds = (
            default_operation_timeout_seconds
        )
        self._admission_gate = admission_gate or AdmissionGate()
        self._active_streams: set[str] = set()
        self._active_streams_lock = asyncio.Lock()
        self._rpc_handlers: dict[str, Any] = {}

        seen_names: set[str] = set()
        for spec in contract.methods:
            if spec.rpc_name in seen_names:
                raise ValueError(
                    f"Duplicate RPC name in {contract.service_full_name}: "
                    f"{spec.rpc_name}."
                )
            seen_names.add(spec.rpc_name)
            if spec.pattern not in _SUPPORTED_PATTERNS:
                raise UnsupportedRpcPatternError(
                    f"{spec.rpc_full_name} uses unsupported LabNet pattern "
                    f"{spec.pattern}. Add a shared runtime handler before "
                    "registering this contract."
                )
            self._validate_cardinality(spec)
            if spec.pattern == _POLLED_SCALAR_STREAM:
                self._rpc_handlers[spec.rpc_name] = self._stream_handler(spec)
            else:
                self._rpc_handlers[spec.rpc_name] = self._unary_handler(spec)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._rpc_handlers[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def admission_gate(self) -> AdmissionGate:
        return self._admission_gate

    def _unary_handler(self, spec: RpcSpec) -> Any:
        async def handler(
            request: Any,
            context: grpc.aio.ServicerContext,
        ) -> Any:
            operation_id = _safe_operation_id(getattr(request, "operation_id", ""))
            try:
                lease_id, device_id, operation_id = _request_authorization(
                    request,
                    context,
                )
                if spec.pattern == _SCALAR_READ:
                    return await self._scalar_read(
                        spec,
                        request,
                        context,
                        lease_id=lease_id,
                        device_id=device_id,
                        operation_id=operation_id,
                    )
                if spec.pattern == _SCALAR_WRITE:
                    return await self._scalar_write(
                        spec,
                        request,
                        context,
                        lease_id=lease_id,
                        device_id=device_id,
                        operation_id=operation_id,
                    )
                raise AssertionError(f"Unhandled supported pattern: {spec.pattern}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await abort_from_exception(
                    context,
                    exc,
                    operation_id=operation_id,
                )

        async def admitted_handler(
            request: Any,
            context: grpc.aio.ServicerContext,
        ) -> Any:
            operation_id = _safe_operation_id(getattr(request, "operation_id", ""))
            try:
                async with self._admission_gate.admit():
                    return await handler(request, context)
            except DeviceUnavailableError as exc:
                await abort_from_exception(
                    context,
                    exc,
                    operation_id=operation_id,
                )

        return admitted_handler

    def _stream_handler(self, spec: RpcSpec) -> Any:
        async def handler(
            request: Any,
            context: grpc.aio.ServicerContext,
        ) -> AsyncIterator[Any]:
            operation_id = _safe_operation_id(getattr(request, "operation_id", ""))
            try:
                lease_id, device_id, operation_id = _request_authorization(
                    request,
                    context,
                )
                stream_spec = _validate_stream_request(
                    request,
                    self._stream_policy,
                )
                physical_kwargs = _forwarded_kwargs(request, spec)
                requested_channel = str(physical_kwargs.get("channel", ""))
                session = await self._executor.authorize(
                    lease_id=lease_id,
                    device_id=device_id,
                    operation_id=operation_id,
                    deadline=_unary_deadline(
                        context,
                        self._default_operation_timeout_seconds,
                    ),
                    expected_device_type=self.contract.device_type,
                )

                async with self._claim_stream(device_id):
                    loop = asyncio.get_running_loop()
                    started_at = loop.time()
                    next_sample_at = started_at
                    next_revalidation_at = (
                        started_at
                        + self._stream_policy.lease_revalidation_interval_seconds
                    )
                    period = 1.0 / stream_spec.sample_rate_hz
                    sequence = 0

                    while True:
                        if (
                            stream_spec.sample_count is not None
                            and sequence >= stream_spec.sample_count
                        ):
                            break
                        if context.cancelled() or context.done():
                            break

                        now = loop.time()
                        if (
                            sequence > 0
                            and stream_spec.duration_seconds is not None
                            and now - started_at >= stream_spec.duration_seconds
                        ):
                            break
                        if now < next_sample_at:
                            await asyncio.sleep(next_sample_at - now)

                        now = loop.time()
                        if (
                            sequence > 0
                            and stream_spec.duration_seconds is not None
                            and now - started_at >= stream_spec.duration_seconds
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
                            spec.physical_method,
                            kwargs=physical_kwargs or None,
                            deadline=_sample_deadline(
                                context,
                                self._stream_policy,
                            ),
                        )
                        measurement = _measurement_from_result(
                            result,
                            sequence=sequence,
                            requested_channel=requested_channel,
                            default_unit=spec.default_unit,
                        )
                        yield spec.response_type(
                            operation_id=operation_id,
                            measurement=measurement,
                        )

                        sequence += 1
                        # Never send a catch-up burst after a slow hardware read.
                        next_sample_at = max(next_sample_at + period, loop.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await abort_from_exception(
                    context,
                    exc,
                    operation_id=operation_id,
                )

        async def admitted_handler(
            request: Any,
            context: grpc.aio.ServicerContext,
        ) -> AsyncIterator[Any]:
            operation_id = _safe_operation_id(getattr(request, "operation_id", ""))
            try:
                async with self._admission_gate.admit():
                    async for response in handler(request, context):
                        yield response
            except DeviceUnavailableError as exc:
                await abort_from_exception(
                    context,
                    exc,
                    operation_id=operation_id,
                )

        return admitted_handler

    async def _scalar_read(
        self,
        spec: RpcSpec,
        request: Any,
        context: grpc.aio.ServicerContext,
        *,
        lease_id: str,
        device_id: str,
        operation_id: str,
    ) -> Any:
        physical_kwargs = _forwarded_kwargs(request, spec)
        requested_channel = str(physical_kwargs.get("channel", ""))
        result = await self._executor.execute(
            lease_id=lease_id,
            device_id=device_id,
            method=spec.physical_method,
            kwargs=physical_kwargs or None,
            deadline=_unary_deadline(
                context,
                self._default_operation_timeout_seconds,
            ),
            operation_id=operation_id,
            expected_device_type=self.contract.device_type,
        )
        return spec.response_type(
            operation_id=operation_id,
            measurement=_measurement_from_result(
                result,
                sequence=0,
                requested_channel=requested_channel,
                default_unit=spec.default_unit,
            ),
        )

    async def _scalar_write(
        self,
        spec: RpcSpec,
        request: Any,
        context: grpc.aio.ServicerContext,
        *,
        lease_id: str,
        device_id: str,
        operation_id: str,
    ) -> Any:
        physical_kwargs = _forwarded_kwargs(request, spec)
        try:
            raw_value = physical_kwargs.pop("value")
        except KeyError as exc:
            raise ValueError(
                f"{spec.rpc_full_name} does not forward a value field."
            ) from exc
        value = _validate_write_value(raw_value)
        physical_kwargs["value"] = value
        requested_channel = str(physical_kwargs.get("channel", ""))
        unit = _request_unit(request, spec.default_unit)
        result = await self._executor.execute(
            lease_id=lease_id,
            device_id=device_id,
            method=spec.physical_method,
            kwargs=physical_kwargs or None,
            deadline=_unary_deadline(
                context,
                self._default_operation_timeout_seconds,
            ),
            operation_id=operation_id,
            expected_device_type=self.contract.device_type,
        )
        return spec.response_type(
            operation_id=operation_id,
            measurement=_measurement_from_result(
                value if result is None else result,
                sequence=0,
                requested_channel=requested_channel,
                default_unit=unit,
                default_status="WRITTEN",
            ),
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

    @staticmethod
    def _validate_cardinality(spec: RpcSpec) -> None:
        if spec.pattern == _POLLED_SCALAR_STREAM:
            valid = not spec.client_streaming and spec.server_streaming
        else:
            valid = not spec.client_streaming and not spec.server_streaming
        if not valid:
            raise ValueError(
                f"{spec.rpc_full_name} cardinality {spec.cardinality!r} is "
                f"incompatible with {spec.pattern}."
            )


def _request_authorization(
    request: Any,
    context: grpc.aio.ServicerContext,
) -> tuple[str, str, str]:
    return (
        extract_lease_id(context),
        _validate_identifier(request.device_id, "device_id"),
        _validate_identifier(request.operation_id, "operation_id"),
    )


def _forwarded_kwargs(request: Any, spec: RpcSpec) -> dict[str, Any]:
    fields_by_name = request.DESCRIPTOR.fields_by_name
    kwargs: dict[str, Any] = {}
    for name in spec.forwarded_fields:
        try:
            field = fields_by_name[name]
        except KeyError as exc:
            raise ValueError(
                f"Generated contract {spec.rpc_full_name} references missing "
                f"request field {name!r}."
            ) from exc
        if field.has_presence and not request.HasField(name):
            continue
        if name == "channel":
            channel = _optional_channel(request)
            if channel:
                kwargs[name] = channel
            continue
        value = getattr(request, name)
        if field.is_repeated:
            if (
                field.type == FieldDescriptor.TYPE_MESSAGE
                and field.message_type.GetOptions().map_entry
            ):
                value = dict(value)
            else:
                value = list(value)
        kwargs[name] = value
    return kwargs


def _request_unit(request: Any, default_unit: str) -> str:
    default_unit = _validate_unit(default_unit, field_name="default_unit")
    fields = request.DESCRIPTOR.fields_by_name
    unit_field = fields.get("unit")
    if unit_field is None:
        return default_unit
    has_unit = not unit_field.has_presence or request.HasField("unit")
    unit = (
        _validate_unit(request.unit, field_name="unit")
        if has_unit
        else default_unit
    )
    if unit != default_unit:
        raise ValueError(
            f"unit must be {default_unit!r}; this service does not perform "
            "implicit unit conversion."
        )
    return unit


def _validate_stream_request(request: Any, policy: StreamPolicy) -> _StreamSpec:
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


def _validate_write_value(value: Any) -> float:
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
    "PatternDeviceGrpcService",
    "StreamPolicy",
    "UnsupportedRpcPatternError",
]
