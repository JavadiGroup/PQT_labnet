from __future__ import annotations

import math
import random
from typing import Any

from ..core import BaseDeviceInterface, DeviceAvailability
from ...grpc.generated.device_contracts import DUMMY_EOM_CONTRACT


def _in_range(value: float, low: float, high: float, what: str, unit: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{what} must be between {low:g} and {high:g} {unit}.")
    return number


def bessel_j(order: int, x: float) -> float:
    """Bessel function of the first kind, J_order(x), by its power series.

    30 terms are far more than enough for the modulation depths used here
    (x <= 2.2).
    """

    total = 0.0
    for k in range(30):
        total += (-1) ** k / (math.factorial(k) * math.factorial(k + order)) * (x / 2) ** (2 * k + order)
    return total


class PhysicalDummyEOM(BaseDeviceInterface):
    """A simulated resonant electro-optic phase modulator.

    The modulation depth follows the RF amplitude (pi rad at 5 V) and the
    resonance (20 MHz, Q = 20):

        depth = pi * V / 5 V / sqrt(1 + (2 Q (f - 20 MHz) / 20 MHz)^2)

    With at most 3.5 V the depth stays below 2.2 rad, short of the first zero
    of J0 (2.405), so the carrier never vanishes. The carrier keeps J0(depth)^2
    of the power and each first sideband J1(depth)^2.
    """

    device_type = DUMMY_EOM_CONTRACT.device_type
    RESONANCE_MHZ = 20.0
    QUALITY_FACTOR = 20.0
    V_PI = 5.0
    FREQUENCY_RANGE_MHZ = (1.0, 100.0)
    RF_AMPLITUDE_RANGE_V = (0.0, 3.5)

    def __init__(
        self,
        device_id: str = "dummy_eom_1",
        *,
        modulation_frequency_mhz: float = 20.0,
        rf_amplitude_v: float = 1.0,
        noise: float = 0.01,
        seed: int | None = None,
    ) -> None:
        self._device_id = device_id
        self._frequency_mhz = _in_range(
            modulation_frequency_mhz, *self.FREQUENCY_RANGE_MHZ, "Modulation frequency", "MHz"
        )
        self._rf_amplitude_v = _in_range(rf_amplitude_v, *self.RF_AMPLITUDE_RANGE_V, "RF amplitude", "V")
        self._rf_enabled = True
        self._noise = float(noise)  # relative noise of each sideband-ratio reading
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
            "model": "DummyEOM",
            "capabilities": self.allowed_methods(),
            "hints": {
                "limits": {
                    "set_modulation_frequency.value": list(self.FREQUENCY_RANGE_MHZ),
                    "set_rf_amplitude.value": list(self.RF_AMPLITUDE_RANGE_V),
                },
            },
        }

    # ---- settings ---------------------------------------------------------------

    def set_modulation_frequency(self, value: float) -> float:
        self._frequency_mhz = _in_range(value, *self.FREQUENCY_RANGE_MHZ, "Modulation frequency", "MHz")
        return self._frequency_mhz

    def set_rf_amplitude(self, value: float) -> float:
        self._rf_amplitude_v = _in_range(value, *self.RF_AMPLITUDE_RANGE_V, "RF amplitude", "V")
        return self._rf_amplitude_v

    def set_rf_enabled(self, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be True or False.")
        self._rf_enabled = enabled
        return self._rf_enabled

    # ---- readings ---------------------------------------------------------------

    def read_modulation_frequency(self) -> float:
        return self._frequency_mhz

    def read_rf_amplitude(self) -> float:
        return self._rf_amplitude_v

    def read_modulation_depth(self) -> float:
        if not self._rf_enabled:
            return 0.0
        detuning = 2 * self.QUALITY_FACTOR * (self._frequency_mhz - self.RESONANCE_MHZ) / self.RESONANCE_MHZ
        return math.pi * self._rf_amplitude_v / self.V_PI / math.sqrt(1 + detuning**2)

    def read_carrier_fraction(self) -> float:
        return bessel_j(0, self.read_modulation_depth()) ** 2

    def read_sideband_fraction(self) -> float:
        return bessel_j(1, self.read_modulation_depth()) ** 2

    def read_sideband_ratio(self, channel: str | None = None) -> float:
        if channel:
            raise ValueError("The EOM has one channel; leave channel empty.")
        if not self._connected:
            raise RuntimeError("The EOM is not connected.")
        depth = self.read_modulation_depth()
        ratio = (bessel_j(1, depth) / bessel_j(0, depth)) ** 2
        if self._noise:
            ratio *= 1 + self._random.gauss(0.0, self._noise)
        return max(0.0, ratio)
