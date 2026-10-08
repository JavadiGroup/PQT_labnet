from __future__ import annotations

import math
import time
from typing import Any, Callable

from ..core import BaseDeviceInterface, DeviceAvailability
from ...grpc.generated.device_contracts import SIMULATED_TUNABLE_LASER_CONTRACT


class PhysicalSimulatedTunableLaser(BaseDeviceInterface):
    """A grating-tuned laser stand-in whose tuning motor moves in whole steps.

    Step 0 is the bottom of the tuning range and each step is ``STEP_NM``. A
    move starts at once and takes ``distance / velocity`` to finish, like a
    real motor: readings in between show where it is, and ``stop()`` halts it
    there. ``velocity_mode="slow"`` moves at a tenth of the set velocity.

    It publishes the ``velocity_mode`` choices but, like many vendor drivers,
    no numeric limits; a Lab PC inventory gives it ``site_limits`` before a
    protocol can automate it.
    """

    device_type = SIMULATED_TUNABLE_LASER_CONTRACT.device_type
    WAVELENGTH_RANGE_NM = (765.0, 795.0)
    STEP_NM = 0.001
    VELOCITY_RANGE_NM_S = (0.01, 5.0)
    VELOCITY_MODES = ("fast", "slow")
    SLOW_FACTOR = 0.1

    def __init__(
        self,
        device_id: str = "simulated_tunable_laser_1",
        *,
        wavelength_nm: float = 780.0,
        velocity_nm_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._device_id = device_id
        self._clock = clock
        self._max_steps = round((self.WAVELENGTH_RANGE_NM[1] - self.WAVELENGTH_RANGE_NM[0]) / self.STEP_NM)
        self._velocity_nm_s = self._checked_velocity(velocity_nm_s)
        start = self._steps_for(self._checked_wavelength(wavelength_nm))
        # The current move: from, to, when it started and its speed in steps/s.
        self._from = self._to = start
        self._started = 0.0
        self._speed = 1.0
        self._connected = False

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self.stop()
        self._connected = False

    def status(self) -> str:
        if not self._connected:
            return "disconnected"
        return "moving" if self._position() != self._to else "ready"

    def availability(self) -> DeviceAvailability:
        return DeviceAvailability.AVAILABLE if self._connected else DeviceAvailability.UNAVAILABLE

    def metadata(self) -> dict[str, Any]:
        return {
            "manufacturer": "LabNet",
            "model": "SimulatedTunableLaser",
            "wavelength_range_nm": list(self.WAVELENGTH_RANGE_NM),
            "step_nm": self.STEP_NM,
            "velocity_nm_s": self._velocity_nm_s,
            "capabilities": self.allowed_methods(),
            "hints": {"choices": {"move_steps.velocity_mode": list(self.VELOCITY_MODES)}},
        }

    # ---- motion -----------------------------------------------------------------

    def move_steps(self, relative_steps: int, velocity_mode: str | None = None) -> int:
        if isinstance(relative_steps, bool) or not isinstance(relative_steps, int):
            raise ValueError("relative_steps must be a whole number.")
        mode = "fast" if velocity_mode is None else velocity_mode
        if mode not in self.VELOCITY_MODES:
            raise ValueError(f"velocity_mode must be one of {', '.join(self.VELOCITY_MODES)}.")
        target = self._position() + relative_steps
        if not 0 <= target <= self._max_steps:
            raise ValueError(
                f"Moving {relative_steps} steps would leave the tuning range "
                f"(0 to {self._max_steps} steps)."
            )
        self._start_move(target, self.SLOW_FACTOR if mode == "slow" else 1.0)
        return target

    def go_to_wavelength_nm(self, wavelength_nm: float) -> float:
        target = self._steps_for(self._checked_wavelength(wavelength_nm))
        self._start_move(target, 1.0)
        return self._wavelength_at(target)

    def set_velocity_nm_s(self, velocity_nm_s: float) -> float:
        self._velocity_nm_s = self._checked_velocity(velocity_nm_s)
        return self._velocity_nm_s

    def stop(self) -> float:
        here = self._position()
        self._from = self._to = here
        return self._wavelength_at(here)

    # ---- readings ---------------------------------------------------------------

    def read_wavelength(self) -> float:
        self._require_connected()
        return self._wavelength_at(self._position())

    def read_position_steps(self) -> float:
        self._require_connected()
        return float(self._position())

    # ---- helpers ----------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("The laser is not connected.")

    def _start_move(self, target: int, factor: float) -> None:
        self._require_connected()
        self._from = self._position()
        self._to = target
        self._started = self._clock()
        self._speed = self._velocity_nm_s * factor / self.STEP_NM

    def _position(self) -> int:
        """Where the motor is now, in whole steps."""

        if self._from == self._to:
            return self._to
        travelled = int((self._clock() - self._started) * self._speed)
        if travelled >= abs(self._to - self._from):
            self._from = self._to
            return self._to
        return self._from + int(math.copysign(travelled, self._to - self._from))

    def _steps_for(self, wavelength_nm: float) -> int:
        return round((wavelength_nm - self.WAVELENGTH_RANGE_NM[0]) / self.STEP_NM)

    def _wavelength_at(self, steps: int) -> float:
        return round(self.WAVELENGTH_RANGE_NM[0] + steps * self.STEP_NM, 6)

    @classmethod
    def _checked_wavelength(cls, value: float) -> float:
        wavelength = float(value)
        low, high = cls.WAVELENGTH_RANGE_NM
        if not math.isfinite(wavelength) or not low <= wavelength <= high:
            raise ValueError(f"Wavelength must be between {low:g} and {high:g} nm.")
        return wavelength

    @classmethod
    def _checked_velocity(cls, value: float) -> float:
        velocity = float(value)
        low, high = cls.VELOCITY_RANGE_NM_S
        if not math.isfinite(velocity) or not low <= velocity <= high:
            raise ValueError(f"Velocity must be between {low:g} and {high:g} nm/s.")
        return velocity
