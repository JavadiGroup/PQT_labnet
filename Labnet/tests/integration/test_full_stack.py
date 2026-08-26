from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from labnet.central.config import Settings
from labnet.central.main import create_app
from labnet.central.services.auth import UserService
from labnet.client import AsyncLabClient, LeaseAuthorityError
from labnet.device_manager import (
    DeviceManager,
    EndpointDescriptor,
    HttpCentralRegistryClient,
    HttpLeaseValidator,
)
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.grpc import GrpcServerConfig, LabnetGrpcServer, StreamPolicy


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


async def _wait_until(
    predicate: Callable[[], bool],
    description: str,
    *,
    timeout_seconds: float = 3.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {description}.")


@dataclass(slots=True)
class _RenewalStack:
    app: object
    user_key: str
    other_user_key: str
    manager: DeviceManager
    grpc_server: LabnetGrpcServer

    def client(self, api_key: str | None = None) -> AsyncLabClient:
        return AsyncLabClient(
            CENTRAL_URL,
            api_key or self.user_key,
            transport=httpx.ASGITransport(app=self.app),
        )


@asynccontextmanager
async def _renewal_stack(
    tmp_path: Path,
    *,
    idle_timeout_seconds: int = 10,
    stream_revalidation_seconds: float = 0.15,
) -> AsyncIterator[_RenewalStack]:
    settings = replace(
        _central_settings(tmp_path / "renewal-central.db"),
        lease_default_idle_timeout_seconds=idle_timeout_seconds,
        maintenance_interval_seconds=0.05,
    )
    central_app = create_app(settings)
    users: UserService = central_app.state.user_service
    manager_key = users.create_user(MANAGER_ID, "device_manager")["api_key"]
    user_key = users.create_user("renewal-researcher", "user")["api_key"]
    other_user_key = users.create_user("replacement-researcher", "user")[
        "api_key"
    ]

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
        heartbeat_interval_seconds=5,
    )
    grpc_server = LabnetGrpcServer(
        manager.executor,
        config=GrpcServerConfig(bind="127.0.0.1:0"),
        stream_policy=StreamPolicy(
            lease_revalidation_interval_seconds=stream_revalidation_seconds,
        ),
    )
    manager.endpoint = EndpointDescriptor(
        transport="grpc",
        target=f"127.0.0.1:{grpc_server.bound_port}",
        api_version="labnet.devices.v1",
        secure=False,
    )
    stack = _RenewalStack(
        app=central_app,
        user_key=user_key,
        other_user_key=other_user_key,
        manager=manager,
        grpc_server=grpc_server,
    )

    try:
        async with central_app.router.lifespan_context(central_app):
            try:
                await grpc_server.start()
                await manager.start()
                await _wait_for_registration(manager)
                await manager.send_heartbeat()
                yield stack
            finally:
                try:
                    await grpc_server.stop(grace=0)
                finally:
                    await manager.shutdown()
    finally:
        await asyncio.gather(manager_http.aclose(), registry_http.aclose())


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


@pytest.mark.asyncio
async def test_auto_renew_keeps_device_usable_beyond_original_ttl(
    tmp_path: Path,
) -> None:
    async with _renewal_stack(tmp_path) as stack:
        async with stack.client() as lab:
            remote = await lab.acquire_dummy(
                DEVICE_ID,
                duration_seconds=1,
                renewal_interval_seconds=0.2,
            )
            try:
                original_expires_at = remote.grant.lease.expires_at
                await _wait_until(
                    lambda: remote.grant.lease.expires_at > original_expires_at,
                    "the in-memory lease expiry to advance",
                )
                await _wait_until(
                    lambda: datetime.now(timezone.utc) > original_expires_at,
                    "the original lease TTL to elapse",
                )

                written = await remote.write_value(27.5, channel="renewed")
                read = await remote.read_value(channel="renewed")
                assert written.value == 27.5
                assert read.value == 27.5
                assert remote.grant.lease.expires_at > original_expires_at
                assert remote.grant.lease.last_renewed_at is not None
            finally:
                await remote.aclose()


@pytest.mark.asyncio
async def test_idle_timeout_revokes_quiet_auto_renewing_client_and_frees_slot(
    tmp_path: Path,
) -> None:
    async with _renewal_stack(tmp_path, idle_timeout_seconds=1) as stack:
        async with stack.client() as first_lab, stack.client(
            stack.other_user_key
        ) as replacement_lab:
            quiet = await first_lab.acquire_dummy(
                DEVICE_ID,
                duration_seconds=2,
                renewal_interval_seconds=0.2,
            )
            replacement = None
            try:
                await _wait_until(
                    lambda: quiet._authority_error is not None,
                    "Central idle expiry to terminate client authority",
                )
                assert isinstance(quiet._authority_error, LeaseAuthorityError)
                assert quiet._channel_closed is True
                with pytest.raises(LeaseAuthorityError):
                    await quiet.read_value()

                await _wait_until(
                    lambda: stack.app.state.lease_service.list_active() == [],
                    "the expired lease's active slot to be freed",
                )
                replacement = await replacement_lab.acquire_dummy(
                    DEVICE_ID,
                    duration_seconds=2,
                    renewal_interval_seconds=0.2,
                )
                assert replacement.lease_id != quiet.lease_id
                assert (await replacement.read_value()).value == 0.0
            finally:
                if replacement is not None:
                    await replacement.aclose()
                await quiet.aclose()


@pytest.mark.asyncio
async def test_continuous_stream_stays_authorized_beyond_original_ttl(
    tmp_path: Path,
) -> None:
    async with _renewal_stack(
        tmp_path,
        idle_timeout_seconds=1,
        stream_revalidation_seconds=0.15,
    ) as stack:
        async with stack.client() as lab:
            remote = await lab.acquire_dummy(
                DEVICE_ID,
                duration_seconds=1,
                renewal_interval_seconds=0.2,
            )
            try:
                original_expires_at = remote.grant.lease.expires_at
                samples = [
                    sample
                    async for sample in remote.stream_data(
                        sample_rate_hz=20,
                        sample_count=26,
                        channel="continuous",
                    )
                ]
                assert len(samples) == 26
                assert [sample.sequence for sample in samples] == list(range(26))
                assert datetime.now(timezone.utc) > original_expires_at
                assert remote.grant.lease.expires_at > original_expires_at
                assert remote._authority_error is None
            finally:
                await remote.aclose()


@pytest.mark.asyncio
async def test_fixed_ttl_without_auto_renew_expires_and_frees_slot(
    tmp_path: Path,
) -> None:
    async with _renewal_stack(tmp_path) as stack:
        async with stack.client() as first_lab, stack.client(
            stack.other_user_key
        ) as replacement_lab:
            fixed = await first_lab.acquire_dummy(
                DEVICE_ID,
                duration_seconds=1,
                auto_renew=False,
            )
            replacement = None
            try:
                await _wait_until(
                    lambda: stack.app.state.lease_service.list_active() == [],
                    "the fixed lease to expire and free its active slot",
                )
                with pytest.raises(LeaseAuthorityError):
                    await fixed.read_value()

                replacement = await replacement_lab.acquire_dummy(
                    DEVICE_ID,
                    duration_seconds=2,
                    renewal_interval_seconds=0.2,
                )
                assert replacement.lease_id != fixed.lease_id
                assert (await replacement.write_value(4.0)).value == 4.0
            finally:
                if replacement is not None:
                    await replacement.aclose()
                await fixed.aclose()
