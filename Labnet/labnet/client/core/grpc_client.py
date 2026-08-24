"""Typed direct gRPC clients for the Labnet DummyDevice service."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import grpc

from labnet.identifiers import validate_labnet_identifier

from labnet.grpc.auth import LEASE_METADATA_KEY
from labnet.grpc.generated import common_pb2, dummy_device_pb2
from labnet.grpc.generated import dummy_device_pb2_grpc, transfer_pb2

from .exceptions import (
    ResourceNotFoundError,
    ServerConnectionError,
    translate_grpc_error,
)
from .models import EndpointDescriptor, LeaseGrant, Measurement, MeasurementQuality


SUPPORTED_DEVICE_API_VERSION = "labnet.devices.v1"
_QUALITY_BY_PROTO = {
    common_pb2.MEASUREMENT_QUALITY_UNSPECIFIED: MeasurementQuality.UNSPECIFIED,
    common_pb2.MEASUREMENT_QUALITY_GOOD: MeasurementQuality.GOOD,
    common_pb2.MEASUREMENT_QUALITY_SUSPECT: MeasurementQuality.SUSPECT,
    common_pb2.MEASUREMENT_QUALITY_BAD: MeasurementQuality.BAD,
}
_MAX_LEASE_ID_LENGTH = 512


class AsyncLeaseReleaser(Protocol):
    async def release_lease(self, lease_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class GrpcTlsConfig:
    """Trust roots and optional client identity used for TLS or mTLS."""

    root_certificates: bytes | None = None
    private_key: bytes | None = None
    certificate_chain: bytes | None = None

    def __post_init__(self) -> None:
        if (self.private_key is None) != (self.certificate_chain is None):
            raise ValueError(
                "private_key and certificate_chain must be supplied together for mTLS."
            )

    @classmethod
    def from_files(
        cls,
        *,
        root_certificates: str | Path | None = None,
        private_key: str | Path | None = None,
        certificate_chain: str | Path | None = None,
    ) -> "GrpcTlsConfig":
        return cls(
            root_certificates=_read_optional_file(root_certificates),
            private_key=_read_optional_file(private_key),
            certificate_chain=_read_optional_file(certificate_chain),
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
        private_key=tls.private_key,
        certificate_chain=tls.certificate_chain,
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
        private_key=tls.private_key,
        certificate_chain=tls.certificate_chain,
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


def _request_channel(message: object, channel: str | None) -> None:
    if channel is not None:
        cleaned = channel.strip()
        if not cleaned:
            raise ValueError("channel cannot be empty when supplied.")
        setattr(message, "channel", cleaned)


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


def _stream_request(
    *,
    device_id: str,
    operation_id: str,
    sample_rate_hz: float,
    sample_count: int | None,
    duration_seconds: float | None,
    channel: str | None,
) -> transfer_pb2.StreamDataRequest:
    request = transfer_pb2.StreamDataRequest(
        device_id=device_id,
        operation_id=operation_id,
        sample_rate_hz=_positive_finite(sample_rate_hz, "sample_rate_hz"),
    )
    _request_channel(request, channel)
    if sample_count is not None:
        if (
            isinstance(sample_count, bool)
            or not isinstance(sample_count, int)
            or not 1 <= sample_count <= (2**64 - 1)
        ):
            raise ValueError("sample_count must be a positive uint64 integer.")
        request.sample_count = sample_count
    if duration_seconds is not None:
        seconds = _positive_finite(duration_seconds, "duration_seconds")
        request.duration.FromTimedelta(timedelta(seconds=seconds))
    return request


class _DummyDeviceRequests:
    device_id: str
    lease_id: str
    default_timeout_seconds: float

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        return ((LEASE_METADATA_KEY, self.lease_id),)

    def _read_request(
        self, channel: str | None, operation_id: str | None
    ) -> dummy_device_pb2.ReadValueRequest:
        request = dummy_device_pb2.ReadValueRequest(
            device_id=self.device_id,
            operation_id=_validate_operation_id(operation_id),
        )
        _request_channel(request, channel)
        return request

    def _write_request(
        self,
        value: float,
        unit: str | None,
        channel: str | None,
        operation_id: str | None,
    ) -> dummy_device_pb2.WriteValueRequest:
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("value must be finite.")
        request = dummy_device_pb2.WriteValueRequest(
            device_id=self.device_id,
            operation_id=_validate_operation_id(operation_id),
            value=numeric_value,
        )
        _request_channel(request, channel)
        if unit is not None:
            cleaned = unit.strip()
            if not cleaned:
                raise ValueError("unit cannot be empty when supplied.")
            request.unit = cleaned
        return request


class DummyDeviceGrpcClient(_DummyDeviceRequests):
    """Synchronous typed client over one reusable regular gRPC channel."""

    def __init__(
        self,
        grant: LeaseGrant,
        *,
        tls: GrpcTlsConfig | None = None,
        default_timeout_seconds: float = 10.0,
        channel: grpc.Channel | None = None,
    ) -> None:
        validate_endpoint(grant.device.endpoint)
        self.grant = grant
        self.device_id = _validate_device_id(grant.device.device_id)
        self.lease_id = _validate_lease_id(grant.lease.lease_id)
        self.default_timeout_seconds = _positive_finite(
            default_timeout_seconds, "default_timeout_seconds"
        )
        self._owns_channel = channel is None
        self._channel = channel or create_grpc_channel(grant.device.endpoint, tls=tls)
        self._stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(self._channel)
        self._closed = False

    def connect(self, timeout_seconds: float | None = None) -> None:
        self._ensure_open()
        timeout = _optional_timeout(timeout_seconds, self.default_timeout_seconds)
        try:
            grpc.channel_ready_future(self._channel).result(timeout=timeout)
        except grpc.FutureTimeoutError as exc:
            raise ServerConnectionError(
                f"Timed out connecting to Lab PC at {self.grant.device.endpoint.target}."
            ) from exc

    def read_value(
        self,
        channel: str | None = None,
        *,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Measurement:
        self._ensure_open()
        request = self._read_request(channel, operation_id)
        try:
            response = self._stub.ReadValue(
                request,
                metadata=self._metadata(),
                timeout=_optional_timeout(timeout_seconds, self.default_timeout_seconds),
            )
        except grpc.RpcError as exc:
            raise translate_grpc_error(exc) from exc
        return _measurement(response)

    def write_value(
        self,
        value: float,
        channel: str | None = None,
        *,
        unit: str | None = None,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Measurement:
        self._ensure_open()
        request = self._write_request(value, unit, channel, operation_id)
        try:
            response = self._stub.WriteValue(
                request,
                metadata=self._metadata(),
                timeout=_optional_timeout(timeout_seconds, self.default_timeout_seconds),
            )
        except grpc.RpcError as exc:
            raise translate_grpc_error(exc) from exc
        return _measurement(response)

    def stream_data(
        self,
        *,
        sample_rate_hz: float,
        sample_count: int | None = None,
        duration_seconds: float | None = None,
        channel: str | None = None,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Iterator[Measurement]:
        self._ensure_open()
        request = _stream_request(
            device_id=self.device_id,
            operation_id=_validate_operation_id(operation_id),
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
            duration_seconds=duration_seconds,
            channel=channel,
        )
        call = self._stub.StreamData(
            request,
            metadata=self._metadata(),
            timeout=_optional_timeout(timeout_seconds, None),
        )
        try:
            for response in call:
                yield _measurement(response)
        except grpc.RpcError as exc:
            raise translate_grpc_error(exc) from exc
        finally:
            call.cancel()

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_channel:
            self._channel.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("The DummyDevice gRPC client is closed.")

    def __enter__(self) -> "DummyDeviceGrpcClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class AsyncDummyDeviceClient(_DummyDeviceRequests):
    """Async typed client that owns a channel and, optionally, its lease."""

    def __init__(
        self,
        grant: LeaseGrant,
        *,
        central: AsyncLeaseReleaser | None = None,
        tls: GrpcTlsConfig | None = None,
        default_timeout_seconds: float = 10.0,
        channel: grpc.aio.Channel | None = None,
    ) -> None:
        validate_endpoint(grant.device.endpoint)
        self.grant = grant
        self.device_id = _validate_device_id(grant.device.device_id)
        self.lease_id = _validate_lease_id(grant.lease.lease_id)
        self.default_timeout_seconds = _positive_finite(
            default_timeout_seconds, "default_timeout_seconds"
        )
        self._central = central
        self._owns_channel = channel is None
        self._channel = channel or create_async_grpc_channel(
            grant.device.endpoint, tls=tls
        )
        self._stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(self._channel)
        self._channel_closed = False
        self._closing = False
        self._lease_released = central is None
        self._active_stream_calls: set[object] = set()

    async def connect(self, timeout_seconds: float | None = None) -> None:
        self._ensure_open()
        timeout = _optional_timeout(timeout_seconds, self.default_timeout_seconds)
        try:
            await asyncio.wait_for(self._channel.channel_ready(), timeout=timeout)
        except TimeoutError as exc:
            raise ServerConnectionError(
                f"Timed out connecting to Lab PC at {self.grant.device.endpoint.target}."
            ) from exc

    async def read_value(
        self,
        channel: str | None = None,
        *,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Measurement:
        self._ensure_open()
        request = self._read_request(channel, operation_id)
        try:
            response = await self._stub.ReadValue(
                request,
                metadata=self._metadata(),
                timeout=_optional_timeout(timeout_seconds, self.default_timeout_seconds),
            )
        except grpc.aio.AioRpcError as exc:
            raise translate_grpc_error(exc) from exc
        return _measurement(response)

    async def write_value(
        self,
        value: float,
        channel: str | None = None,
        *,
        unit: str | None = None,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Measurement:
        self._ensure_open()
        request = self._write_request(value, unit, channel, operation_id)
        try:
            response = await self._stub.WriteValue(
                request,
                metadata=self._metadata(),
                timeout=_optional_timeout(timeout_seconds, self.default_timeout_seconds),
            )
        except grpc.aio.AioRpcError as exc:
            raise translate_grpc_error(exc) from exc
        return _measurement(response)

    async def stream_data(
        self,
        *,
        sample_rate_hz: float,
        sample_count: int | None = None,
        duration_seconds: float | None = None,
        channel: str | None = None,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[Measurement]:
        """Stream measurements until exhausted, cancelled, or the client closes.

        If a consumer intentionally stops before exhaustion, prefer
        :meth:`open_stream`, which closes the iterator immediately on context
        exit instead of waiting for async-generator finalization.
        """

        self._ensure_open()
        request = _stream_request(
            device_id=self.device_id,
            operation_id=_validate_operation_id(operation_id),
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
            duration_seconds=duration_seconds,
            channel=channel,
        )
        call = self._stub.StreamData(
            request,
            metadata=self._metadata(),
            timeout=_optional_timeout(timeout_seconds, None),
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

        stream = self.stream_data(
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
            duration_seconds=duration_seconds,
            channel=channel,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
        )
        try:
            yield stream
        finally:
            await stream.aclose()

    async def aclose(self) -> None:
        # The transport is always closed before authority is released, so no
        # command can race after a successful lease release.
        if not self._channel_closed:
            self._closing = True
            for call in tuple(self._active_stream_calls):
                call.cancel()
            self._active_stream_calls.clear()
            if self._owns_channel:
                await self._channel.close()
            self._channel_closed = True
        if self._lease_released:
            return
        central = self._central
        if central is None:
            self._lease_released = True
            return
        try:
            await central.release_lease(self.lease_id)
        except ResourceNotFoundError:
            pass
        self._lease_released = True
        discard = getattr(central, "_discard_device", None)
        if discard is not None:
            discard(self)

    def _ensure_open(self) -> None:
        if self._closing or self._channel_closed:
            raise RuntimeError("The async DummyDevice gRPC client is closed.")

    async def __aenter__(self) -> "AsyncDummyDeviceClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        try:
            await self.aclose()
        except Exception:
            if exc_type is None:
                raise


__all__ = [
    "AsyncDummyDeviceClient",
    "DummyDeviceGrpcClient",
    "GrpcTlsConfig",
    "SUPPORTED_DEVICE_API_VERSION",
    "create_async_grpc_channel",
    "create_grpc_channel",
    "validate_endpoint",
]
