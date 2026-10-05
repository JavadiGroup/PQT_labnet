from __future__ import annotations

import random
import time
from typing import Any

from ..core import BaseDeviceInterface, DeviceAvailability
from ...grpc.generated.device_contracts import SIMULATED_WAVEMETER_CONTRACT


class PhysicalSimulatedWavemeter(BaseDeviceInterface):
    """A wavemeter stand-in: a fixed line with slow drift and Gaussian noise.

    Use it to develop and demonstrate experiments without hardware. A real
    wavemeter driver would implement the same ``read_wavelength`` method by
    calling its vendor SDK.
    """

    device_type = SIMULATED_WAVEMETER_CONTRACT.device_type

    def __init__(
        self,
        device_id: str = "simulated_wavemeter_1",
        *,
        center_nm: float = 780.241,
        noise_nm: float = 0.0005,
        drift_nm_per_hour: float = 0.002,
    ) -> None:
        self._device_id = device_id
        self._center_nm = center_nm
        self._noise_nm = noise_nm
        self._drift_nm_per_second = drift_nm_per_hour / 3600.0
        self._connected_at: float | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self._connected_at = time.monotonic()

    def disconnect(self) -> None:
        self._connected_at = None

    def status(self) -> str:
        return "measuring" if self._connected_at is not None else "disconnected"

    def availability(self) -> DeviceAvailability:
        return (
            DeviceAvailability.AVAILABLE
            if self._connected_at is not None
            else DeviceAvailability.UNAVAILABLE
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "SimulatedWavemeter",
            "center_nm": self._center_nm,
            "capabilities": self.allowed_methods(),
        }

    def read_wavelength(self, channel: str | None = None) -> float:
        if self._connected_at is None:
            raise RuntimeError("The simulated wavemeter is not connected.")
        elapsed = time.monotonic() - self._connected_at
        drift = self._drift_nm_per_second * elapsed
        return self._center_nm + drift + random.gauss(0.0, self._noise_nm)
