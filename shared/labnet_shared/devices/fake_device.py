from __future__ import annotations

from abc import abstractmethod

from labnet_shared.devices.base import BaseDeviceInterface


class FakeDeviceInterface(BaseDeviceInterface):
    """
    Interface shared by physical and remote fake devices.
    """

    device_type = "fake_device"

    @abstractmethod
    def read_value(self) -> float:
        """Read the current fake value."""
        raise NotImplementedError

    @abstractmethod
    def write_value(self, value: float) -> None:
        """Write a fake value."""
        raise NotImplementedError