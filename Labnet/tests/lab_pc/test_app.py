from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import replace
from pathlib import Path

import grpc
import httpx
import pytest

from labnet.lab_pc.app import create_app, load_device_factory, resolve_lab_pc_id
from labnet.lab_pc.config import LabPCSettings
from labnet.lab_pc.inventory import simulated_devices
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.grpc import LEASE_METADATA_KEY
from labnet.grpc.generated import dummy_device_pb2, dummy_device_pb2_grpc
from labnet.grpc.generated import transfer_pb2


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_lab_pc_composes_health_control_and_streaming(tmp_path: Path) -> None:
    settings = replace(
        LabPCSettings.from_env(search_cwd=False),
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
        LabPCSettings.from_env(search_cwd=False),
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
        LabPCSettings.from_env(search_cwd=False),
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


def test_missing_inventory_serves_one_simulated_device() -> None:
    assert load_device_factory(None) is simulated_devices


def test_inventory_can_be_a_module(monkeypatch: pytest.MonkeyPatch) -> None:
    devices = load_device_factory("labnet.lab_pc.inventory:simulated_devices")()

    assert [device.device_id for device in devices] == ["dummy_device_1"]


def test_inventory_can_be_a_file_path(tmp_path: Path) -> None:
    inventory = tmp_path / "lab_pc_01.py"
    inventory.write_text(
        "from labnet.devices.DummyDevice.physical import PhysicalDummyDevice\n"
        "def create_devices():\n"
        "    return [PhysicalDummyDevice('bench-a'), PhysicalDummyDevice('bench-b')]\n",
        encoding="utf-8",
    )

    # tmp_path is absolute, so on Windows this spec also contains "C:".
    devices = load_device_factory(f"{inventory}:create_devices")()

    assert [device.device_id for device in devices] == ["bench-a", "bench-b"]


@pytest.mark.parametrize(
    "spec",
    [
        "labnet.lab_pc.inventory",
        "labnet.lab_pc.inventory:missing",
        "C:/does/not/exist.py:create_devices",
    ],
)
def test_invalid_inventory_is_rejected(spec: str) -> None:
    with pytest.raises(ValueError, match="LABNET_DEVICE_INVENTORY"):
        load_device_factory(spec)


def _settings_with_key(monkeypatch: pytest.MonkeyPatch) -> LabPCSettings:
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "manager-key")
    return LabPCSettings.from_env(search_cwd=False)


@pytest.mark.asyncio
async def test_lab_pc_id_is_resolved_from_the_key_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def central(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/me"
        assert request.headers["Authorization"] == "Bearer manager-key"
        return httpx.Response(200, json={"user": "lab-pc-07", "role": "device_manager"})

    lab_pc_id = await resolve_lab_pc_id(
        _settings_with_key(monkeypatch),
        transport=httpx.MockTransport(central),
    )

    assert lab_pc_id == "lab-pc-07"


@pytest.mark.asyncio
async def test_lab_pc_id_resolution_requires_a_device_manager_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"user": "alice", "role": "user"})
    )

    with pytest.raises(RuntimeError, match="needs a device_manager key"):
        await resolve_lab_pc_id(_settings_with_key(monkeypatch), transport=transport)
