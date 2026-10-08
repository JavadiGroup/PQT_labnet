from __future__ import annotations

from typing import Any

from ..core import BaseDeviceInterface, DeviceAvailability
from ...grpc.generated.device_contracts import DUMMY_DEVICE_CONTRACT


class PhysicalDummyDevice(BaseDeviceInterface):
    """
    Dummy physical device running on the lab PC.

    This simulates a real hardware wrapper.
    """

    device_type = DUMMY_DEVICE_CONTRACT.device_type

    def __init__(self, device_id: str = "dummy_device_1"):
        self._device_id = device_id
        self._connected = False
        self._values: dict[str, float] = {"": 0.0}

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

    def availability(self) -> DeviceAvailability:
        """Return an explicit control-readiness signal for health checks."""

        return (
            DeviceAvailability.AVAILABLE
            if self._connected
            else DeviceAvailability.UNAVAILABLE
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "DummyDeviceV1",
            "channel_addressable": True,
            "capabilities": self.allowed_methods(),
        }

    def read_value(self, channel: str | None = None) -> float:
        return self._values.get(channel or "", 0.0)

    def write_value(self, value: float, channel: str | None = None) -> None:
        self._values[channel or ""] = float(value)
