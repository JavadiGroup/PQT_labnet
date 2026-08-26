from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import replace
from pathlib import Path

import grpc
import httpx
import pytest

from examples.lab_pc.config import LabPCSettings
from examples.lab_pc.main import create_app
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.grpc import LEASE_METADATA_KEY
from labnet.grpc.generated import dummy_device_pb2, dummy_device_pb2_grpc
from labnet.grpc.generated import transfer_pb2


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_lab_pc_composes_health_control_and_streaming(tmp_path: Path) -> None:
    settings = replace(
        LabPCSettings.from_env(tmp_path / "missing.env"),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
    )
    app = create_app(settings=settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://management.test",
        ) as management_client:
            live = await management_client.get("/health/live")
            ready = await management_client.get("/health/ready")

        assert live.status_code == 200
        assert live.json()["status"] == "alive"
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["devices"][0]["device_id"] == "dummy_device_1"

        grpc_port = app.state.runtime.grpc_server.bound_port
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{grpc_port}")
        try:
            stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)
            metadata = ((LEASE_METADATA_KEY, "development-lease"),)

            write = await stub.WriteValue(
                dummy_device_pb2.WriteValueRequest(
                    device_id="dummy_device_1",
                    operation_id="write-lock-channel",
                    channel="lock-channel",
                    value=384_230_484_468_500.0,
                    unit="Hz",
                ),
                metadata=metadata,
            )
            assert write.measurement.status == "WRITTEN"

            read = await stub.ReadValue(
                dummy_device_pb2.ReadValueRequest(
                    device_id="dummy_device_1",
                    operation_id="read-lock-channel",
                    channel="lock-channel",
                ),
                metadata=metadata,
            )
            assert read.measurement.value == write.measurement.value
            assert read.measurement.channel == "lock-channel"
            assert read.measurement.unit == "Hz"

            samples = [
                response
                async for response in stub.StreamData(
                    transfer_pb2.StreamDataRequest(
                        device_id="dummy_device_1",
                        operation_id="stream-lock-channel",
                        channel="lock-channel",
                        sample_rate_hz=100.0,
                        sample_count=3,
                    ),
                    metadata=metadata,
                )
            ]
            assert [sample.measurement.sequence for sample in samples] == [0, 1, 2]
            assert all(
                sample.measurement.value == write.measurement.value
                for sample in samples
            )
            assert all(sample.measurement.unit == "Hz" for sample in samples)
        finally:
            await channel.close()

    assert app.state.runtime.grpc_server.started is False


@pytest.mark.asyncio
async def test_one_hanging_device_does_not_block_healthy_device_startup(
    tmp_path: Path,
) -> None:
    release_connect = threading.Event()

    class HangingDummyDevice(PhysicalDummyDevice):
        def connect(self) -> None:
            release_connect.wait(timeout=2)
            super().connect()

    settings = replace(
        LabPCSettings.from_env(tmp_path / "missing.env"),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
        device_start_timeout_seconds=0.02,
    )
    app = create_app(
        settings=settings,
        device_factory=lambda: [
            PhysicalDummyDevice("healthy"),
            HangingDummyDevice("hanging"),
        ],
    )

    async with app.router.lifespan_context(app):
        snapshots = {
            item.device_id: item for item in app.state.runtime.manager.snapshots()
        }
        assert snapshots["healthy"].available is True
        assert snapshots["hanging"].available is False
        assert app.state.runtime.ready is True
        release_connect.set()


@pytest.mark.asyncio
async def test_runtime_disconnect_updates_http_and_grpc_readiness(
    tmp_path: Path,
) -> None:
    class DisconnectingDummyDevice(PhysicalDummyDevice):
        fail_reads = False

        def read_value(self, channel: str | None = None) -> float:
            if self.fail_reads:
                self._connected = False
                raise OSError("simulated disconnect")
            return super().read_value(channel)

    device = DisconnectingDummyDevice("disconnecting")
    settings = replace(
        LabPCSettings.from_env(tmp_path / "missing.env"),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
        health_sync_interval_seconds=0.01,
    )
    app = create_app(settings=settings, device_factory=lambda: [device])

    async with app.router.lifespan_context(app):
        grpc_port = app.state.runtime.grpc_server.bound_port
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{grpc_port}")
        try:
            stub = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)
            device.fail_reads = True
            with pytest.raises(grpc.aio.AioRpcError):
                await stub.ReadValue(
                    dummy_device_pb2.ReadValueRequest(
                        device_id="disconnecting",
                        operation_id="detect-disconnect",
                    ),
                    metadata=((LEASE_METADATA_KEY, "development-lease"),),
                )

            for _ in range(100):
                if not app.state.runtime.grpc_server.serving:
                    break
                await asyncio.sleep(0.01)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://management.test",
            ) as management_client:
                ready = await management_client.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json()["devices"][0]["status"] == "disconnected"
            assert app.state.runtime.grpc_server.serving is False
        finally:
            await channel.close()
