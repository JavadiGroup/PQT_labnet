from __future__ import annotations

import math
import random
from typing import Any

from labnet.devices.core import BaseDeviceInterface, DeviceAvailability
from .._generated.contracts import DUMMY_LASER_CONTRACT


def _in_range(value: float, low: float, high: float, what: str, unit: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{what} must be between {low:g} and {high:g} {unit}.")
    return number


class PhysicalDummyLaser(BaseDeviceInterface):
    """A simulated tunable diode laser.

    While emission is on, the output power rises linearly above threshold:

        power = 0.8 mW/mA * max(0, I - 40 mA)

    and the current pulls the wavelength by 0.003 nm/mA above threshold. With
    emission off (the state it starts in, as real lasers do) the power is 0.
    """

    device_type = DUMMY_LASER_CONTRACT.device_type
    THRESHOLD_MA = 40.0
    SLOPE_MW_PER_MA = 0.8
    PULL_NM_PER_MA = 0.003
    WAVELENGTH_RANGE_NM = (765.0, 795.0)
    CURRENT_RANGE_MA = (0.0, 200.0)

    def __init__(
        self,
        device_id: str = "dummy_laser_1",
        *,
        wavelength_nm: float = 780.241,
        noise_mw: float = 0.05,
        noise_nm: float = 0.0002,
        seed: int | None = None,
    ) -> None:
        self._device_id = device_id
        self._wavelength_nm = _in_range(wavelength_nm, *self.WAVELENGTH_RANGE_NM, "Wavelength", "nm")
        self._current_ma = 0.0
        self._emission = False
        self._noise_mw = float(noise_mw)
        self._noise_nm = float(noise_nm)
        self._random = random.Random(seed)
        self._connected = False

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._emission = False
        self._connected = False

    def status(self) -> str:
        if not self._connected:
            return "disconnected"
        return "emitting" if self._emission else "ready"

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "DummyLaser",
            "capabilities": self.allowed_methods(),
            "hints": {
                "limits": {
                    "set_wavelength.value": list(self.WAVELENGTH_RANGE_NM),
                    "set_current.value": list(self.CURRENT_RANGE_MA),
                },
            },
        }

    # ---- settings ---------------------------------------------------------------

    def set_wavelength(self, value: float) -> float:
        self._wavelength_nm = _in_range(value, *self.WAVELENGTH_RANGE_NM, "Wavelength", "nm")
        return self._wavelength_nm

    def set_current(self, value: float) -> float:
        self._current_ma = _in_range(value, *self.CURRENT_RANGE_MA, "Current", "mA")
        return self._current_ma

    def set_emission_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be True or False.")
        self._emission = enabled
        return self._emission

    # ---- readings ---------------------------------------------------------------

    def _above_threshold_ma(self) -> float:
        return max(0.0, self._current_ma - self.THRESHOLD_MA) if self._emission else 0.0

    def read_current(self) -> float:
        return self._current_ma

    def read_power(self, channel: str | None = None) -> float:
        if channel:
            raise ValueError("The laser has one channel; leave channel empty.")
        if not self._connected:
            raise RuntimeError("The laser is not connected.")
        power = self.SLOPE_MW_PER_MA * self._above_threshold_ma()
        if power and self._noise_mw:
            power += self._random.gauss(0.0, self._noise_mw)
        return max(0.0, power)

    def read_wavelength(self) -> float:
        wavelength = self._wavelength_nm + self.PULL_NM_PER_MA * self._above_threshold_ma()
        if self._noise_nm:
            wavelength += self._random.gauss(0.0, self._noise_nm)
        return wavelength
