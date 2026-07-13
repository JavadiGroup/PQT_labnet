from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from dotenv import load_dotenv

import httpx

from devices.registry import create_devices

class LabPCDeviceManager:
    """
    Generic lab PC device manager.

    This file should be identical on every lab computer.
    The only thing that changes from PC to PC is devices/registry.py
    and the device classes inside devices/.
    """

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

        self.http = httpx.Client(
            base_url=self.server_url,
            timeout=10.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def connect_devices(self) -> None:
        """Connect all configured local devices."""
        for device in self.devices:
            print(f"Connecting {device.device_id}...")
            device.connect()

    def disconnect_devices(self) -> None:
        """Disconnect all local devices."""
        for device in self.devices:
            try:
                device.disconnect()
            except Exception as exc:
                print(f"Error disconnecting {device.device_id}: {exc}")

    def register_devices(self) -> None:
        """Register all local devices with the Central Server."""
        payload = {
            "host": self.host,
            "rpc_url": self.rpc_url,
            "devices": [
                self._device_registration_payload(device)
                for device in self.devices
            ],
        }

        response = self.http.post(
            "/devices/register",
            json=payload,
        )

        response.raise_for_status()

        print("Registered devices:")
        print(response.json())

    def send_heartbeat(self) -> None:
        """Send one heartbeat to the Central Server."""
        payload = {
            "host": self.host,
            "rpc_url": self.rpc_url,
            "timestamp": self._utc_now_iso(),
            "devices": [
                self._device_heartbeat_payload(device)
                for device in self.devices
            ],
        }

        response = self.http.post(
            "/devices/heartbeat",
            json=payload,
        )

        response.raise_for_status()

        print(f"Heartbeat sent at {payload['timestamp']}")

    def run_forever(self) -> None:
        """Connect devices, register them, then heartbeat forever."""
        self.connect_devices()
        self.register_devices()

        print()
        print("Lab PC Device Manager running.")
        print(f"Central Server: {self.server_url}")
        print(f"Host: {self.host}")
        print(f"RPC URL placeholder: {self.rpc_url}")
        print()

        while True:
            self.send_heartbeat()
            time.sleep(self.heartbeat_interval_seconds)

    def close(self) -> None:
        """Clean shutdown."""
        self.disconnect_devices()
        self.http.close()

    def _device_registration_payload(self, device) -> dict[str, Any]:
        """Build one device registration payload."""
        return {
            "device_id": device.device_id,
            "device_type": device.device_type,
            "status": device.status(),
            "metadata": device.metadata(),
        }

    def _device_heartbeat_payload(self, device) -> dict[str, Any]:
        """Build one heartbeat payload item."""
        return {
            "device_id": device.device_id,
            "status": device.status(),
            "metadata": device.metadata(),
        }

    def _utc_now_iso(self) -> str:
        """Return UTC timestamp ending in Z."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    load_dotenv()

    server_url = os.getenv(
        "LABNET_SERVER_URL",
        "http://127.0.0.1:8008",
    )

    api_key = os.environ["API_KEY"]

    host = os.getenv(
        "LABNET_LAB_PC_HOST",
        "fake-lab-pc-1",
    )

    rpc_url = os.getenv(
        "LABNET_RPC_URL",
        "ws://127.0.0.1:9001/ws/rpc",
    )

    heartbeat_interval_seconds = float(
        os.getenv("LABNET_HEARTBEAT_INTERVAL_SECONDS", "5")
    )

    devices = create_devices()

    manager = LabPCDeviceManager(
        server_url=server_url,
        api_key=api_key,
        host=host,
        rpc_url=rpc_url,
        devices=devices,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )

    try:
        manager.run_forever()
    finally:
        manager.close()


if __name__ == "__main__":
    main()