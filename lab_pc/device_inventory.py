from __future__ import annotations

from labnet.devices.DummyDevice.physical import PhysicalDummyDevice


def create_devices() -> list[PhysicalDummyDevice]:
    """Instantiate the physical devices attached to this particular Lab PC."""

    return [PhysicalDummyDevice(device_id="dummy_device_1")]


__all__ = ["create_devices"]
