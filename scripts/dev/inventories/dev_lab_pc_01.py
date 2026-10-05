"""Simulated inventory for the first local development Lab PC."""

from __future__ import annotations

from labnet.devices.DummyDevice.physical import PhysicalDummyDevice


def create_devices() -> list[PhysicalDummyDevice]:
    # Device IDs must be unique across every Lab PC registered with Central.
    return [
        PhysicalDummyDevice(device_id="dev01-dummy-a"),
        PhysicalDummyDevice(device_id="dev01-dummy-b"),
    ]
