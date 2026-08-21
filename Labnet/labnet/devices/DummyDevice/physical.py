from __future__ import annotations

from .interface import DummyDeviceInterface
from typing import Any

class PhysicalDummyDevice(DummyDeviceInterface):
    """
    Fake physical device running on the lab PC.

    This simulates a real hardware wrapper.
    """

    def __init__(self, device_id: str = "fake_device_1"):
        self._device_id = device_id
        self._connected = False
        self._value = 0.0

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> str:
        if not self._connected:
            return "disconnected"

        return "idle"

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "FakeDeviceV1",
            "capabilities": self.allowed_methods(),
        }

    def allowed_methods(self) -> list[str]:
        return [
            "read_value",
            "write_value",
        ]

    def read_value(self) -> float:
        return self._value

    def write_value(self, value: float) -> None:
        self._value = value
