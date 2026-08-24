from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from labnet.device_manager import (
    HttpLeaseValidator,
    InvalidConfigurationError,
    LeaseInvalidError,
)


@pytest.mark.asyncio
async def test_http_lease_validator_accepts_matching_unexpired_grant() -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/leases/lease-1/validate"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "valid": True,
                "lease_id": "lease-1",
                "device_id": "device-1",
                "user": "researcher",
                "expires_at": expires_at.isoformat(),
            },
        )

    client = httpx.AsyncClient(
        base_url="https://central.example",
        transport=httpx.MockTransport(handler),
    )
    validator = HttpLeaseValidator(
        server_url="https://central.example",
        api_key="test-key",
        client=client,
    )
    try:
        grant = await validator.validate(
            lease_id="lease-1",
            device_id="device-1",
        )
        assert grant.user == "researcher"
        assert grant.expires_at == expires_at
    finally:
        await validator.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_http_lease_validator_fails_closed_on_denial() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "expired"})

    client = httpx.AsyncClient(
        base_url="https://central.example",
        transport=httpx.MockTransport(handler),
    )
    validator = HttpLeaseValidator(
        server_url="https://central.example",
        api_key="test-key",
        client=client,
    )
    try:
        with pytest.raises(LeaseInvalidError):
            await validator.validate(
                lease_id="expired",
                device_id="device-1",
            )
    finally:
        await validator.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_http_lease_validator_encodes_lease_as_one_path_segment() -> None:
    lease_id = "lease/with?reserved#characters"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == (
            b"/api/v1/leases/lease%2Fwith%3Freserved%23characters/validate"
        )
        return httpx.Response(403)

    client = httpx.AsyncClient(
        base_url="https://central.example",
        transport=httpx.MockTransport(handler),
    )
    validator = HttpLeaseValidator(
        server_url="https://central.example",
        api_key="test-key",
        client=client,
    )
    try:
        with pytest.raises(LeaseInvalidError):
            await validator.validate(lease_id=lease_id, device_id="device-1")
    finally:
        await validator.close()
        await client.aclose()


def test_remote_central_server_requires_https() -> None:
    with pytest.raises(InvalidConfigurationError, match="use HTTPS"):
        HttpLeaseValidator(
            server_url="http://192.0.2.10:8008",
            api_key="test-key",
        )
