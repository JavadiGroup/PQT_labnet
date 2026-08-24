from __future__ import annotations

import asyncio
from concurrent import futures
from datetime import datetime, timedelta, timezone

import grpc
import httpx
import pytest

from labnet.client import (
    AsyncDummyDeviceClient,
    DeviceDeadlineExceededError,
    DeviceInfo,
    EndpointDescriptor,
    LeaseGrant,
    LeaseInfo,
    MeasurementQuality,
)
from labnet.devices.DummyDevice.remote import RemoteDummyDevice
from labnet.client.core.lab_client import LabClient
from labnet.grpc.generated import common_pb2, dummy_device_pb2
from labnet.grpc.generated import dummy_device_pb2_grpc, transfer_pb2


def grant_for(port: int) -> LeaseGrant:
    now = datetime.now(timezone.utc)
    return LeaseGrant(
        lease=LeaseInfo(
            lease_id="lease-123",
            device_id="dummy-1",
            user="alice",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            state="active",
        ),
        device=DeviceInfo(
            device_id="dummy-1",
            manager_id="lab-pc-1",
            endpoint=EndpointDescriptor(
                transport="grpc",
                target=f"127.0.0.1:{port}",
                api_version="labnet.devices.v1",
                secure=False,
            ),
            device_type="dummy_device",
            runtime_state="ready",
            availability="available",
            effective_available=True,
        ),
    )


def scalar(value: float, sequence: int = 0) -> common_pb2.ScalarMeasurement:
    return common_pb2.ScalarMeasurement(
        sequence=sequence,
        timestamp_ns=1_700_000_000_000_000_000 + sequence,
        value=value,
        unit="Hz",
        quality=common_pb2.MEASUREMENT_QUALITY_GOOD,
        status="OK",
    )


class AsyncService(dummy_device_pb2_grpc.DummyDeviceServiceServicer):
    def __init__(self) -> None:
        self.metadata: list[dict[str, str]] = []

    def _record(self, context: grpc.aio.ServicerContext) -> None:
        self.metadata.append(dict(context.invocation_metadata()))

    async def ReadValue(self, request, context):  # noqa: N802
        self._record(context)
        if request.operation_id == "deadline-op":
            await context.abort(
                grpc.StatusCode.DEADLINE_EXCEEDED,
                "timed out",
                trailing_metadata=(
                    ("labnet-error-code", "DEVICE_DEADLINE_EXCEEDED"),
                    ("labnet-operation-id", request.operation_id),
                    ("labnet-retryable", "true"),
                    ("labnet-operation-may-have-completed", "true"),
                ),
            )
        return dummy_device_pb2.ReadValueResponse(
            operation_id=request.operation_id, measurement=scalar(12.5)
        )

    async def WriteValue(self, request, context):  # noqa: N802
        self._record(context)
        return dummy_device_pb2.WriteValueResponse(
            operation_id=request.operation_id, measurement=scalar(request.value)
        )

    async def StreamData(self, request, context):  # noqa: N802
        self._record(context)
        count = request.sample_count if request.HasField("sample_count") else 2
        for sequence in range(count):
            yield transfer_pb2.StreamDataResponse(
                operation_id=request.operation_id,
                measurement=scalar(20.0 + sequence, sequence),
            )


class BlockingAsyncService(AsyncService):
    def __init__(self) -> None:
        super().__init__()
        self.sample_sent = asyncio.Event()

    async def StreamData(self, request, context):  # noqa: N802
        self._record(context)
        sequence = 0
        while True:
            yield transfer_pb2.StreamDataResponse(
                operation_id=request.operation_id,
                measurement=scalar(20.0 + sequence, sequence),
            )
            self.sample_sent.set()
            sequence += 1
            await asyncio.sleep(60)


class FakeCentral:
    def __init__(self) -> None:
        self.released: list[str] = []

    async def release_lease(self, lease_id: str) -> None:
        self.released.append(lease_id)


@pytest.mark.asyncio
async def test_async_read_write_stream_error_metadata_and_cleanup() -> None:
    service = AsyncService()
    server = grpc.aio.server()
    dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    central = FakeCentral()
    client = AsyncDummyDeviceClient(grant_for(port), central=central)
    try:
        await client.connect()
        read = await client.read_value(operation_id="read-op")
        assert read.value == 12.5
        assert read.quality is MeasurementQuality.GOOD
        written = await client.write_value(42.0, unit="Hz")
        assert written.value == 42.0
        samples = [
            sample
            async for sample in client.stream_data(
                sample_rate_hz=100.0,
                sample_count=3,
                operation_id="stream-op",
            )
        ]
        assert [sample.sequence for sample in samples] == [0, 1, 2]
        assert all(metadata["x-labnet-lease-id"] == "lease-123" for metadata in service.metadata)

        with pytest.raises(ValueError, match="uint64"):
            _ = [
                sample
                async for sample in client.stream_data(
                    sample_rate_hz=100.0,
                    sample_count=1.5,  # type: ignore[arg-type]
                )
            ]

        with pytest.raises(DeviceDeadlineExceededError) as caught:
            await client.read_value(operation_id="deadline-op")
        assert caught.value.error_code == "DEVICE_DEADLINE_EXCEEDED"
        assert caught.value.operation_id == "deadline-op"
        assert caught.value.retryable is True
        assert caught.value.operation_may_have_completed is True
    finally:
        await client.aclose()
        await server.stop(None)
    assert central.released == ["lease-123"]


@pytest.mark.asyncio
async def test_async_close_cancels_stream_on_externally_owned_channel() -> None:
    service = BlockingAsyncService()
    server = grpc.aio.server()
    dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    central = FakeCentral()
    client = AsyncDummyDeviceClient(
        grant_for(port), central=central, channel=channel
    )

    async def consume() -> None:
        async for _sample in client.stream_data(sample_rate_hz=10.0):
            pass

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(service.sample_sent.wait(), timeout=1.0)
        assert client._active_stream_calls
        await client.aclose()
        assert not client._active_stream_calls
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True), timeout=1.0
        )
        assert task.done()
        # The injected channel remains caller-owned and usable after client close.
        await asyncio.wait_for(channel.channel_ready(), timeout=1.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await channel.close()
        await server.stop(None)
    assert central.released == ["lease-123"]


class SyncService(dummy_device_pb2_grpc.DummyDeviceServiceServicer):
    def ReadValue(self, request, context):  # noqa: N802
        assert dict(context.invocation_metadata())["x-labnet-lease-id"] == "lease-123"
        return dummy_device_pb2.ReadValueResponse(
            operation_id=request.operation_id, measurement=scalar(3.0)
        )

    def WriteValue(self, request, context):  # noqa: N802
        return dummy_device_pb2.WriteValueResponse(
            operation_id=request.operation_id, measurement=scalar(request.value)
        )

    def StreamData(self, request, context):  # noqa: N802
        for sequence in range(request.sample_count):
            yield transfer_pb2.StreamDataResponse(
                operation_id=request.operation_id,
                measurement=scalar(float(sequence), sequence),
            )


def test_remote_dummy_device_sync_context_closes_channel_then_releases() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server(SyncService(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    grant = grant_for(port)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "lease": {
                        "lease_id": grant.lease.lease_id,
                        "device_id": grant.lease.device_id,
                        "user": grant.lease.user,
                        "created_at": grant.lease.created_at.isoformat(),
                        "expires_at": grant.lease.expires_at.isoformat(),
                        "state": grant.lease.state,
                    },
                    "device": {
                        "device_id": grant.device.device_id,
                        "manager_id": grant.device.manager_id,
                        "endpoint": {
                            "transport": "grpc",
                            "target": grant.device.endpoint.target,
                            "api_version": "labnet.devices.v1",
                            "secure": False,
                        },
                    },
                },
            )
        return httpx.Response(
            200, json={"lease_id": "lease-123", "state": "released", "released": True}
        )

    client = LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        with RemoteDummyDevice(client, device_id="dummy-1") as remote:
            assert remote.read_value() == 3.0
            remote.write_value(8.0)
            assert [item.value for item in remote.stream_data(sample_rate_hz=10, sample_count=2)] == [0.0, 1.0]
        assert remote.closed
        assert requests[-1].method == "DELETE"
        assert requests[-1].url.path == "/api/v1/leases/lease-123"
    finally:
        client.close()
        server.stop(None)


def test_plaintext_grpc_requires_loopback_and_stream_count_is_integer() -> None:
    remote_endpoint = EndpointDescriptor(
        transport="grpc",
        target="lab-pc.example:50051",
        api_version="labnet.devices.v1",
        secure=False,
    )
    with pytest.raises(ValueError, match="loopback"):
        from labnet.client.core.grpc_client import validate_endpoint

        validate_endpoint(remote_endpoint)
