from __future__ import annotations

from typing import Any

from ...client.core.lab_client import LabClient
from .physical import PhysicalDummyDevice
from .remote import RemoteDummyDevice


class DummyDevice(PhysicalDummyDevice, RemoteDummyDevice):
    """Dummy device that works as either a local or remote device.

    With no arguments, the physical dummy-device implementation is used. To
    use the remote implementation, provide both a ``LabClient`` and a lease
    duration.
    """

    def __init__(
        self,
        client: LabClient | None = None,
        lease_duration: int | None = None,
    ) -> None:
        if client is None and lease_duration is None:
            self._is_remote = False
            PhysicalDummyDevice.__init__(self)
            return

        if client is None or lease_duration is None:
            raise ValueError(
                "client and lease_duration must either both be provided "
                "or both be None."
            )

        self._is_remote = True
        RemoteDummyDevice.__init__(
            self,
            client=client,
            lease_duration=lease_duration,
        )

    @property
    def is_remote(self) -> bool:
        """Return whether this instance is using the remote implementation."""
        return self._is_remote

    @property
    def device_id(self) -> str:
        if self._is_remote:
            return self.device.device_id
        return self._device_id

    def metadata(self) -> dict[str, Any]:
        if self._is_remote:
            return RemoteDummyDevice.metadata(self)
        return PhysicalDummyDevice.metadata(self)

    def connect(self) -> None:
        if self._is_remote:
            self.rpc.connect()
            return
        PhysicalDummyDevice.connect(self)

    def disconnect(self) -> None:
        if self._is_remote:
            RemoteDummyDevice.close(self)
            return
        PhysicalDummyDevice.disconnect(self)

    def status(self) -> str:
        if self._is_remote:
            if self._closed:
                return "disconnected"
            return self.device.status
        return PhysicalDummyDevice.status(self)

    def allowed_methods(self) -> list[str]:
        return PhysicalDummyDevice.allowed_methods(self)

    def read_value(self) -> float:
        if self._is_remote:
            return RemoteDummyDevice.read_value(self)
        return PhysicalDummyDevice.read_value(self)

    def write_value(self, value: float) -> None:
        if self._is_remote:
            RemoteDummyDevice.write_value(self, value)
            return
        PhysicalDummyDevice.write_value(self, value)

    def close(self) -> None:
        """Release remote resources, or disconnect the physical device."""
        if self._is_remote:
            RemoteDummyDevice.close(self)
            return
        PhysicalDummyDevice.disconnect(self)

    def __enter__(self) -> "DummyDevice":
        if not self._is_remote:
            self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


__all__ = ["DummyDevice"]
