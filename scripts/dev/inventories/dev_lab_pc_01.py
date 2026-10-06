"""Simulated inventory for the first local development Lab PC."""

from __future__ import annotations

from labnet.devices.DummyAOM.physical import PhysicalDummyAOM
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.devices.DummyEOM.physical import PhysicalDummyEOM
from labnet.devices.DummyLaser.physical import PhysicalDummyLaser


def create_devices() -> list:
    # Device IDs must be unique across every Lab PC registered with Central.
    return [
        PhysicalDummyDevice(device_id="dev01-dummy-a"),
        PhysicalDummyDevice(device_id="dev01-dummy-b"),
        # The optics bench: a laser, an AOM and an EOM.
        PhysicalDummyLaser(device_id="dev01-laser"),
        PhysicalDummyAOM(device_id="dev01-aom"),
        PhysicalDummyEOM(device_id="dev01-eom"),
    ]
