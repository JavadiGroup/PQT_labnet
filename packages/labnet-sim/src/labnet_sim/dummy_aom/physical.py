from __future__ import annotations

import math
import random
from typing import Any

from labnet.devices.core import BaseDeviceInterface, DeviceAvailability
from .._generated.contracts import DUMMY_AOM_CONTRACT


def _in_range(value: float, low: float, high: float, what: str, unit: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{what} must be between {low:g} and {high:g} {unit}.")
    return number


class PhysicalDummyAOM(BaseDeviceInterface):
    """A simulated acousto-optic modulator and its RF driver.

    The first-order diffraction efficiency follows the usual model:

        efficiency = 0.85 * sin^2(pi/2 * sqrt(P / 1 W)) * exp(-((f - 80 MHz) / 12 MHz)^2)

    so it peaks at 1 W of RF power and the 80 MHz center frequency, and it is
    zero with the RF off. The deflection angle is lambda * f / v for 780 nm
    light and a 4200 m/s acoustic velocity (14.9 mrad at 80 MHz).
    """

    device_type = DUMMY_AOM_CONTRACT.device_type
    CENTER_MHZ = 80.0
    BANDWIDTH_MHZ = 12.0
    PEAK_EFFICIENCY = 0.85
    SATURATION_W = 1.0
    WAVELENGTH_M = 780e-9
    ACOUSTIC_VELOCITY_M_PER_S = 4200.0
    FREQUENCY_RANGE_MHZ = (60.0, 100.0)
    RF_POWER_RANGE_W = (0.0, 2.0)

    def __init__(
        self,
        device_id: str = "dummy_aom_1",
        *,
        frequency_mhz: float = 80.0,
        rf_power_w: float = 0.5,
        noise: float = 0.002,
        seed: int | None = None,
    ) -> None:
        self._device_id = device_id
        self._frequency_mhz = _in_range(frequency_mhz, *self.FREQUENCY_RANGE_MHZ, "Frequency", "MHz")
        self._rf_power_w = _in_range(rf_power_w, *self.RF_POWER_RANGE_W, "RF power", "W")
        self._rf_enabled = True
        self._noise = float(noise)  # standard deviation of each efficiency reading
        self._random = random.Random(seed)
        self._connected = False

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> str:
        return "ready" if self._connected else "disconnected"

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "DummyAOM",
            "capabilities": self.allowed_methods(),
            # Ranges for the Control Center's forms and checks.
            "hints": {
                "limits": {
                    "set_frequency.value": list(self.FREQUENCY_RANGE_MHZ),
                    "set_rf_power.value": list(self.RF_POWER_RANGE_W),
                },
            },
        }

    # ---- settings ---------------------------------------------------------------

    def set_frequency(self, value: float) -> float:
        self._frequency_mhz = _in_range(value, *self.FREQUENCY_RANGE_MHZ, "Frequency", "MHz")
        return self._frequency_mhz

    def set_rf_power(self, value: float) -> float:
        self._rf_power_w = _in_range(value, *self.RF_POWER_RANGE_W, "RF power", "W")
        return self._rf_power_w

    def set_rf_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be True or False.")
        self._rf_enabled = enabled
        return self._rf_enabled

    # ---- readings ---------------------------------------------------------------

    def read_frequency(self) -> float:
        return self._frequency_mhz

    def read_rf_power(self) -> float:
        return self._rf_power_w

    def read_deflection_angle(self) -> float:
        """Deflection of the first order, in mrad."""

        return self.WAVELENGTH_M * self._frequency_mhz * 1e6 / self.ACOUSTIC_VELOCITY_M_PER_S * 1e3

    def read_diffraction_efficiency(self, channel: str | None = None) -> float:
        if channel:
            raise ValueError("The AOM has one channel; leave channel empty.")
        if not self._connected:
            raise RuntimeError("The AOM is not connected.")
        efficiency = 0.0
        if self._rf_enabled:
            drive = math.sin(math.pi / 2 * math.sqrt(self._rf_power_w / self.SATURATION_W)) ** 2
            detuning = (self._frequency_mhz - self.CENTER_MHZ) / self.BANDWIDTH_MHZ
            efficiency = self.PEAK_EFFICIENCY * drive * math.exp(-(detuning**2))
        if self._noise:
            efficiency += self._random.gauss(0.0, self._noise)
        return min(1.0, max(0.0, efficiency))
