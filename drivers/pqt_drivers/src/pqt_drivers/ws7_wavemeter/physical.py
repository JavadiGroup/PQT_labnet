"""The WS7 on LabNet: a thin wrapper around PQT_devices' ``WS7Wavemeter``.

All instrument logic, range checks and error handling live in PQT_devices.
This class only maps the methods the proto declares onto the driver and
converts types for the network (channels as text, states as text).
"""

from __future__ import annotations

import math
from typing import Any

from labnet.devices.core import BaseDeviceInterface, DeviceAvailability
from PQT_devices.wavemeter.ws7 import WlmReadError, WS7Wavemeter

from .._generated.contracts import WS7_WAVEMETER_CONTRACT


class PhysicalWs7Wavemeter(BaseDeviceInterface):
    """One WS7, read on one channel. The Lab PC calls it from a single worker thread.

    Args:
        device_id: Network-wide ID, e.g. ``"ws7"``.
        channel: The WS7 data channel to read (1 without a switch).
        serial_number: Needed only if several HighFinesse applications run.
        startup_timeout_s: How long ``connect()`` waits for HighFinesse to start.
        wavemeter: An already-built driver (tests); the arguments above are then unused.
    """

    device_type = WS7_WAVEMETER_CONTRACT.device_type

    def __init__(
        self,
        device_id: str = "ws7",
        *,
        channel: int = 1,
        serial_number: int | None = None,
        startup_timeout_s: float = 30.0,
        wavemeter: WS7Wavemeter | None = None,
    ) -> None:
        self._device_id = device_id
        self._meter = wavemeter if wavemeter is not None else WS7Wavemeter(
            channel, serial_number=serial_number, startup_timeout_s=startup_timeout_s
        )
        self._exposure_range_ms: tuple[int, int] | None = None
        self._serial_number: int | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        # Starts HighFinesse (hidden) if it isn't running; never stops it.
        self._meter.connect()
        self._exposure_range_ms = self._meter.get_exposure_range_ms()
        self._serial_number = self._meter.get_device_info().serial_number

    def disconnect(self) -> None:
        # Detaches only: HighFinesse keeps running and measuring for its other users.
        self._meter.close()

    def status(self) -> str:
        if not self._meter.is_connected:
            return "disconnected"
        return self.get_measurement_state()

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._meter.is_connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "manufacturer": "HighFinesse",
            "model": "WS7",
            "serial_number": self._serial_number,
            "channel": self._meter.channel,
            "capabilities": self.allowed_methods(),
        }
        if self._exposure_range_ms is not None:
            # The WS7's own range, read at connect. The Lab PC enforces it on every remote call.
            metadata["hints"] = {"limits": {"set_exposure.value": list(self._exposure_range_ms)}}
        return metadata

    # ---- readings ---------------------------------------------------------------

    def read_wavelength(self) -> float:
        return self._meter.get_wavelength_nm()

    def read_wavelength_sample(self, channel: str | None = None) -> dict[str, Any]:
        """One stream sample: a bad-quality sample instead of an error when there is no signal."""
        self._check_channel(channel)
        try:
            return {"value": self._meter.get_wavelength_nm(), "quality": "GOOD"}
        except WlmReadError as exc:
            # LabNet marks a non-finite value BAD with status NON_FINITE; the stream keeps going.
            return {"value": math.nan, "quality": "BAD", "status": f"WLM_{exc.error_code}"}

    def read_frequency(self) -> float:
        return self._meter.get_frequency_thz()

    def read_temperature(self) -> float:
        return self._meter.get_temperature_c()

    def read_exposure(self) -> float:
        return float(self._meter.get_exposure_ms())

    # ---- settings ---------------------------------------------------------------

    def set_exposure(self, value: float) -> float:
        exposure = float(value)
        if not math.isfinite(exposure) or not exposure.is_integer():
            raise ValueError(f"Exposure must be a whole number of milliseconds; got {value!r}.")
        self._meter.set_exposure_ms(int(exposure))
        return float(self._meter.get_exposure_ms())

    def get_auto_exposure(self) -> bool:
        return self._meter.get_auto_exposure()

    def set_auto_exposure(self, enabled: bool) -> bool:
        self._meter.set_auto_exposure(enabled)
        return self._meter.get_auto_exposure()

    def get_measurement_state(self) -> str:
        return self._meter.get_measurement_state().name.lower()

    def start_measurement(self) -> str:
        self._meter.start_measurement()
        return self.get_measurement_state()

    def stop_measurement(self) -> str:
        self._meter.stop_measurement()
        return self.get_measurement_state()

    def _check_channel(self, channel: str | None) -> None:
        if channel is not None and channel != str(self._meter.channel):
            raise ValueError(f"This WS7 is read on channel {self._meter.channel} only; got {channel!r}.")
