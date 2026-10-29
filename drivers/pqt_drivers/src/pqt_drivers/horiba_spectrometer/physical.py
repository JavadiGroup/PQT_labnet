"""The Horiba spectrometer on LabNet: a thin wrapper around PQT_devices' ``HoribaSpectrometer``.

All LabSpec protocol handling and range checks live in PQT_devices. This
class maps the proto's methods onto the driver, returns spectra as lists of
floats, and publishes LabSpec's grating names as choices.
"""

from __future__ import annotations

import math
from typing import Any

from labnet.devices.core import BaseDeviceInterface, DeviceAvailability
from PQT_devices.spectrometer.horiba import HoribaSpectrometer

from .._generated.contracts import HORIBA_SPECTROMETER_CONTRACT


class PhysicalHoribaSpectrometer(BaseDeviceInterface):
    """One Horiba spectrometer. The Lab PC calls it from a single worker thread.

    Args:
        device_id: Network-wide ID, e.g. ``"horiba-spec-vis"``.
        host, port: LabSpec's Raman-AFM server, normally on this PC.
        exposure_s, accumulations: Applied on every connect (LabSpec can't report them).
        center_wavelength_range_nm: Optional ``(min, max)`` the driver accepts.
        spectrometer: An already-built driver (tests); the arguments above are then unused.
    """

    device_type = HORIBA_SPECTROMETER_CONTRACT.device_type

    def __init__(
        self,
        device_id: str = "horiba-spec-vis",
        *,
        host: str = HoribaSpectrometer.DEFAULT_HOST,
        port: int = HoribaSpectrometer.DEFAULT_PORT,
        exposure_s: float = 1.0,
        accumulations: int = 1,
        center_wavelength_range_nm: tuple[float, float] | None = None,
        spectrometer: HoribaSpectrometer | None = None,
    ) -> None:
        self._device_id = device_id
        self._range_nm = center_wavelength_range_nm
        self._spectrometer = spectrometer if spectrometer is not None else HoribaSpectrometer(
            host,
            port,
            exposure_s=exposure_s,
            accumulations=accumulations,
            center_wavelength_range_nm=center_wavelength_range_nm,
        )
        self._gratings: tuple[str, ...] = ()
        self._detector: str | None = None
        self._last_wavelengths: list[float] | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        # Logs in to LabSpec and applies exposure and accumulations; moves nothing.
        self._spectrometer.connect()
        self._gratings = self._spectrometer.get_available_gratings()
        self._detector = self._spectrometer.get_detector()

    def disconnect(self) -> None:
        # Logs out of this TCP session only; LabSpec keeps running.
        self._last_wavelengths = None
        self._spectrometer.close()

    def status(self) -> str:
        return "ready" if self._spectrometer.is_connected else "disconnected"

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._spectrometer.is_connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        hints: dict[str, Any] = {}
        if self._gratings:
            hints["choices"] = {"set_grating.grating": list(self._gratings)}
        if self._range_nm is not None:
            hints["limits"] = {"set_center_wavelength.value": list(self._range_nm)}
        metadata: dict[str, Any] = {
            "manufacturer": "Horiba",
            "model": "LabSpec 6 spectrometer",
            "server": self._spectrometer.server_banner,
            "detector": self._detector,
            "capabilities": self.allowed_methods(),
        }
        if hints:
            metadata["hints"] = hints
        return metadata

    # ---- spectra ----------------------------------------------------------------

    def acquire_spectrum(self) -> list[float]:
        spectrum = self._spectrometer.acquire_spectrum()
        self._last_wavelengths = spectrum.wavelengths_nm.tolist()
        return spectrum.counts.tolist()

    def get_spectrum_wavelengths(self) -> list[float]:
        if self._last_wavelengths is None:
            raise RuntimeError("No spectrum has been acquired yet; call acquire_spectrum() first.")
        return list(self._last_wavelengths)

    # ---- settings ---------------------------------------------------------------

    def read_exposure(self) -> float:
        return self._spectrometer.get_exposure_s()

    def set_exposure(self, value: float) -> float:
        self._spectrometer.set_exposure_s(value)
        return self._spectrometer.get_exposure_s()

    def read_accumulations(self) -> float:
        return float(self._spectrometer.get_accumulations())

    def set_accumulations(self, value: float) -> float:
        count = float(value)
        if not math.isfinite(count) or not count.is_integer():
            raise ValueError(f"Accumulations must be a whole number; got {value!r}.")
        self._spectrometer.set_accumulations(int(count))
        return float(self._spectrometer.get_accumulations())

    def read_center_wavelength(self) -> float:
        return self._spectrometer.get_center_wavelength_nm()

    def set_center_wavelength(self, value: float) -> float:
        self._spectrometer.set_center_wavelength_nm(value)
        return self._spectrometer.get_center_wavelength_nm()

    def read_acquisition_time(self) -> float:
        return self._spectrometer.get_acquisition_time_s()

    def get_grating(self) -> str:
        return self._spectrometer.get_grating()

    def set_grating(self, grating: str) -> str:
        self._spectrometer.set_grating(grating)
        return self._spectrometer.get_grating()
