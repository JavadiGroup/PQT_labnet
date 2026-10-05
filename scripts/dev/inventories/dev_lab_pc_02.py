"""Simulated inventory for the second local development Lab PC."""

from __future__ import annotations

from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.devices.SimulatedWavemeter.physical import PhysicalSimulatedWavemeter


def create_devices() -> list[PhysicalDummyDevice | PhysicalSimulatedWavemeter]:
    # Two device families on one Lab PC; IDs are unique network-wide.
    return [
        PhysicalDummyDevice(device_id="dev02-dummy-a"),
        PhysicalSimulatedWavemeter(device_id="dev02-wavemeter"),
    ]
