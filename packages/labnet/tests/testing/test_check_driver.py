"""labnet.testing.check_driver: the Lab PC's own startup checks, for driver authors."""

from __future__ import annotations

from typing import Any

import pytest

from labnet import (
    PhysicalDummyAOM,
    PhysicalDummyAWG,
    PhysicalDummyDevice,
    PhysicalDummyEOM,
    PhysicalDummyLaser,
    PhysicalSimulatedTunableLaser,
    PhysicalSimulatedWavemeter,
)
from labnet.devices.core import site_limits
from labnet.testing import DriverCheckError, acheck_driver, check_driver


@pytest.mark.parametrize("device", [
    PhysicalDummyDevice("dummy"),
    PhysicalDummyAOM("aom"),
    PhysicalDummyAWG("awg"),
    PhysicalDummyEOM("eom"),
    PhysicalDummyLaser("laser"),
    PhysicalSimulatedTunableLaser("tunable"),
    PhysicalSimulatedWavemeter("wavemeter"),
], ids=lambda device: device.device_type)
def test_every_built_in_driver_passes(device) -> None:
    snapshot = check_driver(device)
    assert snapshot.device_id == device.device_id
    assert snapshot.allowed_methods


class _Broken(PhysicalDummyDevice):
    def __init__(self, **changes: Any) -> None:
        super().__init__("broken")
        self._changes = changes

    def connect(self) -> None:
        if "connect" in self._changes:
            raise RuntimeError(self._changes["connect"])
        super().connect()

    def metadata(self) -> dict[str, Any]:
        return {**super().metadata(), **self._changes.get("metadata", {})}


def _without_read_value() -> PhysicalDummyDevice:
    class MissingMethod(PhysicalDummyDevice):
        read_value = None  # type: ignore[assignment]

    return MissingMethod("missing")


def _unknown_type() -> PhysicalDummyDevice:
    class Unknown(PhysicalDummyDevice):
        device_type = "not_a_registered_type"

    return Unknown("unknown")


@pytest.mark.parametrize("device, message", [
    (_unknown_type(), "No generated gRPC contract exists for device_type 'not_a_registered_type'"),
    (_without_read_value(), "missing callable method 'read_value'"),
    (_Broken(connect="no USB device"), "no USB device"),
    (_Broken(metadata={"hints": {"limits": {"write_value.typo": [0, 1]}}}), "has no argument 'typo'"),
    (_Broken(metadata={"opened": object()}), "not JSON-compatible"),
    (site_limits(PhysicalDummyAOM("narrow"), limits={"set_frequency.value": (10, 500)}), "site limits may only narrow"),
])
def test_what_a_lab_pc_would_refuse_is_reported(device, message: str) -> None:
    with pytest.raises(DriverCheckError, match=None) as caught:
        check_driver(device)
    assert message in str(caught.value)


async def test_it_works_inside_an_event_loop_too() -> None:
    snapshot = await acheck_driver(PhysicalDummyDevice("async"))
    assert snapshot.device_type == "dummy_device"
