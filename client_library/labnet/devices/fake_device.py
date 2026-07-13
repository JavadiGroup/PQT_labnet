from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from labnet.client import LabClient
from labnet.exceptions import (
    NoAvailableDeviceError,
    ResourceNotFoundError,
)

from labnet_shared import FakeDeviceInterface


class RemoteFakeDevice(FakeDeviceInterface):
    """
    Remote fake-device proxy.

    For this test it performs discovery and lease management only.
    RPC method forwarding will be added with the Device Manager.
    """

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

    @property
    def device_id(self) -> str:
        return self.device.device_id

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id

    @property
    def lease_expires_at(self) -> datetime:
        return self.lease.expires_at

    @property
    def lease_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.lease.expires_at

    def metadata(self) -> dict[str, Any]:
        """Return device metadata."""
        raise NotImplementedError
    
    def read_value(self) -> float:
        raise NotImplementedError(
            "Remote method calls require the Device Manager RPC layer."
        )

    def write_value(self, value: float) -> None:
        raise NotImplementedError(
            "Remote method calls require the Device Manager RPC layer."
        )

    def close(self) -> None:
        """Release the lease unless it was already expired or released."""

        if self._closed:
            return

        try:
            self.client.release_lease(self.lease_id)
        except ResourceNotFoundError:
            # The cleanup task may already have expired the lease.
            pass

        self._closed = True

    def __enter__(self) -> "RemoteFakeDevice":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()