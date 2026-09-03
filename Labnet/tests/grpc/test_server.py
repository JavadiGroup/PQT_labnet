from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import grpc
import pytest
import pytest_asyncio
from grpc_health.v1 import health_pb2, health_pb2_grpc

from labnet.grpc import (
    LEASE_METADATA_KEY,
    GrpcServerConfig,
    LabnetGrpcServer,
    StreamPolicy,
)
from labnet.grpc.generated import dummy_device_pb2, dummy_device_pb2_grpc
from labnet.grpc.generated import transfer_pb2
from labnet.device_manager import DeviceManager, StaticLeaseValidator
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice


LEASE_METADATA = ((LEASE_METADATA_KEY, "lease-123"),)


class _FakeSession:
    def __init__(self, executor: _FakeExecutor) -> None:
        self.executor = executor

    async def execute(
        self,
        method: str,
        args: tuple[Any, ...] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        self.executor.session_calls.append((method, args, kwargs, deadline))
        await asyncio.sleep(0.001)
        return self.executor.value

    async def revalidate(self, *, deadline: float | None = None) -> object:
        self.executor.revalidations.append(deadline)
        return object()


class _FakeExecutor:
    def __init__(self) -> None:
        self.value = 384_230_484_468_500.0
        self.calls: list[dict[str, Any]] = []
        self.session_calls: list[tuple[Any, ...]] = []
        self.revalidations: list[float | None] = []

    async def execute(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs["method"] == "write_value":
            self.value = float(kwargs["kwargs"]["value"])
            return None
        return self.value

    async def authorize(self, **kwargs: Any) -> _FakeSession:
        self.calls.append({"authorize": kwargs})
        return _FakeSession(self)


@pytest.fixture
def executor() -> _FakeExecutor:
    return _FakeExecutor()


@pytest_asyncio.fixture
async def running_server(executor: _FakeExecutor):
    server = LabnetGrpcServer(
        executor,
        config=GrpcServerConfig(bind="127.0.0.1:0"),
        stream_policy=StreamPolicy(
            # Keep this below the 20 ms test sample period while staying above
            # the coarse event-loop clock resolution seen on some Windows hosts.
            lease_revalidation_interval_seconds=0.005,
        ),
    )
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
    try:
        yield server, channel
    finally:
        await channel.close()
        await server.stop(grace=0)


@pytest.mark.asyncio
async def test_read_write_stream_and_health(running_server, executor) -> None:
    server, channel = running_server
    stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)
    assert server.started is True
    assert server.serving is True
    assert tuple(server.device_services) == ("dummy_device", "sacher_laser")
    assert server.service_names == (
        "labnet.devices.v1.DummyDeviceService",
        "labnet.devices.v1.SacherLaserService",
    )

    read = await stub.ReadValue(
        dummy_device_pb2.ReadValueRequest(
            device_id="dummy-1",
            operation_id="read-1",
            channel="frequency",
        ),
        metadata=LEASE_METADATA,
    )
    assert read.operation_id == "read-1"
    assert read.measurement.value == executor.value
    assert read.measurement.unit == "Hz"
    assert read.measurement.channel == "frequency"
    assert executor.calls[-1]["kwargs"] == {"channel": "frequency"}
    assert executor.calls[-1]["deadline"] == 30.0
    assert executor.calls[-1]["expected_device_type"] == "dummy_device"

    write = await stub.WriteValue(
        dummy_device_pb2.WriteValueRequest(
            device_id="dummy-1",
            operation_id="write-1",
            value=384_230_484_469_000.0,
            unit="Hz",
        ),
        metadata=LEASE_METADATA,
    )
    assert write.measurement.status == "WRITTEN"
    assert write.measurement.value == executor.value

    responses = [
        response
        async for response in stub.StreamData(
            transfer_pb2.StreamDataRequest(
                device_id="dummy-1",
                operation_id="stream-1",
                sample_rate_hz=50.0,
                sample_count=3,
            ),
            metadata=LEASE_METADATA,
        )
    ]
    assert [item.measurement.sequence for item in responses] == [0, 1, 2]
    assert [item[0] for item in executor.session_calls] == ["read_value"] * 3
    assert executor.revalidations
    stream_authorization = next(
        item["authorize"] for item in executor.calls if "authorize" in item
    )
    assert stream_authorization["expected_device_type"] == "dummy_device"

    health_stub = health_pb2_grpc.HealthStub(channel)
    health_response = await health_stub.Check(
        health_pb2.HealthCheckRequest(
            service=server.device_service.SERVICE_NAME,
        )
    )
    assert health_response.status == health_pb2.HealthCheckResponse.SERVING

    await server.set_serving(False)
    assert server.serving is False
    health_response = await health_stub.Check(
        health_pb2.HealthCheckRequest(
            service=server.device_service.SERVICE_NAME,
        )
    )
    assert health_response.status == health_pb2.HealthCheckResponse.NOT_SERVING
    await server.set_serving(True)


@pytest.mark.asyncio
async def test_missing_lease_metadata_is_unauthenticated(running_server) -> None:
    _server, channel = running_server
    stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)

    with pytest.raises(grpc.aio.AioRpcError) as error:
        await stub.ReadValue(
            dummy_device_pb2.ReadValueRequest(
                device_id="dummy-1",
                operation_id="read-1",
            )
        )

    assert error.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert dict(error.value.trailing_metadata())["labnet-error-code"] == (
        "LEASE_METADATA_INVALID"
    )


@pytest.mark.asyncio
async def test_stream_rate_outside_policy_is_invalid(running_server) -> None:
    _server, channel = running_server
    stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)

    call = stub.StreamData(
        transfer_pb2.StreamDataRequest(
            device_id="dummy-1",
            operation_id="stream-1",
            sample_rate_hz=0.0,
            sample_count=1,
        ),
        metadata=LEASE_METADATA,
    )
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await call.read()

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_invalid_operation_id_cannot_corrupt_error_metadata(
    running_server,
) -> None:
    _server, channel = running_server
    stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)

    with pytest.raises(grpc.aio.AioRpcError) as error:
        await stub.ReadValue(
            dummy_device_pb2.ReadValueRequest(
                device_id="dummy-1",
                operation_id="bad\nmetadata",
            ),
            metadata=LEASE_METADATA,
        )

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert dict(error.value.trailing_metadata())["labnet-error-code"] == (
        "INVALID_ARGUMENT"
    )


@pytest.mark.asyncio
async def test_write_rejects_implicit_unit_conversion(running_server) -> None:
    _server, channel = running_server
    stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)

    with pytest.raises(grpc.aio.AioRpcError) as error:
        await stub.WriteValue(
            dummy_device_pb2.WriteValueRequest(
                device_id="dummy-1",
                operation_id="wrong-unit",
                value=1.0,
                unit="kHz",
            ),
            metadata=LEASE_METADATA,
        )

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT


def test_plaintext_non_loopback_bind_is_rejected() -> None:
    with pytest.raises(ValueError, match="loopback"):
        GrpcServerConfig(bind="0.0.0.0:50051")


@pytest.mark.asyncio
async def test_grpc_integrates_with_real_device_executor() -> None:
    manager = DeviceManager(
        [PhysicalDummyDevice("dummy-real")],
        StaticLeaseValidator({"lease-real": "dummy-real"}),
    )
    server: LabnetGrpcServer | None = None
    channel: grpc.aio.Channel | None = None
    try:
        report = await manager.start()
        assert report.ready_device_ids == ("dummy-real",)
        server = LabnetGrpcServer(
            manager.executor,
            config=GrpcServerConfig(bind="127.0.0.1:0"),
        )
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)
        metadata = ((LEASE_METADATA_KEY, "lease-real"),)

        await stub.WriteValue(
            dummy_device_pb2.WriteValueRequest(
                device_id="dummy-real",
                operation_id="write-real",
                channel="lock",
                value=12.5,
            ),
            metadata=metadata,
        )
        response = await stub.ReadValue(
            dummy_device_pb2.ReadValueRequest(
                device_id="dummy-real",
                operation_id="read-real",
                channel="lock",
            ),
            metadata=metadata,
        )
        assert response.measurement.value == 12.5
        assert response.measurement.channel == "lock"
    finally:
        if channel is not None:
            await channel.close()
        if server is not None:
            await server.stop(grace=0)
        await manager.shutdown()
