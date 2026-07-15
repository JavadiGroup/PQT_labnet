from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

class LabPCDeviceManager:
    def __init__(
        self,
        server_url: str,
        api_key: str,
        host: str,
        rpc_url: str,
        devices: list,
        heartbeat_interval_seconds: float = 5.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.host = host
        self.rpc_url = rpc_url
        self.devices = devices
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

        self.registered = False

        self.http = httpx.AsyncClient(
            base_url=self.server_url,
            timeout=10.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    async def start(self) -> None:
        """
        Start local device manager state.

        This should not block forever.
        """
        self.connect_devices()

    async def shutdown(self) -> None:
        """Disconnect devices and close HTTP resources."""
        self.disconnect_devices()
        await self.http.aclose()

    def connect_devices(self) -> None:
        for device in self.devices:
            print(f"Connecting {device.device_id}...")
            device.connect()

    def disconnect_devices(self) -> None:
        for device in self.devices:
            try:
                print(f"Disconnecting {device.device_id}...")
                device.disconnect()
            except Exception as exc:
                print(f"Error disconnecting {device.device_id}: {exc}")

    def get_device(self, device_id: str):
        for device in self.devices:
            if device.device_id == device_id:
                return device

        return None

    async def register_devices(self) -> None:
        payload = {
            "host": self.host,
            "rpc_url": self.rpc_url,
            "devices": [
                self._device_registration_payload(device)
                for device in self.devices
            ],
        }

        response = await self.http.post(
            "/devices/register",
            json=payload,
        )

        response.raise_for_status()

        self.registered = True

        print("Registered devices with Central Server.")

    async def send_heartbeat(self) -> None:
        payload = {
            "host": self.host,
            "rpc_url": self.rpc_url,
            "timestamp": self._utc_now_iso(),
            "devices": [
                self._device_heartbeat_payload(device)
                for device in self.devices
            ],
        }

        response = await self.http.post(
            "/devices/heartbeat",
            json=payload,
        )

        response.raise_for_status()

        print(f"Heartbeat sent at {payload['timestamp']}")

    async def heartbeat_loop(self) -> None:
        """
        Register once, then heartbeat forever.

        If the Central Server goes down, keep retrying.
        """
        while True:
            try:
                if not self.registered:
                    await self.register_devices()

                await self.send_heartbeat()

            except Exception as exc:
                self.registered = False
                print(f"Central Server communication failed: {exc}")
                print("Will retry registration...")

            await asyncio.sleep(self.heartbeat_interval_seconds)

    async def validate_lease(
        self,
        lease_id: str,
        device_id: str,
    ) -> None:
        response = await self.http.post(
            f"/leases/{lease_id}/validate",
            json={
                "device_id": device_id,
            },
        )

        if response.status_code != 200:
            raise RuntimeError(response.text)

    def _device_registration_payload(self, device) -> dict[str, Any]:
        return {
            "device_id": device.device_id,
            "device_type": device.device_type,
            "status": device.status(),
            "metadata": device.metadata(),
        }

    def _device_heartbeat_payload(self, device) -> dict[str, Any]:
        return {
            "device_id": device.device_id,
            "status": device.status(),
            "metadata": device.metadata(),
        }

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")