from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from central_server.app.config import Settings
from central_server.app.main import create_app
from central_server.app.services.auth import UserService
from labnet.client import AsyncLabClient
from labnet.device_manager import (
    DeviceManager,
    EndpointDescriptor,
    HttpCentralRegistryClient,
    HttpLeaseValidator,
)
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.grpc import GrpcServerConfig, LabnetGrpcServer


CENTRAL_URL = "http://127.0.0.1"
MANAGER_ID = "integration-lab-pc"
DEVICE_ID = "integration-dummy"


def _central_settings(database_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{database_path.as_posix()}",
        lease_min_seconds=1,
        lease_max_seconds=60,
        heartbeat_timeout_seconds=30,
        maintenance_interval_seconds=60,
        create_schema_on_startup=True,
    )


async def _wait_for_registration(manager: DeviceManager) -> None:
    for _ in range(100):
        if manager.registered:
            return
        if manager.last_central_error is not None:
            raise AssertionError("Device Manager registration failed") from (
                manager.last_central_error
            )
        await asyncio.sleep(0.01)
    raise AssertionError("Device Manager did not register with Central")


@pytest.mark.asyncio
async def test_full_stack_discovery_control_stream_and_release(tmp_path: Path) -> None:
    central_app = create_app(_central_settings(tmp_path / "central.db"))
    users: UserService = central_app.state.user_service
    manager_key = users.create_user(MANAGER_ID, "device_manager")["api_key"]
    user_key = users.create_user("integration-researcher", "user")["api_key"]

    manager_http = httpx.AsyncClient(
        base_url=CENTRAL_URL,
        transport=httpx.ASGITransport(app=central_app),
    )
    registry_http = httpx.AsyncClient(
        base_url=CENTRAL_URL,
        transport=httpx.ASGITransport(app=central_app),
    )
    lease_validator = HttpLeaseValidator(
        server_url=CENTRAL_URL,
        api_key=manager_key,
        client=manager_http,
    )
    registry = HttpCentralRegistryClient(
        manager_id=MANAGER_ID,
        server_url=CENTRAL_URL,
        api_key=manager_key,
        client=registry_http,
    )
    manager = DeviceManager(
        [PhysicalDummyDevice(DEVICE_ID)],
        lease_validator,
        central_client=registry,
        host=MANAGER_ID,
        endpoint=EndpointDescriptor(
            transport="grpc",
            target="127.0.0.1:1",
            api_version="labnet.devices.v1",
            secure=False,
        ),
        heartbeat_interval_seconds=60,
    )
    grpc_server = LabnetGrpcServer(
        manager.executor,
        config=GrpcServerConfig(bind="127.0.0.1:0"),
    )
    manager.endpoint = EndpointDescriptor(
        transport="grpc",
        target=f"127.0.0.1:{grpc_server.bound_port}",
        api_version="labnet.devices.v1",
        secure=False,
    )

    try:
        async with central_app.router.lifespan_context(central_app):
            try:
                await manager.start()
                await grpc_server.start()
                await _wait_for_registration(manager)
                await manager.send_heartbeat()

                async with AsyncLabClient(
                    CENTRAL_URL,
                    user_key,
                    transport=httpx.ASGITransport(app=central_app),
                ) as lab:
                    assert lab.current_user is not None
                    assert lab.current_user.user == "integration-researcher"

                    available = await lab.list_available_devices("dummy_device")
                    assert [device.device_id for device in available] == [DEVICE_ID]
                    assert available[0].endpoint.target == (
                        f"127.0.0.1:{grpc_server.bound_port}"
                    )

                    remote = await lab.acquire_dummy(
                        duration_seconds=10,
                        idempotency_key="full-stack-acquisition",
                    )
                    try:
                        assert (
                            await lab.get_device(DEVICE_ID)
                        ).effective_available is False

                        written = await remote.write_value(12.5, channel="phase")
                        assert written.value == 12.5
                        assert written.channel == "phase"

                        read = await remote.read_value(channel="phase")
                        assert read.value == 12.5
                        assert read.channel == "phase"

                        samples = [
                            sample
                            async for sample in remote.stream_data(
                                sample_rate_hz=100,
                                sample_count=3,
                                channel="phase",
                            )
                        ]
                        assert [sample.sequence for sample in samples] == [0, 1, 2]
                        assert [sample.value for sample in samples] == [
                            12.5,
                            12.5,
                            12.5,
                        ]
                    finally:
                        await remote.aclose()

                    restored = await lab.list_available_devices("dummy_device")
                    assert [device.device_id for device in restored] == [DEVICE_ID]
                    assert (
                        await lab.get_device(DEVICE_ID)
                    ).effective_available is True
            finally:
                try:
                    await grpc_server.stop(grace=0)
                finally:
                    await manager.shutdown()
    finally:
        await asyncio.gather(manager_http.aclose(), registry_http.aclose())
