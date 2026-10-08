from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from labnet.client import (
    AsyncContractDeviceClient,
    AsyncLabClient,
    EndpointDescriptor,
    LeaseAuthorityError,
    LeaseGrant,
    LeaseInfo,
    ResourceNotFoundError,
)
from labnet.client.core.models import DeviceInfo
from labnet.devices.core import remote as remote_module
from labnet.devices.core.remote import RemoteDeviceBinding
from labnet.grpc.generated.device_contracts import DUMMY_DEVICE_CONTRACT


def _lease(*, expires_in: float = 2.0, marker: int = 0) -> LeaseInfo:
    now = datetime.now(timezone.utc)
    return LeaseInfo(
        lease_id="lease-1",
        device_id="dummy-1",
        user="alice",
        created_at=now,
        expires_at=now + timedelta(seconds=expires_in + marker),
        state="active",
        last_renewed_at=now if marker else None,
        last_activity_at=now,
        idle_timeout_seconds=900,
    )


def _grant(
    *,
    expires_in: float = 2.0,
    device_type: str = "dummy_device",
) -> LeaseGrant:
    endpoint = EndpointDescriptor(
        transport="grpc",
        target="127.0.0.1:50051",
        api_version="labnet.devices.v1",
        secure=False,
    )
    device = DeviceInfo(
        device_id="dummy-1",
        manager_id="lab-pc-1",
        endpoint=endpoint,
        device_type=device_type,
    )
    return LeaseGrant(lease=_lease(expires_in=expires_in), device=device)


class _FakeSyncGrpc:
    def __init__(self, grant: LeaseGrant, **_: object) -> None:
        self.grant = grant
        self.closed = threading.Event()

    def connect(self, _: float | None = None) -> None:
        pass

    def close(self) -> None:
        self.closed.set()


class _SyncCentral:
    _request_timeout_seconds = 0.2

    def __init__(self, *, terminal: bool = False) -> None:
        self.terminal = terminal
        self.renewed = threading.Event()
        self.released = 0
        self.renewal_result = _lease(expires_in=10, marker=1)

    def list_available_devices(self, **_: object) -> list[DeviceInfo]:
        return [_grant().device]

    def request_lease(self, **_: object) -> LeaseGrant:
        return _grant()

    def renew_lease(self, lease_id: str, duration_seconds: int) -> LeaseInfo:
        assert lease_id == "lease-1"
        assert duration_seconds == 1
        self.renewed.set()
        if self.terminal:
            raise ResourceNotFoundError("expired", error_code="LEASE_EXPIRED")
        return self.renewal_result

    def release_lease(self, _: str) -> None:
        self.released += 1


def test_sync_remote_renews_and_updates_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_module, "ContractDeviceGrpcClient", _FakeSyncGrpc)
    central = _SyncCentral()
    remote = RemoteDeviceBinding(
        central,  # type: ignore[arg-type]
        device_id="dummy-1",
        lease_duration=1,
        renewal_interval_seconds=0.01,
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    assert central.renewed.wait(timeout=1)
    for _ in range(100):
        if remote.lease.last_renewed_at is not None:
            break
        threading.Event().wait(0.001)
    assert remote.lease == central.renewal_result
    remote.close()
    assert central.released == 1


def test_sync_terminal_renewal_closes_transport_and_blocks_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_module, "ContractDeviceGrpcClient", _FakeSyncGrpc)
    central = _SyncCentral(terminal=True)
    remote = RemoteDeviceBinding(
        central,  # type: ignore[arg-type]
        device_id="dummy-1",
        lease_duration=1,
        renewal_interval_seconds=0.01,
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    assert remote.grpc.closed.wait(timeout=1)
    with pytest.raises(LeaseAuthorityError, match="can no longer be used"):
        remote.read_value()
    remote.close()


class _FakeAsyncChannel:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    def unary_unary(self, *_: object, **__: object) -> object:
        return object()

    def unary_stream(self, *_: object, **__: object) -> object:
        return object()

    async def channel_ready(self) -> None:
        pass

    async def close(self) -> None:
        self.closed.set()


class _AsyncCentral:
    def __init__(self) -> None:
        self.renewed = asyncio.Event()
        self.released = 0

    async def renew_lease(self, _: str, __: int) -> LeaseInfo:
        self.renewed.set()
        raise ResourceNotFoundError("expired", error_code="LEASE_EXPIRED")

    async def release_lease(self, _: str) -> None:
        self.released += 1


@pytest.mark.asyncio
async def test_async_terminal_renewal_closes_transport_and_blocks_calls() -> None:
    central = _AsyncCentral()
    channel = _FakeAsyncChannel()
    device = AsyncContractDeviceClient(
        _grant(),
        central=central,
        channel=channel,  # type: ignore[arg-type]
        lease_duration_seconds=1,
        auto_renew=True,
        renewal_interval_seconds=0.01,
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    device._start_auto_renewal()
    await asyncio.wait_for(central.renewed.wait(), timeout=1)
    assert device._renewal_task is not None
    await asyncio.wait_for(device._renewal_task, timeout=1)
    assert device._channel_closed is True
    with pytest.raises(LeaseAuthorityError, match="can no longer be used"):
        await device.read_value()
    await device.aclose()
    assert central.released == 1


@pytest.mark.asyncio
async def test_async_successful_renewal_updates_grant() -> None:
    class RenewThenStop(_AsyncCentral):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.result = _lease(expires_in=10, marker=2)

        async def renew_lease(self, _: str, __: int) -> LeaseInfo:
            self.calls += 1
            if self.calls == 1:
                return self.result
            raise ResourceNotFoundError("released", error_code="LEASE_RELEASED")

    central = RenewThenStop()
    device = AsyncContractDeviceClient(
        _grant(),
        central=central,
        channel=_FakeAsyncChannel(),  # type: ignore[arg-type]
        lease_duration_seconds=1,
        auto_renew=True,
        renewal_interval_seconds=0.01,
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    device._start_auto_renewal()
    assert device._renewal_task is not None
    await asyncio.wait_for(device._renewal_task, timeout=1)
    assert device.grant.lease == central.result
    await device.aclose()


def test_request_lease_uses_renewable_defaults_without_client_idle_override() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        now = datetime.now(timezone.utc)
        payload = {
            "lease": {
                "lease_id": "lease-1",
                "device_id": "dummy-1",
                "user": "alice",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=60)).isoformat(),
                "state": "active",
                "last_renewed_at": now.isoformat(),
                "last_activity_at": now.isoformat(),
                "idle_timeout_seconds": 900,
            },
            "device": {
                "device_id": "dummy-1",
                "manager_id": "lab-pc-1",
                "endpoint": {
                    "transport": "grpc",
                    "target": "127.0.0.1:50051",
                    "api_version": "labnet.devices.v1",
                    "secure": False,
                },
            },
        }
        return httpx.Response(201, json=payload)

    from labnet.client import LabClient

    client = LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        grant = client.request_lease("dummy-1")
    finally:
        client.close()
    body = json.loads(requests[0].content)
    assert body == {"device_id": "dummy-1", "duration_seconds": 60}
    assert grant.lease.last_renewed_at is not None
    assert grant.lease.last_activity_at is not None
    assert grant.lease.idle_timeout_seconds == 900


def test_renewal_interval_validation_only_applies_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_module, "ContractDeviceGrpcClient", _FakeSyncGrpc)
    central = _SyncCentral()
    with pytest.raises(ValueError, match="less than"):
        RemoteDeviceBinding(
            central,  # type: ignore[arg-type]
            device_id="dummy-1",
            lease_duration=1,
            renewal_interval_seconds=1,
            _contract=DUMMY_DEVICE_CONTRACT,
        )
    fixed = RemoteDeviceBinding(
        central,  # type: ignore[arg-type]
        device_id="dummy-1",
        lease_duration=1,
        auto_renew=False,
        renewal_interval_seconds=float("nan"),
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    fixed.close()


def test_sync_close_never_releases_while_renewal_thread_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_module, "ContractDeviceGrpcClient", _FakeSyncGrpc)
    central = _SyncCentral()
    central._request_timeout_seconds = -0.99
    remote = RemoteDeviceBinding(
        central,  # type: ignore[arg-type]
        device_id="dummy-1",
        lease_duration=1,
        auto_renew=False,
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    unblock = threading.Event()
    thread = threading.Thread(target=unblock.wait, daemon=True)
    thread.start()
    remote._renewal_thread = thread

    with pytest.raises(RuntimeError, match="renewal worker"):
        remote.close()
    assert central.released == 0
    assert remote._renewal_thread is thread

    unblock.set()
    thread.join(timeout=1)
    remote.close()
    assert central.released == 1


def test_sync_close_retries_transient_lease_release_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_module, "ContractDeviceGrpcClient", _FakeSyncGrpc)

    class FlakyReleaseCentral(_SyncCentral):
        def __init__(self) -> None:
            super().__init__()
            self.release_attempts = 0

        def release_lease(self, lease_id: str) -> None:
            self.release_attempts += 1
            if self.release_attempts == 1:
                raise RuntimeError("temporary Central outage")
            super().release_lease(lease_id)

    central = FlakyReleaseCentral()
    remote = RemoteDeviceBinding(
        central,  # type: ignore[arg-type]
        device_id="dummy-1",
        auto_renew=False,
        _contract=DUMMY_DEVICE_CONTRACT,
    )

    with pytest.raises(RuntimeError, match="temporary Central outage"):
        remote.close()
    assert remote.grpc.closed.is_set()
    assert remote._lease_released is False
    assert remote.closed is False

    remote.close()
    assert central.release_attempts == 2
    assert central.released == 1
    assert remote.closed is True


@pytest.mark.asyncio
async def test_async_concurrent_close_releases_exactly_once() -> None:
    class BlockingCentral(_AsyncCentral):
        def __init__(self) -> None:
            super().__init__()
            self.release_started = asyncio.Event()
            self.allow_release = asyncio.Event()

        async def release_lease(self, _: str) -> None:
            self.released += 1
            self.release_started.set()
            await self.allow_release.wait()

    central = BlockingCentral()
    device = AsyncContractDeviceClient(
        _grant(),
        central=central,
        channel=_FakeAsyncChannel(),  # type: ignore[arg-type]
        _contract=DUMMY_DEVICE_CONTRACT,
    )
    first = asyncio.create_task(device.aclose())
    await asyncio.wait_for(central.release_started.wait(), timeout=1)
    second = asyncio.create_task(device.aclose())
    await asyncio.sleep(0)
    assert central.released == 1
    central.allow_release.set()
    await asyncio.gather(first, second)
    assert central.released == 1


@pytest.mark.asyncio
async def test_async_close_retries_transient_lease_release_failure() -> None:
    class FlakyReleaseCentral(_AsyncCentral):
        def __init__(self) -> None:
            super().__init__()
            self.release_attempts = 0

        async def release_lease(self, lease_id: str) -> None:
            self.release_attempts += 1
            if self.release_attempts == 1:
                raise RuntimeError("temporary Central outage")
            await super().release_lease(lease_id)

    central = FlakyReleaseCentral()
    device = AsyncContractDeviceClient(
        _grant(),
        central=central,
        channel=_FakeAsyncChannel(),  # type: ignore[arg-type]
        _contract=DUMMY_DEVICE_CONTRACT,
    )

    with pytest.raises(RuntimeError, match="temporary Central outage"):
        await device.aclose()
    assert device._channel_closed is True
    assert device._lease_released is False

    await device.aclose()
    assert central.release_attempts == 2
    assert central.released == 1
    assert device._lease_released is True


def test_stale_activity_does_not_shorten_client_liveness_deadline() -> None:
    now = datetime.now(timezone.utc)
    lease = LeaseInfo(
        lease_id="lease-1",
        device_id="dummy-1",
        user="alice",
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(seconds=30),
        state="active",
        last_activity_at=now - timedelta(minutes=20),
        idle_timeout_seconds=900,
    )
    assert lease.expired is False


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_central_client_rejects_invalid_request_timeout(timeout: float) -> None:
    from labnet.client import AsyncLabClient, LabClient

    with pytest.raises(ValueError, match="finite"):
        LabClient(
            "https://central.example",
            "secret",
            timeout_seconds=timeout,
            authenticate_on_start=False,
        )
    with pytest.raises(ValueError, match="finite"):
        AsyncLabClient(
            "https://central.example",
            "secret",
            timeout_seconds=timeout,
            authenticate_on_start=False,
        )


@pytest.mark.asyncio
async def test_async_lab_client_close_drains_in_flight_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labnet.client import AsyncLabClient
    from labnet.client.core import grpc_client as grpc_module

    connect_started = asyncio.Event()
    allow_connect = asyncio.Event()
    delete_count = 0

    class BlockingDevice:
        def __init__(self, grant: LeaseGrant, *, central: AsyncLabClient, **_: object):
            self.grant = grant
            self.central = central
            self.closed = False

        async def connect(self, _: float) -> None:
            connect_started.set()
            await allow_connect.wait()

        def _start_auto_renewal(self) -> None:
            pass

        async def aclose(self) -> None:
            if self.closed:
                return
            await self.central.release_lease(self.grant.lease.lease_id)
            self.closed = True
            self.central._discard_device(self)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_count
        if request.method == "POST" and request.url.path == "/api/v1/leases":
            grant = _grant(expires_in=60)
            return httpx.Response(
                201,
                json={
                    "lease": {
                        "lease_id": grant.lease.lease_id,
                        "device_id": grant.lease.device_id,
                        "user": grant.lease.user,
                        "created_at": grant.lease.created_at.isoformat(),
                        "expires_at": grant.lease.expires_at.isoformat(),
                        "state": "active",
                    },
                    "device": {
                        "device_id": grant.device.device_id,
                        "manager_id": grant.device.manager_id,
                        "device_type": grant.device.device_type,
                        "endpoint": {
                            "transport": "grpc",
                            "target": "127.0.0.1:50051",
                            "api_version": "labnet.devices.v1",
                            "secure": False,
                        },
                    },
                },
            )
        if request.method == "DELETE":
            delete_count += 1
            return httpx.Response(200, json={"state": "released"})
        return httpx.Response(404)

    monkeypatch.setattr(grpc_module, "AsyncContractDeviceClient", BlockingDevice)
    client = AsyncLabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    )
    acquire = asyncio.create_task(
        client.acquire_device(DUMMY_DEVICE_CONTRACT, "dummy-1", duration_seconds=60)
    )
    await asyncio.wait_for(connect_started.wait(), timeout=1)
    close = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    assert client._closing is True
    assert close.done() is False

    allow_connect.set()
    device = await asyncio.wait_for(acquire, timeout=1)
    await asyncio.wait_for(close, timeout=1)
    assert device.closed is True
    assert delete_count == 1
    with pytest.raises(RuntimeError, match="closing or closed"):
        await client.acquire_device(DUMMY_DEVICE_CONTRACT, "dummy-1")


@pytest.mark.asyncio
async def test_async_acquire_releases_a_wrong_family_grant_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from labnet.client.core import grpc_client as grpc_module

    client_was_constructed = False
    released_lease_ids: list[str] = []

    class MustNotConstruct:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal client_was_constructed
            client_was_constructed = True
            raise AssertionError("gRPC client must not be constructed")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/leases":
            grant = _grant(expires_in=60, device_type="wavemeter")
            return httpx.Response(
                201,
                json={
                    "lease": {
                        "lease_id": grant.lease.lease_id,
                        "device_id": grant.lease.device_id,
                        "user": grant.lease.user,
                        "created_at": grant.lease.created_at.isoformat(),
                        "expires_at": grant.lease.expires_at.isoformat(),
                        "state": "active",
                    },
                    "device": {
                        "device_id": grant.device.device_id,
                        "manager_id": grant.device.manager_id,
                        "device_type": grant.device.device_type,
                        "endpoint": {
                            "transport": "grpc",
                            "target": "127.0.0.1:50051",
                            "api_version": "labnet.devices.v1",
                            "secure": False,
                        },
                    },
                },
            )
        if request.method == "DELETE":
            released_lease_ids.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"state": "released"})
        return httpx.Response(404)

    monkeypatch.setattr(
        grpc_module,
        "AsyncContractDeviceClient",
        MustNotConstruct,
    )
    client = AsyncLabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="LeaseGrant device_type") as caught:
            await client.acquire_device(
                DUMMY_DEVICE_CONTRACT,
                "dummy-1",
                auto_renew=False,
            )
    finally:
        await client.aclose()

    assert "requested contract device_type 'dummy_device'" in str(caught.value)
    assert client_was_constructed is False
    assert released_lease_ids == ["lease-1"]
    assert client._devices == set()
    assert client._active_acquires == 0


@pytest.mark.asyncio
async def test_async_lab_client_concurrent_close_is_idempotent() -> None:
    from labnet.client import AsyncLabClient

    class CountingTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.close_count = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        async def aclose(self) -> None:
            self.close_count += 1

    transport = CountingTransport()
    client = AsyncLabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=transport,
    )
    await asyncio.gather(client.aclose(), client.aclose(), client.aclose())
    assert transport.close_count == 1
