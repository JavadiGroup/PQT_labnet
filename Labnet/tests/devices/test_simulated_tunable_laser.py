"""The simulated step-tuned laser: motion over time, stop, validation, and its contract."""

from __future__ import annotations

import pytest

from labnet import SimulatedTunableLaser
from labnet.devices.SimulatedTunableLaser.physical import PhysicalSimulatedTunableLaser
from labnet.contracts import validate_implementation
from labnet.grpc.generated.device_contracts import SIMULATED_TUNABLE_LASER_CONTRACT


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def laser(clock: Clock) -> PhysicalSimulatedTunableLaser:
    device = PhysicalSimulatedTunableLaser("laser-1", wavelength_nm=780.0, velocity_nm_s=1.0, clock=clock)
    device.connect()
    return device


def test_it_satisfies_its_contract_and_the_facade_is_named_after_the_service() -> None:
    validate_implementation(PhysicalSimulatedTunableLaser, SIMULATED_TUNABLE_LASER_CONTRACT)
    assert SIMULATED_TUNABLE_LASER_CONTRACT.device_type == "simulated_tunable_laser"
    assert SimulatedTunableLaser.physical_type is PhysicalSimulatedTunableLaser


def test_go_to_moves_at_the_set_velocity(laser: PhysicalSimulatedTunableLaser, clock: Clock) -> None:
    assert laser.read_position_steps() == 15000.0  # 780 nm is 15 nm above the 765 nm bottom
    assert laser.go_to_wavelength_nm(782.0004) == 782.0  # the nearest step
    assert laser.status() == "moving"
    clock.now += 1.0  # 1 nm/s
    assert laser.read_wavelength() == pytest.approx(781.0)
    clock.now += 1.5
    assert laser.read_wavelength() == 782.0 and laser.status() == "ready"


def test_slow_moves_at_a_tenth_and_stop_holds_where_it_is(laser: PhysicalSimulatedTunableLaser, clock: Clock) -> None:
    assert laser.move_steps(-2000, velocity_mode="slow") == 13000
    clock.now += 5.0  # 0.1 nm/s = 100 steps/s
    assert laser.stop() == pytest.approx(779.5)
    clock.now += 60.0
    assert laser.read_position_steps() == 14500.0 and laser.status() == "ready"


def test_set_velocity_applies_to_the_next_move(laser: PhysicalSimulatedTunableLaser, clock: Clock) -> None:
    assert laser.set_velocity_nm_s(5.0) == 5.0
    laser.move_steps(5000)
    clock.now += 0.5
    assert laser.read_position_steps() == 17500.0


@pytest.mark.parametrize("call, message", [
    (lambda d: d.go_to_wavelength_nm(800.0), "between 765 and 795 nm"),
    (lambda d: d.go_to_wavelength_nm(float("nan")), "between 765 and 795 nm"),
    (lambda d: d.move_steps(20000), "leave the tuning range"),
    (lambda d: d.move_steps(5, velocity_mode="turbo"), "fast, slow"),
    (lambda d: d.move_steps(5.0), "whole number"),
    (lambda d: d.move_steps(True), "whole number"),
    (lambda d: d.set_velocity_nm_s(0.0), "between 0.01 and 5 nm/s"),
])
def test_it_refuses_what_a_motor_cannot_do(laser: PhysicalSimulatedTunableLaser, call, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        call(laser)


def test_it_needs_a_connection_and_publishes_choices_but_no_limits(clock: Clock) -> None:
    laser = PhysicalSimulatedTunableLaser("laser-2", clock=clock)
    with pytest.raises(RuntimeError, match="not connected"):
        laser.read_wavelength()
    with pytest.raises(RuntimeError, match="not connected"):
        laser.go_to_wavelength_nm(770.0)
    assert laser.status() == "disconnected"
    assert laser.metadata()["hints"] == {"choices": {"move_steps.velocity_mode": ["fast", "slow"]}}
