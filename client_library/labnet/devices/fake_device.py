from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from labnet.client import LabClient
from labnet.exceptions import (
    NoAvailableDeviceError,
    ResourceNotFoundError,
)
from labnet.rpc import PersistentRpcConnection
from labnet_shared.devices.fake_device import FakeDeviceInterface


class RemoteFakeDevice(FakeDeviceInterface):
    """
    Remote fake-device proxy.

    This object owns:
    - one Central Server lease
    - one persistent WebSocket connection to the Lab PC
    """

    device_type = "fake_device"

    def __init__(
        self,
        client: LabClient,
        lease_duration: int = 600,
        device_id: str | None = None,
    ):
        self.client = client
        self._closed = False

        if device_id is None:
            available = client.list_available_devices(
                device_type=self.device_type
            )

            if not available:
                raise NoAvailableDeviceError(
                    "No idle fake_device is available."
                )

            device_id = available[0].device_id

        grant = client.request_lease(
            device_id=device_id,
            duration_seconds=lease_duration,
        )

        self.lease = grant.lease
        self.device = grant.device

        self.rpc = PersistentRpcConnection(
            rpc_url=self.device.rpc_url,
            timeout_seconds=5.0,
        )

        # Open immediately so constructor fails early if the Lab PC RPC
        # server is unreachable.
        try:
            self.rpc.connect()
        except Exception:
            # Avoid leaving a lease active if the RPC connection fails.
            try:
                self.client.release_lease(self.lease.lease_id)
            finally:
                self._closed = True

            raise

    @property
    def device_id(self) -> str:
        return self.device.device_id

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id

    @property
    def lease_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.lease.expires_at

    def metadata(self) -> dict[str, Any]:
        return {"metadata": "None"}

    def read_value(self) -> float:
        result = self.rpc.call(
            lease_id=self.lease.lease_id,
            device_id=self.device.device_id,
            method="read_value",
        )

        return float(result)

    def write_value(self, value: float) -> None:
        self.rpc.call(
            lease_id=self.lease.lease_id,
            device_id=self.device.device_id,
            method="write_value",
            args=[value],
        )

    def close(self) -> None:
        """
        Close RPC connection and release the lease.

        Close RPC first so no more commands can be sent after release.
        """

        if self._closed:
            return

        self.rpc.close()

        try:
            self.client.release_lease(self.lease.lease_id)
        except ResourceNotFoundError:
            # Lease may already have expired and been cleaned up.
            pass

        self._closed = True

    def __enter__(self) -> "RemoteFakeDevice":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()