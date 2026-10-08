from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from labnet.client import AsyncLabClient, AuthenticationError, LabClient
from labnet.devices.core.remote import RemoteDeviceBinding
from labnet_sim._generated.contracts import DUMMY_DEVICE_CONTRACT


NOW = datetime.now(timezone.utc)


def endpoint() -> dict[str, object]:
    return {
        "transport": "grpc",
        "target": "127.0.0.1:50051",
        "api_version": "labnet.v3",
        "secure": False,
        "attributes": {},
    }


def device(*, effective_available: bool = True) -> dict[str, object]:
    return {
        "device_id": "dummy-1",
        "manager_id": "lab-pc-1",
        "endpoint": endpoint(),
        "device_type": "dummy_device",
        "runtime_state": "ready",
        "availability": "available",
        "status_text": "OK",
        "allocation_state": "free" if effective_available else "leased",
        "effective_available": effective_available,
        "last_seen": NOW.isoformat(),
        "metadata": {"unit": "Hz"},
    }


def lease() -> dict[str, object]:
    return {
        "lease_id": "lease-1",
        "device_id": "dummy-1",
        "user": "alice",
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
        "state": "active",
    }


@pytest.mark.asyncio
async def test_async_central_v1_discovery_and_lease_lifecycle() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json={"user": "alice", "role": "user"})
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "alive"})
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v1/devices":
            return httpx.Response(
                200, json={"devices": [device(), device(effective_available=False)]}
            )
        if request.url.path == "/api/v1/leases" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "lease": lease(),
                    "device": {
                        "device_id": "dummy-1",
                        "manager_id": "lab-pc-1",
                        "endpoint": endpoint(),
                    },
                },
            )
        if request.url.path == "/api/v1/leases/lease-1/renew":
            return httpx.Response(200, json=lease())
        if request.url.path == "/api/v1/leases/lease-1" and request.method == "DELETE":
            return httpx.Response(
                200, json={"lease_id": "lease-1", "state": "released", "released": True}
            )
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": "missing"}})

    transport = httpx.MockTransport(handler)
    async with AsyncLabClient(
        "https://central.example", "secret", transport=transport
    ) as client:
        assert client.current_user is not None
        assert client.current_user.user == "alice"
        assert (await client.health())["status"] == "ready"
        assert (await client.health_live())["status"] == "alive"
        available = await client.list_available_devices("dummy_device")
        assert [record.device_id for record in available] == ["dummy-1"]
        grant = await client.request_lease(
            "dummy-1", 600, idempotency_key="stable-attempt-1"
        )
        assert grant.lease.lease_id == "lease-1"
        assert grant.device.endpoint.target == "127.0.0.1:50051"
        renewed = await client.renew_lease("lease-1", 600)
        assert renewed.state == "active"
        await client.release_lease("lease-1")

    list_request = next(request for request in requests if request.url.path.endswith("/devices"))
    assert list_request.url.params["device_type"] == "dummy_device"
    assert list_request.url.params["availability"] == "available"
    lease_request = next(
        request
        for request in requests
        if request.url.path == "/api/v1/leases" and request.method == "POST"
    )
    assert lease_request.headers["Idempotency-Key"] == "stable-attempt-1"
    assert lease_request.headers["Authorization"] == "Bearer secret"


def test_sync_client_uses_v1_and_idempotency_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "lease": lease(),
                "device": {
                    "device_id": "dummy-1",
                    "manager_id": "lab-pc-1",
                    "endpoint": endpoint(),
                },
            },
        )

    with LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    ) as client:
        grant = client.request_lease("dummy-1", 60, idempotency_key="same-request")
    assert grant.lease.device_id == "dummy-1"
    assert requests[0].url.path == "/api/v1/leases"
    assert requests[0].headers["Idempotency-Key"] == "same-request"


def test_remote_plaintext_central_is_rejected() -> None:
    with pytest.raises(ValueError, match="loopback"):
        AsyncLabClient("http://central.example", "secret")


class ClosingSyncTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad credential"})

    def close(self) -> None:
        self.closed = True


class ClosingAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad credential"})

    async def aclose(self) -> None:
        self.closed = True


def test_sync_eager_authentication_failure_closes_http_transport() -> None:
    transport = ClosingSyncTransport()
    with pytest.raises(AuthenticationError):
        LabClient("https://central.example", "bad", transport=transport)
    assert transport.closed is True


@pytest.mark.asyncio
async def test_async_context_authentication_failure_closes_http_transport() -> None:
    transport = ClosingAsyncTransport()
    client = AsyncLabClient("https://central.example", "bad", transport=transport)
    with pytest.raises(AuthenticationError):
        async with client:
            pass
    assert transport.closed is True


def invalid_endpoint() -> dict[str, object]:
    value = endpoint()
    value["target"] = "remote-lab-pc.example:50051"
    return value


def grant_with_invalid_endpoint() -> dict[str, object]:
    return {
        "lease": lease(),
        "device": {
            "device_id": "dummy-1",
            "manager_id": "lab-pc-1",
            "device_type": "dummy_device",
            "endpoint": invalid_endpoint(),
        },
    }


@pytest.mark.asyncio
async def test_async_acquire_rolls_back_lease_when_device_construction_fails() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=grant_with_invalid_endpoint())
        return httpx.Response(
            200, json={"lease_id": "lease-1", "state": "released", "released": True}
        )

    client = AsyncLabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="loopback"):
            await client.acquire_device(DUMMY_DEVICE_CONTRACT, "dummy-1")
        assert [(request.method, request.url.path) for request in requests] == [
            ("POST", "/api/v1/leases"),
            ("DELETE", "/api/v1/leases/lease-1"),
        ]
    finally:
        await client.aclose()


def test_sync_remote_rolls_back_lease_when_grpc_construction_fails() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=grant_with_invalid_endpoint())
        return httpx.Response(
            200, json={"lease_id": "lease-1", "state": "released", "released": True}
        )

    client = LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="loopback"):
            RemoteDeviceBinding(client, device_id="dummy-1", _contract=DUMMY_DEVICE_CONTRACT)
        assert [(request.method, request.url.path) for request in requests] == [
            ("POST", "/api/v1/leases"),
            ("DELETE", "/api/v1/leases/lease-1"),
        ]
    finally:
        client.close()
