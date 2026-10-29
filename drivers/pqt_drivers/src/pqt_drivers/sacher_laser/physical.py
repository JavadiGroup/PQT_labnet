"""The Sacher laser on LabNet: a thin wrapper around PQT_devices' ``SacherLaser``.

All motion, calibration, position bookkeeping and range checks live in
PQT_devices. This class maps the proto's methods onto the driver and
publishes the driver's own limits; a Lab PC inventory narrows them with
``site_limits``.

Never served: ``jog_steps`` and ``calibration_mode`` (supervised calibration
only), ``reference_wavelength_nm`` (rebase from a wavemeter reading instead),
and ``stop`` (calls to one device run one at a time, so it would only run
after the move it was meant to stop).
"""

from __future__ import annotations

from typing import Any

from labnet.devices.core import BaseDeviceInterface, DeviceAvailability
from PQT_devices.laser.sacher_laser import SacherLaser

from .._generated.contracts import SACHER_LASER_CONTRACT


class PhysicalSacherLaser(BaseDeviceInterface):
    """One Sacher laser. The Lab PC calls it from a single worker thread.

    Args:
        device_id: Network-wide ID, e.g. ``"sacher-vis"``.
        port: EPOS USB port, normally ``"USB0"``.
        laser_type: Built-in calibration, ``"VIS"`` or ``"IR"``.
        velocity_nm_s: Tuning velocity applied on connect.
        laser: An already-built driver (tests); the arguments above are then unused.
    """

    device_type = SACHER_LASER_CONTRACT.device_type

    def __init__(
        self,
        device_id: str = "sacher-vis",
        *,
        port: str = "USB0",
        laser_type: str = "VIS",
        velocity_nm_s: float = 2.0,
        laser: SacherLaser | None = None,
    ) -> None:
        self._device_id = device_id
        self._laser = laser if laser is not None else SacherLaser(
            port, laser_type, velocity_nm_s=velocity_nm_s
        )

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        # Refuses a stored position outside the calibration; never moves the motor.
        self._laser.connect()

    def disconnect(self) -> None:
        # Disables the motor and closes the EPOS connection.
        self._laser.close()

    def status(self) -> str:
        return self._laser.get_state().lower() if self._laser.is_connected else "disconnected"

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._laser.is_connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        calibration = self._laser.calibration
        wavelength_range = [calibration.wavelength_min_nm, calibration.wavelength_max_nm]
        return {
            "manufacturer": "Sacher Lasertechnik",
            "model": "Sacher laser (maxon EPOS2 tuning motor)",
            "calibration": calibration.name,
            "calibration_verified": calibration.verified,
            "capabilities": self.allowed_methods(),
            # The driver's own limits. The Lab PC enforces them, and site limits may only narrow them.
            "hints": {
                "limits": {
                    "move_to_wavelength_nm.wavelength_nm": wavelength_range,
                    "rebase_position_axis.measured_wavelength_nm": wavelength_range,
                    "set_velocity.value": [SacherLaser.MIN_VELOCITY_NM_S, SacherLaser.MAX_VELOCITY_NM_S],
                }
            },
        }

    # ---- motion -----------------------------------------------------------------

    def move_to_wavelength_nm(self, wavelength_nm: float) -> None:
        self._laser.move_to_wavelength_nm(wavelength_nm)

    def rebase_position_axis(self, measured_wavelength_nm: float) -> float:
        self._laser.rebase_position_axis(measured_wavelength_nm)
        return self._laser.get_live_wavelength_nm()

    # ---- readings and settings --------------------------------------------------

    def read_live_wavelength(self, channel: str | None = None) -> float:
        if channel is not None:
            raise ValueError(f"The Sacher laser has no channels; got {channel!r}.")
        return self._laser.get_live_wavelength_nm()

    def read_commanded_wavelength(self) -> float:
        return self._laser.get_commanded_wavelength_nm()

    def read_velocity(self) -> float:
        return self._laser.get_velocity_nm_s()

    def set_velocity(self, value: float) -> float:
        self._laser.set_velocity_nm_s(value)
        return self._laser.get_velocity_nm_s()

    def get_state(self) -> str:
        return self._laser.get_state()
