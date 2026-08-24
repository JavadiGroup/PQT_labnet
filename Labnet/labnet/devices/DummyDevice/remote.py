"""Synchronous Central-lease + gRPC convenience proxy for DummyDevice."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from ...client.core.exceptions import NoAvailableDeviceError, ResourceNotFoundError
from ...client.core.grpc_client import DummyDeviceGrpcClient, GrpcTlsConfig
from ...client.core.lab_client import LabClient
from ...client.core.models import Measurement
from .interface import DummyDeviceInterface


class RemoteDummyDevice(DummyDeviceInterface):
    """Own one Central lease and one reusable, typed synchronous gRPC channel."""

    device_type = "dummy_device"

    def __init__(
        self,
        client: LabClient,
        lease_duration: int = 600,
        device_id: str | None = None,
        *,
        tls: GrpcTlsConfig | None = None,
        connection_timeout_seconds: float = 10.0,
        default_rpc_timeout_seconds: float = 10.0,
        idempotency_key: str | None = None,
    ) -> None:
        self.client = client
        self._channel_closed = False
        self._lease_released = False

        if device_id is None:
            available = client.list_available_devices(device_type=self.device_type)
            if not available:
                raise NoAvailableDeviceError(
                    "No available dummy_device is available."
                )
            device_id = available[0].device_id

        grant = client.request_lease(
            device_id=device_id,
            duration_seconds=lease_duration,
            idempotency_key=idempotency_key,
        )
        self.lease = grant.lease
        self.device = grant.device
        try:
            self.grpc = DummyDeviceGrpcClient(
                grant,
                tls=tls,
                default_timeout_seconds=default_rpc_timeout_seconds,
            )
            self.grpc.connect(connection_timeout_seconds)
        except BaseException as exc:
            grpc_client = getattr(self, "grpc", None)
            if grpc_client is not None:
                try:
                    grpc_client.close()
                    self._channel_closed = True
                except BaseException as cleanup_exc:
                    exc.add_note(
                        "Closing the gRPC channel after setup failure also failed: "
                        f"{cleanup_exc}"
                    )
            try:
                self.client.release_lease(self.lease.lease_id)
                self._lease_released = True
            except BaseException as cleanup_exc:
                exc.add_note(
                    "Lease rollback also failed after device-client setup: "
                    f"{cleanup_exc}"
                )
            raise

    @property
    def device_id(self) -> str:
        return self.device.device_id

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id

    @property
    def lease_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.lease.expires_at

    @property
    def closed(self) -> bool:
        return self._channel_closed and self._lease_released

    def connect(self, timeout_seconds: float | None = None) -> None:
        self.grpc.connect(timeout_seconds)

    def metadata(self) -> dict[str, Any]:
        return dict(self.device.metadata)

    def read_measurement(
        self,
        channel: str | None = None,
        *,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Measurement:
        return self.grpc.read_value(
            channel,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
        )

    def read_value(self, channel: str | None = None) -> float:
        return self.read_measurement(channel).value

    def write_measurement(
        self,
        value: float,
        channel: str | None = None,
        *,
        unit: str | None = None,
        operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Measurement:
        return self.grpc.write_value(
            value,
            channel,
            unit=unit,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
        )

    def write_value(self, value: float, channel: str | None = None) -> None:
        self.write_measurement(value, channel)

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
        return self.grpc.stream_data(
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
            duration_seconds=duration_seconds,
            channel=channel,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
        )

    def close(self) -> None:
        if not self._channel_closed:
            self.grpc.close()
            self._channel_closed = True
        if self._lease_released:
            return
        try:
            self.client.release_lease(self.lease.lease_id)
        except ResourceNotFoundError:
            pass
        self._lease_released = True

    def __enter__(self) -> "RemoteDummyDevice":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise


__all__ = ["RemoteDummyDevice"]
