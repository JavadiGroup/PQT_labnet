from __future__ import annotations

from abc import abstractmethod

from ..core.base import BaseDeviceInterface


class DummyDeviceInterface(BaseDeviceInterface):
    """
    Interface shared by physical and remote dummy devices.
    """

    device_type = "dummy_device"

    @abstractmethod
    def read_value(self, channel: str | None = None) -> float:
        """Read the current value, optionally selecting a named channel."""
        raise NotImplementedError

    @abstractmethod
    def write_value(self, value: float, channel: str | None = None) -> None:
        """Write a value, optionally selecting a named channel."""
        raise NotImplementedError
