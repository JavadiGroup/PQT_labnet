from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from labnet.device_manager import (
    DeviceAvailability,
    DeviceSnapshot,
    DeviceState,
    EndpointDescriptor,
    HttpCentralRegistryClient,
    InvalidConfigurationError,
)


def _snapshot() -> DeviceSnapshot:
    return DeviceSnapshot(
        device_id="wavemeter-1",
        device_type="wavemeter",
        state=DeviceState.READY,
        status="locked",
        availability=DeviceAvailability.AVAILABLE,
        metadata={"room": "A12"},
        allowed_methods=("read_value", "stream_data"),
    )


def _endpoint() -> EndpointDescriptor:
    return EndpointDescriptor(
        transport="grpc",
        target="lab-pc-1.example:50051",
        api_version="labnet.devices.v1",
        secure=True,
        tls_server_name="lab-pc-1.example",
        attributes={"site": "quantum-lab"},
    )


@pytest.mark.asyncio
async def test_registry_uses_v1_contract_for_registration_and_heartbeat() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        assert request.headers["authorization"] == "Bearer manager-secret"
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://central.example",
        transport=transport,
    )
    registry = HttpCentralRegistryClient(
        manager_id="optics-rack-01",
        server_url="https://central.example",
        api_key="manager-secret",
        client=http_client,
    )
    timestamp = datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)
    try:
        await registry.register(
            host="lab-pc-display-name",
            endpoint=_endpoint(),
            devices=[_snapshot()],
        )
        await registry.heartbeat(
            host="lab-pc-display-name",
            endpoint=_endpoint(),
            devices=[_snapshot()],
            timestamp=timestamp,
        )
    finally:
        await registry.close()
        await http_client.aclose()

    assert [path for path, _payload in requests] == [
        "/api/v1/device-managers/register",
        "/api/v1/device-managers/heartbeat",
    ]
    registration = requests[0][1]
    assert registration == {
        "manager_id": "optics-rack-01",
        "host": "lab-pc-display-name",
        "endpoint": {
            "transport": "grpc",
            "target": "lab-pc-1.example:50051",
            "api_version": "labnet.devices.v1",
            "secure": True,
            "tls_server_name": "lab-pc-1.example",
            "attributes": {"site": "quantum-lab"},
        },
        "devices": [
            {
                "device_id": "wavemeter-1",
                "device_type": "wavemeter",
                "runtime_state": "ready",
                "availability": "available",
                "status": "locked",
                "metadata": {
                    "room": "A12",
                    "capabilities": ["read_value", "stream_data"],
                },
            }
        ],
    }
    heartbeat = requests[1][1]
    assert heartbeat["manager_id"] == "optics-rack-01"
    assert heartbeat["host"] == "lab-pc-display-name"
    assert heartbeat["endpoint"] == registration["endpoint"]
    assert heartbeat["timestamp"] == "2026-08-24T12:30:00+00:00"
    assert heartbeat["devices"] == [
        {
            "device_id": "wavemeter-1",
            "runtime_state": "ready",
            "availability": "available",
            "status": "locked",
            "metadata": {
                "room": "A12",
                "capabilities": ["read_value", "stream_data"],
            },
        }
    ]


def test_registry_requires_stable_manager_id() -> None:
    with pytest.raises(InvalidConfigurationError, match="manager_id"):
        HttpCentralRegistryClient(
            manager_id="  ",
            server_url="https://central.example",
            api_key="manager-secret",
        )
