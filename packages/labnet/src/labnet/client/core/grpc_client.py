"""Typed direct gRPC clients driven by generated Labnet device contracts."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import grpc

from labnet.identifiers import validate_labnet_identifier
from labnet.lease_policy import (
    DEFAULT_LEASE_DURATION_SECONDS,
    DEFAULT_RENEWAL_INTERVAL_SECONDS,
)

from labnet.grpc.auth import LEASE_METADATA_KEY
from labnet.grpc.generated import common_pb2
from labnet.contracts import DeviceContract, RpcSpec

from .exceptions import (
    AuthenticationError,
    LeaseAuthorityError,
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServerConnectionError,
    ServerResponseError,
    translate_grpc_error,
)
from .models import (
    EndpointDescriptor,
    LeaseGrant,
    LeaseInfo,
    Measurement,
    MeasurementQuality,
)


SUPPORTED_DEVICE_API_VERSION = "labnet.devices.v1"
_QUALITY_BY_PROTO = {
    common_pb2.MEASUREMENT_QUALITY_UNSPECIFIED: MeasurementQuality.UNSPECIFIED,
    common_pb2.MEASUREMENT_QUALITY_GOOD: MeasurementQuality.GOOD,
    common_pb2.MEASUREMENT_QUALITY_SUSPECT: MeasurementQuality.SUSPECT,
    common_pb2.MEASUREMENT_QUALITY_BAD: MeasurementQuality.BAD,
}
_MAX_LEASE_ID_LENGTH = 512
_SCALAR_READ = "RPC_PATTERN_SCALAR_READ"
_SCALAR_WRITE = "RPC_PATTERN_SCALAR_WRITE"
_UNARY_COMMAND = "RPC_PATTERN_UNARY_COMMAND"
_POLLED_SCALAR_STREAM = "RPC_PATTERN_POLLED_SCALAR_STREAM"


class AsyncLeaseReleaser(Protocol):
    async def release_lease(self, lease_id: str) -> None: ...

    async def renew_lease(
        self, lease_id: str, duration_seconds: int
    ) -> LeaseInfo: ...


@dataclass(frozen=True, slots=True)
class GrpcTlsConfig:
    """Trust roots used to verify a Lab-PC gRPC server."""

    root_certificates: bytes | None = None

    @classmethod
    def from_files(
        cls,
        *,
        root_certificates: str | Path | None = None,
    ) -> "GrpcTlsConfig":
        return cls(
            root_certificates=_read_optional_file(root_certificates),
        )


def _read_optional_file(path: str | Path | None) -> bytes | None:
    return Path(path).expanduser().read_bytes() if path is not None else None


def _target_host(target: str) -> str:
    """Validate the Central-provided gRPC target and extract its host."""

    value = target.strip()
    if (
        not value
        or "://" in value
        or "/" in value
        or "@" in value
        or any(c.isspace() for c in value)
    ):
        raise ValueError("gRPC target must use host:port syntax.")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 2 or closing + 1 >= len(value) or value[closing + 1] != ":":
            raise ValueError("IPv6 gRPC targets must use [address]:port syntax.")
        host, port_text = value[1:closing], value[closing + 2 :]
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator or not host:
            raise ValueError("gRPC target must include a host and port.")
    if not port_text.isdecimal() or not 1 <= int(port_text) <= 65_535:
        raise ValueError("gRPC target port must be between 1 and 65535.")
    if any(ord(character) < 0x21 for character in host):
        raise ValueError("gRPC target host contains invalid characters.")
    return host.rstrip(".").lower()


def _is_loopback_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_endpoint(endpoint: EndpointDescriptor) -> None:
    """Apply API-version and plaintext transport policy before connecting."""

    if endpoint.api_version != SUPPORTED_DEVICE_API_VERSION:
        raise ValueError(
            f"Unsupported device API version {endpoint.api_version!r}; expected "
            f"{SUPPORTED_DEVICE_API_VERSION!r}."
        )
    host = _target_host(endpoint.target)
    if not endpoint.secure and not _is_loopback_host(host):
        raise ValueError("Plaintext gRPC endpoints are allowed only on loopback.")
    if not endpoint.secure and endpoint.tls_server_name is not None:
        raise ValueError("tls_server_name is valid only for secure gRPC endpoints.")


def _channel_options(
    endpoint: EndpointDescriptor,
    options: Sequence[tuple[str, str | int]],
) -> tuple[tuple[str, str | int], ...]:
    configured = list(options)
    if endpoint.tls_server_name:
        names = {name for name, _ in configured}
        if "grpc.ssl_target_name_override" in names:
            raise ValueError("TLS server name override was supplied more than once.")
        configured.extend(
            (
                ("grpc.ssl_target_name_override", endpoint.tls_server_name),
                ("grpc.default_authority", endpoint.tls_server_name),
            )
        )
    return tuple(configured)


def create_grpc_channel(
    endpoint: EndpointDescriptor,
    *,
    tls: GrpcTlsConfig | None = None,
    options: Sequence[tuple[str, str | int]] = (),
) -> grpc.Channel:
    validate_endpoint(endpoint)
    configured_options = _channel_options(endpoint, options)
    if not endpoint.secure:
        if tls is not None:
            raise ValueError("TLS credentials cannot be used with a plaintext endpoint.")
        return grpc.insecure_channel(endpoint.target, options=configured_options)
    tls = tls or GrpcTlsConfig()
    credentials = grpc.ssl_channel_credentials(
        root_certificates=tls.root_certificates,
    )
    return grpc.secure_channel(
        endpoint.target,
        credentials,
        options=configured_options,
    )


def create_async_grpc_channel(
    endpoint: EndpointDescriptor,
    *,
    tls: GrpcTlsConfig | None = None,
    options: Sequence[tuple[str, str | int]] = (),
) -> grpc.aio.Channel:
    validate_endpoint(endpoint)
    configured_options = _channel_options(endpoint, options)
    if not endpoint.secure:
        if tls is not None:
            raise ValueError("TLS credentials cannot be used with a plaintext endpoint.")
        return grpc.aio.insecure_channel(endpoint.target, options=configured_options)
    tls = tls or GrpcTlsConfig()
    credentials = grpc.ssl_channel_credentials(
        root_certificates=tls.root_certificates,
    )
    return grpc.aio.secure_channel(
        endpoint.target,
        credentials,
        options=configured_options,
    )


def _validate_operation_id(operation_id: str | None) -> str:
    value = str(uuid.uuid4()) if operation_id is None else operation_id
    return validate_labnet_identifier(value, name="operation_id")


def _validate_device_id(device_id: str) -> str:
    return validate_labnet_identifier(device_id, name="device_id")


def _validate_lease_id(lease_id: str) -> str:
    value = lease_id.strip()
    if (
        not value
        or len(value) > _MAX_LEASE_ID_LENGTH
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError("lease_id must be 1-512 visible ASCII characters.")
    return value


def _positive_finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and greater than zero.")
    return result


def _optional_timeout(value: float | None, default: float | None) -> float | None:
    selected = default if value is None else value
    return None if selected is None else _positive_finite(selected, "timeout_seconds")


def _measurement(response: object) -> Measurement:
    proto = response.measurement
    return Measurement(
        operation_id=response.operation_id,
        sequence=proto.sequence,
        timestamp_ns=proto.timestamp_ns,
        value=proto.value,
        unit=proto.unit,
        quality=_QUALITY_BY_PROTO.get(proto.quality, MeasurementQuality.UNSPECIFIED),
        status=proto.status,
        channel=proto.channel or None,
    )


def _contract_method(contract: DeviceContract, python_name: str) -> RpcSpec | None:
    return next(
        (method for method in contract.methods if method.python_name == python_name),
        None,
    )


def _contract_dispatch_kind(spec: RpcSpec) -> str:
    """Return the generated transport shape, rejecting stale/malformed specs."""

    if spec.pattern in {_SCALAR_READ, _SCALAR_WRITE, _UNARY_COMMAND}:
        if spec.client_streaming or spec.server_streaming:
            raise RuntimeError(
                f"Generated RPC {spec.rpc_full_name} uses {spec.pattern} but "
                f"declares {spec.cardinality} cardinality. Regenerate the "
                "device contracts."
            )
        return "unary"
    if spec.pattern == _POLLED_SCALAR_STREAM:
        if spec.client_streaming or not spec.server_streaming:
            raise RuntimeError(
                f"Generated RPC {spec.rpc_full_name} uses {spec.pattern} but "
                f"declares {spec.cardinality} cardinality. Regenerate the "
                "device contracts."
            )
        return "stream"
    raise RuntimeError(
        f"Generated RPC {spec.rpc_full_name} uses unsupported client pattern "
        f"{spec.pattern}."
    )


def _require_contract_pattern(
    contract: DeviceContract,
    python_name: str,
    expected_pattern: str,
) -> RpcSpec:
    """Resolve a convenience method without overriding its generated meaning."""

    spec = _contract_method(contract, python_name)
    if spec is None:
        raise AttributeError(
            f"Contract {contract.device_type!r} does not define RPC method "
            f"{python_name!r}."
        )
    if spec.pattern != expected_pattern:
        raise RuntimeError(
            f"Contract RPC {spec.rpc_full_name} now uses {spec.pattern}; the "
            f"{python_name!r} convenience API requires {expected_pattern}."
        )
    _contract_dispatch_kind(spec)
    return spec


def _validate_grant_contract(
    grant: LeaseGrant,
    contract: DeviceContract,
) -> None:
    actual = grant.device.device_type
    if actual != contract.device_type:
        raise ValueError(
            f"LeaseGrant device_type {actual!r} does not match requested "
            f"contract device_type {contract.device_type!r}."
        )


def _bind_contract_arguments(
    spec: RpcSpec,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Bind the physical arguments declared by the generated RpcSpec."""

    if len(args) > len(spec.forwarded_fields):
        raise TypeError(
            f"{spec.python_name}() accepts at most {len(spec.forwarded_fields)} "
            f"positional argument(s); received {len(args)}."
        )
    values = dict(zip(spec.forwarded_fields, args, strict=False))
    for field in spec.forwarded_field_specs:
        if field.name in kwargs:
            if field.name in values:
                raise TypeError(
                    f"{spec.python_name}() got multiple values for {field.name!r}."
                )
            values[field.name] = kwargs.pop(field.name)
        if not field.optional and field.name not in values:
            raise TypeError(
                f"{spec.python_name}() is missing required argument {field.name!r}."
            )
    return values


def _set_proto_field(message: object, name: str, value: Any) -> None:
    if value is None:
        return
    field = message.DESCRIPTOR.fields_by_name[name]
    target = getattr(message, name)
    if field.is_repeated:
        if field.message_type is not None and field.message_type.GetOptions().map_entry:
            target.update(value)
        else:
            target.extend(value)
    elif field.message_type is not None:
        target.CopyFrom(value)
    else:
        setattr(message, name, value)


def _build_contract_request(
    spec: RpcSpec,
    *,
    device_id: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[object, float | None]:
    """Build a protobuf request using only generated contract metadata."""

    options = dict(kwargs)
    operation_id = _validate_operation_id(options.pop("operation_id", None))
    timeout_seconds = options.pop("timeout_seconds", None)
    forwarded = _bind_contract_arguments(spec, args, options)
    request = spec.request_type(device_id=device_id, operation_id=operation_id)

    for name, value in forwarded.items():
        if name == "channel" and value is not None:
            cleaned = str(value).strip()
            if not cleaned:
                raise ValueError("channel cannot be empty when supplied.")
            value = cleaned
        if name == "value":
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("value must be finite.")
        _set_proto_field(request, name, value)

    if spec.pattern == "RPC_PATTERN_SCALAR_WRITE":
        unit = options.pop("unit", None)
        if unit is not None:
            unit = str(unit).strip()
            if not unit:
                raise ValueError("unit cannot be empty when supplied.")
            request.unit = unit
    elif spec.pattern == "RPC_PATTERN_POLLED_SCALAR_STREAM":
        if "sample_rate_hz" not in options:
            raise TypeError(
                f"{spec.python_name}() is missing required argument "
                "'sample_rate_hz'."
            )
        request.sample_rate_hz = _positive_finite(
            options.pop("sample_rate_hz"), "sample_rate_hz"
        )
        sample_count = options.pop("sample_count", None)
        if sample_count is not None:
            if (
                isinstance(sample_count, bool)
                or not isinstance(sample_count, int)
                or not 1 <= sample_count <= (2**64 - 1)
            ):
                raise ValueError("sample_count must be a positive uint64 integer.")
            request.sample_count = sample_count
        duration_seconds = options.pop("duration_seconds", None)
        if duration_seconds is not None:
            request.duration.FromTimedelta(
                timedelta(
                    seconds=_positive_finite(
                        duration_seconds, "duration_seconds"
                    )
                )
            )

    if options:
        unexpected = next(iter(options))
        raise TypeError(
            f"{spec.python_name}() got an unexpected keyword argument "
            f"{unexpected!r}."
        )
    return request, _optional_timeout(timeout_seconds, None)


def _command_value(response: object) -> Any:
    """Decode a lossless unary-command result into its Python value."""

    result = response.result
    kind = result.WhichOneof("kind")
    if kind == "none_value":
        return None
    if kind == "boolean_value":
        return bool(result.boolean_value)
    if kind == "integer_value":
        return int(result.integer_value)
    if kind == "floating_point_value":
        return float(result.floating_point_value)
    if kind == "text_value":
        return str(result.text_value)
    if kind == "integer_sequence":
        return tuple(int(value) for value in result.integer_sequence.values)
    raise RuntimeError("The device returned an empty or unknown command result.")


class _ContractDeviceRequests:
    device_id: str
    lease_id: str
    default_timeout_seconds: float
    _contract: DeviceContract

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return ((LEASE_METADATA_KEY, self.lease_id),)


class ContractDeviceGrpcClient(_ContractDeviceRequests):
    """Synchronous contract-driven client over one reusable gRPC channel.

    ``contract`` is private framework plumbing. Public users obtain this
    transport through a local-or-remote device facade, not by constructing a
    device-specific gRPC object.
    """

    def __init__(
        self,
        grant: LeaseGrant,
        *,
        tls: GrpcTlsConfig | None = None,
        default_timeout_seconds: float = 10.0,
        channel: grpc.Channel | None = None,
        _contract: DeviceContract,
    ) -> None:
        validate_endpoint(grant.device.endpoint)
        _validate_grant_contract(grant, _contract)
        self.grant = grant
        self.device_id = _validate_device_id(grant.device.device_id)
        self.lease_id = _validate_lease_id(grant.lease.lease_id)
        self.default_timeout_seconds = _positive_finite(
            default_timeout_seconds, "default_timeout_seconds"
        )
        self._contract = _contract
        self._owns_channel = channel is None
        self._channel = channel or create_grpc_channel(grant.device.endpoint, tls=tls)
        self._stub = self._contract.stub_type(self._channel)
        self._closed = False

    @property
    def contract(self) -> DeviceContract:
        return self._contract

    def __getattr__(self, name: str) -> Any:
        """Create methods for newly generated RPCs from their pattern specs."""

        contract = self.__dict__.get("_contract")
        if contract is None:
            raise AttributeError(name)
        spec = _contract_method(contract, name)
        if spec is None:
            raise AttributeError(name)

        if _contract_dispatch_kind(spec) == "stream":
            def stream(*args: Any, **kwargs: Any) -> Iterator[Measurement]:
                return self._invoke_generated_stream(spec, args, kwargs)

            return stream

        def unary(*args: Any, **kwargs: Any) -> Any:
            return self._invoke_generated_unary(spec, args, kwargs)

        return unary

    def _invoke_generated_unary(
        self,
        spec: RpcSpec,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        if _contract_dispatch_kind(spec) != "unary":
            raise RuntimeError(
                f"RPC {spec.rpc_full_name} is not a unary operation."
            )
        self._ensure_open()
        request, timeout = _build_contract_request(
            spec,
            device_id=self.device_id,
            args=args,
            kwargs=kwargs,
        )
        try:
            response = getattr(self._stub, spec.rpc_name)(
                request,
                metadata=self._metadata(),
                timeout=(
                    self.default_timeout_seconds if timeout is None else timeout
                ),
            )
        except grpc.RpcError as exc:
            raise translate_grpc_error(exc) from exc
        if spec.pattern == _UNARY_COMMAND:
            return _command_value(response)
        return _measurement(response)

    def _invoke_generated_stream(
        self,
        spec: RpcSpec,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Iterator[Measurement]:
        if _contract_dispatch_kind(spec) != "stream":
            raise RuntimeError(
                f"RPC {spec.rpc_full_name} is not a streaming operation."
            )
        self._ensure_open()
        request, timeout = _build_contract_request(
            spec,
            device_id=self.device_id,
            args=args,
            kwargs=kwargs,
        )
        call = getattr(self._stub, spec.rpc_name)(
            request,
            metadata=self._metadata(),
            timeout=timeout,
        )
        try:
            for response in call:
                yield _measurement(response)
        except grpc.RpcError as exc:
            raise translate_grpc_error(exc) from exc
        finally:
            call.cancel()

    def connect(self, timeout_seconds: float | None = None) -> None:
        self._ensure_open()
        timeout = _optional_timeout(timeout_seconds, self.default_timeout_seconds)
        try:
            grpc.channel_ready_future(self._channel).result(timeout=timeout)
        except grpc.FutureTimeoutError as exc:
            raise ServerConnectionError(
                f"Timed out connecting to Lab PC at {self.grant.device.endpoint.target}."
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_channel:
            self._channel.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("The contract device gRPC client is closed.")

    def __enter__(self) -> "ContractDeviceGrpcClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class AsyncContractDeviceClient(_ContractDeviceRequests):
    """Async contract-driven client that owns a channel and optional lease."""

    def __init__(
        self,
        grant: LeaseGrant,
        *,
        central: AsyncLeaseReleaser | None = None,
        lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        auto_renew: bool = False,
        renewal_interval_seconds: float = DEFAULT_RENEWAL_INTERVAL_SECONDS,
        tls: GrpcTlsConfig | None = None,
        default_timeout_seconds: float = 10.0,
        channel: grpc.aio.Channel | None = None,
        _contract: DeviceContract,
    ) -> None:
        validate_endpoint(grant.device.endpoint)
        _validate_grant_contract(grant, _contract)
        self.grant = grant
        self.device_id = _validate_device_id(grant.device.device_id)
        self.lease_id = _validate_lease_id(grant.lease.lease_id)
        self.default_timeout_seconds = _positive_finite(
            default_timeout_seconds, "default_timeout_seconds"
        )
        self._contract = _contract
        if not isinstance(auto_renew, bool):
            raise TypeError("auto_renew must be a boolean.")
        if auto_renew and central is None:
            raise ValueError("auto_renew requires a Central lease client.")
        if isinstance(lease_duration_seconds, bool) or not isinstance(
            lease_duration_seconds, int
        ):
            raise TypeError("lease_duration_seconds must be an integer.")
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be greater than zero.")
        if auto_renew:
            renewal_interval_seconds = _positive_finite(
                renewal_interval_seconds, "renewal_interval_seconds"
            )
            if renewal_interval_seconds >= lease_duration_seconds:
                raise ValueError(
                    "renewal_interval_seconds must be less than lease_duration_seconds."
                )
        else:
            renewal_interval_seconds = DEFAULT_RENEWAL_INTERVAL_SECONDS
        self._central = central
        self._owns_channel = channel is None
        self._channel = channel or create_async_grpc_channel(
            grant.device.endpoint, tls=tls
        )
        self._stub = self._contract.stub_type(self._channel)
        self._channel_closed = False
        self._closing = False
        self._lease_released = central is None
        self._active_stream_calls: set[object] = set()
        self._lease_duration_seconds = lease_duration_seconds
        self._auto_renew = auto_renew
        self._renewal_interval_seconds = renewal_interval_seconds
        self._renewal_task: asyncio.Task[None] | None = None
        self._authority_error: LeaseAuthorityError | None = None
        self._authority_cleanup_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()

    @property
    def contract(self) -> DeviceContract:
        return self._contract

    def __getattr__(self, name: str) -> Any:
        """Create async methods for newly generated RPCs from their specs."""

        contract = self.__dict__.get("_contract")
        if contract is None:
            raise AttributeError(name)
        spec = _contract_method(contract, name)
        if spec is None:
            raise AttributeError(name)

        if _contract_dispatch_kind(spec) == "stream":
            def stream(*args: Any, **kwargs: Any) -> AsyncIterator[Measurement]:
                return self._invoke_generated_stream(spec, args, kwargs)

            return stream

        async def unary(*args: Any, **kwargs: Any) -> Any:
            return await self._invoke_generated_unary(spec, args, kwargs)

        return unary

    async def _invoke_generated_unary(
        self,
        spec: RpcSpec,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        if _contract_dispatch_kind(spec) != "unary":
            raise RuntimeError(
                f"RPC {spec.rpc_full_name} is not a unary operation."
            )
        self._ensure_open()
        request, timeout = _build_contract_request(
            spec,
            device_id=self.device_id,
            args=args,
            kwargs=kwargs,
        )
        try:
            response = await getattr(self._stub, spec.rpc_name)(
                request,
                metadata=self._metadata(),
                timeout=(
                    self.default_timeout_seconds if timeout is None else timeout
                ),
            )
        except grpc.aio.AioRpcError as exc:
            raise translate_grpc_error(exc) from exc
        if spec.pattern == _UNARY_COMMAND:
            return _command_value(response)
        return _measurement(response)

    async def _invoke_generated_stream(
        self,
        spec: RpcSpec,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> AsyncIterator[Measurement]:
        if _contract_dispatch_kind(spec) != "stream":
            raise RuntimeError(
                f"RPC {spec.rpc_full_name} is not a streaming operation."
            )
        self._ensure_open()
        request, timeout = _build_contract_request(
            spec,
            device_id=self.device_id,
            args=args,
            kwargs=kwargs,
        )
        call = getattr(self._stub, spec.rpc_name)(
            request,
            metadata=self._metadata(),
            timeout=timeout,
        )
        self._active_stream_calls.add(call)
        try:
            async for response in call:
                yield _measurement(response)
        except grpc.aio.AioRpcError as exc:
            raise translate_grpc_error(exc) from exc
        finally:
            call.cancel()
            self._active_stream_calls.discard(call)

    def _start_auto_renewal(self) -> None:
        """Start the configured renewal task after the channel is connected."""

        if not self._auto_renew or self._renewal_task is not None:
            return
        if self._closing or self._channel_closed:
            raise RuntimeError("Cannot start renewal after the device client is closing.")
        self._renewal_task = asyncio.create_task(
            self._renewal_worker(),
            name=f"labnet-lease-renew-{self.lease_id}",
        )

    async def connect(self, timeout_seconds: float | None = None) -> None:
        self._ensure_open()
        timeout = _optional_timeout(timeout_seconds, self.default_timeout_seconds)
        try:
            await asyncio.wait_for(self._channel.channel_ready(), timeout=timeout)
        except TimeoutError as exc:
            raise ServerConnectionError(
                f"Timed out connecting to Lab PC at {self.grant.device.endpoint.target}."
            ) from exc

    @asynccontextmanager
    async def open_stream(
        self,
        *,
        sample_rate_hz: float,
        sample_count: int | None = None,
        duration_seconds: float | None = None,
        channel: str | None = None,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[AsyncIterator[Measurement]]:
        """Provide an explicitly closed stream for early-exit consumers."""

        spec = _require_contract_pattern(
            self._contract,
            "stream_data",
            _POLLED_SCALAR_STREAM,
        )
        stream = self._invoke_generated_stream(
            spec,
            (),
            {
                "sample_rate_hz": sample_rate_hz,
                "sample_count": sample_count,
                "duration_seconds": duration_seconds,
                "channel": channel,
                "operation_id": operation_id,
                "timeout_seconds": timeout_seconds,
            },
        )
        try:
            yield stream
        finally:
            await stream.aclose()

    async def aclose(self) -> None:
        async with self._close_lock:
            await self._aclose_locked()

    async def _aclose_locked(self) -> None:
        # The transport is always closed before authority is released, so no
        # command can race after a successful lease release.
        self._closing = True
        first_error: BaseException | None = None
        try:
            await self._cancel_auto_renewal()
        except BaseException as exc:
            first_error = exc
        cleanup_task = self._authority_cleanup_task
        if cleanup_task is not None and cleanup_task is not asyncio.current_task():
            self._authority_cleanup_task = None
            try:
                await cleanup_task
            except BaseException as exc:
                first_error = first_error or exc
        if not self._channel_closed:
            for call in tuple(self._active_stream_calls):
                call.cancel()
            self._active_stream_calls.clear()
            try:
                if self._owns_channel:
                    await self._channel.close()
            except BaseException as exc:
                first_error = first_error or exc
            finally:
                self._channel_closed = True
        if self._lease_released:
            if first_error is not None:
                raise first_error
            return
        central = self._central
        if central is None:
            self._lease_released = True
            if first_error is not None:
                raise first_error
            return
        try:
            await central.release_lease(self.lease_id)
        except ResourceNotFoundError:
            self._lease_released = True
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            else:
                first_error.add_note(f"Releasing the lease also failed: {exc}")
        else:
            self._lease_released = True
        if self._lease_released:
            discard = getattr(central, "_discard_device", None)
            if discard is not None:
                discard(self)
        if first_error is not None:
            raise first_error

    def _ensure_open(self) -> None:
        if self._authority_error is not None:
            raise self._authority_error
        if self.grant.lease.expired:
            error = LeaseAuthorityError(
                f"Lease {self.lease_id!r} expired before it could be renewed."
            )
            self._authority_error = error
            self._closing = True
            for call in tuple(self._active_stream_calls):
                call.cancel()
            self._active_stream_calls.clear()
            self._authority_cleanup_task = asyncio.create_task(
                self._mark_authority_lost(error),
                name=f"labnet-authority-close-{self.lease_id}",
            )
            raise error
        if self._closing or self._channel_closed:
            raise RuntimeError("The async contract device gRPC client is closed.")

    async def _cancel_auto_renewal(self) -> None:
        task = self._renewal_task
        if task is None:
            return
        self._renewal_task = None
        if task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _renewal_worker(self) -> None:
        wait_seconds = self._renewal_interval_seconds
        try:
            while True:
                remaining = (
                    self.grant.lease.expires_at - datetime.now(timezone.utc)
                ).total_seconds()
                if remaining <= 0:
                    await self._mark_authority_lost(
                        LeaseAuthorityError(
                            f"Lease {self.lease_id!r} reached its authority deadline."
                        )
                    )
                    return
                await asyncio.sleep(min(wait_seconds, remaining))
                if self.grant.lease.expired:
                    await self._mark_authority_lost(
                        LeaseAuthorityError(
                            f"Lease {self.lease_id!r} expired before it could be renewed."
                        )
                    )
                    return
                central = self._central
                if central is None:
                    return
                try:
                    renewed = await central.renew_lease(
                        self.lease_id, self._lease_duration_seconds
                    )
                    if renewed.state != "active" or renewed.expired:
                        raise LeaseAuthorityError(
                            f"Lease {renewed.lease_id!r} is no longer active."
                        )
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    if self._renewal_is_terminal(exc) or self.grant.lease.expired:
                        await self._mark_authority_lost(exc)
                        return
                    remaining = (
                        self.grant.lease.expires_at - datetime.now(timezone.utc)
                    ).total_seconds()
                    wait_seconds = min(
                        self._renewal_interval_seconds,
                        max(0.001, remaining / 2),
                    )
                    continue
                self.grant = LeaseGrant(lease=renewed, device=self.grant.device)
                wait_seconds = self._renewal_interval_seconds
        except asyncio.CancelledError:
            return

    @staticmethod
    def _renewal_is_terminal(exc: BaseException) -> bool:
        if isinstance(
            exc,
            (
                LeaseAuthorityError,
                AuthenticationError,
                PermissionDeniedError,
                ResourceConflictError,
                ResourceNotFoundError,
            ),
        ):
            return True
        return (
            isinstance(exc, ServerResponseError)
            and 400 <= exc.status_code < 500
            and exc.status_code not in {408, 429}
        )

    async def _mark_authority_lost(self, cause: BaseException) -> None:
        self._authority_error = (
            cause
            if isinstance(cause, LeaseAuthorityError)
            else LeaseAuthorityError(
                f"Automatic renewal failed and lease {self.lease_id!r} can no "
                f"longer be used: {cause}"
            )
        )
        self._closing = True
        for call in tuple(self._active_stream_calls):
            call.cancel()
        self._active_stream_calls.clear()
        if not self._channel_closed:
            if self._owns_channel:
                await self._channel.close()
            self._channel_closed = True

    async def __aenter__(self) -> "AsyncContractDeviceClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        try:
            await self.aclose()
        except Exception:
            if exc_type is None:
                raise


__all__ = [
    "AsyncContractDeviceClient",
    "ContractDeviceGrpcClient",
    "GrpcTlsConfig",
    "SUPPORTED_DEVICE_API_VERSION",
    "create_async_grpc_channel",
    "create_grpc_channel",
    "validate_endpoint",
]
