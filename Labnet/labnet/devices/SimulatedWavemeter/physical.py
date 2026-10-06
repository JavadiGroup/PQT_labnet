from __future__ import annotations

import random
import time
from typing import Any

from ..core import BaseDeviceInterface, DeviceAvailability
from ...grpc.generated.device_contracts import SIMULATED_WAVEMETER_CONTRACT


class PhysicalSimulatedWavemeter(BaseDeviceInterface):
    """A wavemeter stand-in: a fixed line with slow drift and Gaussian noise.

    Use it to develop and demonstrate experiments without hardware. A real
    wavemeter driver would implement the same ``read_wavelength`` and
    ``set_exposure`` methods by calling its vendor SDK.

    Like a real instrument, each reading takes its exposure time, and longer
    exposures average away noise (noise scales with 1/sqrt(exposure)).
    """

    device_type = SIMULATED_WAVEMETER_CONTRACT.device_type
    MIN_EXPOSURE_MS = 1.0
    MAX_EXPOSURE_MS = 1000.0
    _REFERENCE_EXPOSURE_MS = 10.0

    def __init__(
        self,
        device_id: str = "simulated_wavemeter_1",
        *,
        center_nm: float = 780.241,
        noise_nm: float = 0.0005,
        drift_nm_per_hour: float = 0.002,
        exposure_ms: float = 10.0,
    ) -> None:
        self._device_id = device_id
        self._center_nm = center_nm
        # Standard deviation at the 10 ms reference exposure.
        self._noise_nm = noise_nm
        self._drift_nm_per_second = drift_nm_per_hour / 3600.0
        self._connected_at: float | None = None
        self._exposure_ms = self._checked_exposure(exposure_ms)

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
            "exposure_ms": self._exposure_ms,
            "capabilities": self.allowed_methods(),
        }

    def set_exposure(self, value: float, channel: str | None = None) -> float:
        """Set the exposure time in milliseconds and return the value applied."""

        self._exposure_ms = self._checked_exposure(value)
        return self._exposure_ms

    def read_wavelength(self, channel: str | None = None) -> float:
        if self._connected_at is None:
            raise RuntimeError("The simulated wavemeter is not connected.")
        exposure_ms = self._exposure_ms
        time.sleep(exposure_ms / 1000.0)
        elapsed = time.monotonic() - self._connected_at
        drift = self._drift_nm_per_second * elapsed
        noise = self._noise_nm * (self._REFERENCE_EXPOSURE_MS / exposure_ms) ** 0.5
        return self._center_nm + drift + random.gauss(0.0, noise)

    @classmethod
    def _checked_exposure(cls, value: float) -> float:
        exposure = float(value)
        if not cls.MIN_EXPOSURE_MS <= exposure <= cls.MAX_EXPOSURE_MS:
            raise ValueError(
                f"Exposure must be between {cls.MIN_EXPOSURE_MS:g} and "
                f"{cls.MAX_EXPOSURE_MS:g} ms."
            )
        return exposure
