from __future__ import annotations

from devices import (
    PhysicalFakeDevice
)


def create_devices() -> list:
    """
    Create local device objects for this lab PC.

    This is the only required function that lab_pc.py expects.
    """

    return [
        PhysicalFakeDevice(device_id="fake_device_1"),
    ]