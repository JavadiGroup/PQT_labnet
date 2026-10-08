"""Simulated inventory for the first local development Lab PC."""

from __future__ import annotations

from labnet_sim.dummy_aom.physical import PhysicalDummyAOM
from labnet_sim.dummy_device.physical import PhysicalDummyDevice
from labnet_sim.dummy_eom.physical import PhysicalDummyEOM
from labnet_sim.dummy_laser.physical import PhysicalDummyLaser
from labnet.devices.core import site_limits

# Site limits (see "Site limits" in packages/labnet/README.md): this Lab PC refuses every
# call outside them, for every client. dummy_device's driver publishes no limit
# for write_value, so without one it can't be automated ("no limit, no automation").
DUMMY_WRITE_RANGE = {"write_value.value": (-100.0, 100.0)}


def create_devices() -> list:
    # Device IDs must be unique across every Lab PC registered with Central.
    return [
        site_limits(PhysicalDummyDevice(device_id="dev01-dummy-a"), limits=DUMMY_WRITE_RANGE),
        site_limits(PhysicalDummyDevice(device_id="dev01-dummy-b"), limits=DUMMY_WRITE_RANGE),
        # The optics bench: a laser, an AOM and an EOM.
        PhysicalDummyLaser(device_id="dev01-laser"),
        # The driver allows 60-100 MHz; this bench's AOM is narrowed to 65-95 MHz.
        site_limits(PhysicalDummyAOM(device_id="dev01-aom"), limits={"set_frequency.value": (65.0, 95.0)}),
        PhysicalDummyEOM(device_id="dev01-eom"),
    ]
