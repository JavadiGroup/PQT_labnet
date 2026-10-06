from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any

from ..core import BaseDeviceInterface, DeviceAvailability
from ...grpc.generated.device_contracts import DUMMY_AWG_CONTRACT


def _in_range(value: float, low: float, high: float, what: str, unit: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{what} must be between {low:g} and {high:g} {unit}.")
    return number


@dataclass
class _Channel:
    waveform: str = "sine"
    frequency_hz: float = 1000.0
    amplitude_vpp: float = 1.0
    offset_v: float = 0.0
    output: bool = False


class PhysicalDummyAWG(BaseDeviceInterface):
    """A simulated two-channel arbitrary waveform generator.

    Each channel (``CH1``, ``CH2``; no channel means ``CH1``) has a waveform,
    frequency, peak-to-peak amplitude, offset and an output switch that starts
    off. ``read_output_voltage`` samples the waveform at the current time;
    ``read_output_rms`` gives sqrt(offset^2 + (amplitude/2)^2 * k), with
    k = 1/2 (sine), 1 (square), 1/3 (triangle, ramp) or 0 (dc).
    """

    device_type = DUMMY_AWG_CONTRACT.device_type
    CHANNELS = ("CH1", "CH2")
    WAVEFORMS = ("sine", "square", "triangle", "ramp", "dc")
    RMS_FACTOR = {"sine": 0.5, "square": 1.0, "triangle": 1 / 3, "ramp": 1 / 3, "dc": 0.0}
    FREQUENCY_RANGE_HZ = (1e-3, 1.2e8)
    AMPLITUDE_RANGE_VPP = (0.0, 10.0)
    OFFSET_RANGE_V = (-5.0, 5.0)

    def __init__(self, device_id: str = "dummy_awg_1", *, noise: float = 0.0005, seed: int | None = None) -> None:
        self._device_id = device_id
        self._channels = {name: _Channel() for name in self.CHANNELS}
        self._noise = float(noise)  # standard deviation of each voltage sample, V
        self._random = random.Random(seed)
        self._started = time.monotonic()
        self._connected = False

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        for channel in self._channels.values():
            channel.output = False

    def status(self) -> str:
        return "ready" if self._connected else "disconnected"

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "DummyAWG",
            "capabilities": self.allowed_methods(),
            # Channels, ranges and choices for the Control Center's forms and checks.
            "hints": {
                "channels": list(self.CHANNELS),
                "limits": {
                    "set_frequency.value": list(self.FREQUENCY_RANGE_HZ),
                    "set_amplitude.value": list(self.AMPLITUDE_RANGE_VPP),
                    "set_offset.value": list(self.OFFSET_RANGE_V),
                },
                "choices": {"set_waveform.waveform": list(self.WAVEFORMS)},
            },
        }

    def _channel(self, channel: str | None) -> _Channel:
        name = (channel or "CH1").strip().upper()
        if name not in self._channels:
            raise ValueError(f"Unknown channel {channel!r}; use CH1 or CH2.")
        return self._channels[name]

    # ---- settings ---------------------------------------------------------------

    def set_frequency(self, value: float, channel: str | None = None) -> float:
        target = self._channel(channel)
        target.frequency_hz = _in_range(value, *self.FREQUENCY_RANGE_HZ, "Frequency", "Hz")
        return target.frequency_hz

    def set_amplitude(self, value: float, channel: str | None = None) -> float:
        target = self._channel(channel)
        target.amplitude_vpp = _in_range(value, *self.AMPLITUDE_RANGE_VPP, "Amplitude", "Vpp")
        return target.amplitude_vpp

    def set_offset(self, value: float, channel: str | None = None) -> float:
        target = self._channel(channel)
        target.offset_v = _in_range(value, *self.OFFSET_RANGE_V, "Offset", "V")
        return target.offset_v

    def set_waveform(self, waveform: str, channel: str | None = None) -> str:
        target = self._channel(channel)
        shape = str(waveform).strip().lower()
        if shape not in self.WAVEFORMS:
            raise ValueError(f"Unknown waveform {waveform!r}; use one of {', '.join(self.WAVEFORMS)}.")
        target.waveform = shape
        return shape

    def set_output_enabled(self, enabled: bool, channel: str | None = None) -> bool:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be True or False.")
        target = self._channel(channel)
        target.output = enabled
        return target.output

    # ---- readings ---------------------------------------------------------------

    def read_frequency(self, channel: str | None = None) -> float:
        return self._channel(channel).frequency_hz

    def read_amplitude(self, channel: str | None = None) -> float:
        return self._channel(channel).amplitude_vpp

    def read_offset(self, channel: str | None = None) -> float:
        return self._channel(channel).offset_v

    def read_output_rms(self, channel: str | None = None) -> float:
        target = self._channel(channel)
        if not target.output:
            return 0.0
        half = target.amplitude_vpp / 2
        return math.sqrt(target.offset_v**2 + half**2 * self.RMS_FACTOR[target.waveform])

    def read_output_voltage(self, channel: str | None = None) -> float:
        target = self._channel(channel)
        if not self._connected:
            raise RuntimeError("The AWG is not connected.")
        if not target.output:
            return 0.0
        phase = ((time.monotonic() - self._started) * target.frequency_hz) % 1.0
        voltage = target.offset_v + target.amplitude_vpp / 2 * self._shape(target.waveform, phase)
        if self._noise:
            voltage += self._random.gauss(0.0, self._noise)
        return voltage

    @staticmethod
    def _shape(waveform: str, phase: float) -> float:
        """The waveform at ``phase`` (0 to 1 of a period), between -1 and 1."""

        if waveform == "sine":
            return math.sin(2 * math.pi * phase)
        if waveform == "square":
            return 1.0 if phase < 0.5 else -1.0
        if waveform == "triangle":
            return 4 * phase - 1 if phase < 0.5 else 3 - 4 * phase
        if waveform == "ramp":
            return 2 * phase - 1
        return 0.0  # dc: the offset alone
