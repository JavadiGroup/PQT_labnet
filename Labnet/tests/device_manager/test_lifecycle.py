from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Sequence

import pytest

from labnet.device_manager import (
    DeviceManager,
    DeviceSnapshot,
    EndpointDescriptor,
    StaticLeaseValidator,
)


class SimpleDevice:
    device_type = "simple"

    def __init__(self) -> None:
        self._connected = False

    @property
    def device_id(self) -> str:
        return "simple-1"

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> str:
        return "idle" if self._connected else "disconnected"

    def metadata(self) -> dict[str, Any]:
        return {}

    def allowed_methods(self) -> list[str]:
        return ["read"]

    def read(self) -> int:
        return 1


class RecordingCentralClient:
    def __init__(self) -> None:
        self.registered = asyncio.Event()
        self.heartbeat_received = asyncio.Event()
        self.endpoint: EndpointDescriptor | None = None
        self.devices: tuple[DeviceSnapshot, ...] = ()
        self.closed = False

    async def register(
        self,
        *,
        host: str,
        endpoint: EndpointDescriptor,
        devices: Sequence[DeviceSnapshot],
    ) -> None:
        assert host == "lab-pc-1"
        self.endpoint = endpoint
        self.devices = tuple(devices)
        self.registered.set()

    async def heartbeat(
        self,
        *,
        host: str,
        endpoint: EndpointDescriptor,
        devices: Sequence[DeviceSnapshot],
        timestamp: datetime,
    ) -> None:
        assert timestamp.tzinfo is not None
        self.heartbeat_received.set()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_manager_registers_endpoint_and_closes_central_client() -> None:
    central = RecordingCentralClient()
    endpoint = EndpointDescriptor(
        transport="grpc",
        target="lab-pc-1.example:50051",
        api_version="labnet.devices.v1",
        secure=True,
        tls_server_name="lab-pc-1.example",
    )
    manager = DeviceManager(
        [SimpleDevice()],
        StaticLeaseValidator({"lease": "simple-1"}),
        central_client=central,
        host="lab-pc-1",
        endpoint=endpoint,
        heartbeat_interval_seconds=0.05,
    )

    await manager.start()
    await asyncio.wait_for(central.registered.wait(), timeout=1)
    await asyncio.wait_for(central.heartbeat_received.wait(), timeout=1)

    assert central.endpoint == endpoint
    assert central.devices[0].device_id == "simple-1"
    await manager.shutdown()
    assert central.closed is True
