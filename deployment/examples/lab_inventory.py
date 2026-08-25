"""Machine-owned device inventory kept outside versioned LabNet runtimes."""

from __future__ import annotations

from labnet.devices.DummyDevice import PhysicalDummyDevice


def create_devices() -> list[PhysicalDummyDevice]:
    # Replace these examples with the adapters and stable IDs attached to this
    # machine. Never store credentials in this module.
    return [PhysicalDummyDevice(device_id="dummy-device-1")]


__all__ = ["create_devices"]

