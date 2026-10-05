"""Fallback inventory used when no LABNET_DEVICE_INVENTORY is configured."""

from __future__ import annotations

from labnet.devices.DummyDevice.physical import PhysicalDummyDevice


def simulated_devices() -> list[PhysicalDummyDevice]:
    """One simulated device, so a bare development Lab PC is usable."""

    return [PhysicalDummyDevice(device_id="dummy_device_1")]


__all__ = ["simulated_devices"]
