"""Simulated inventory for the second local development Lab PC."""

from __future__ import annotations

from labnet_sim.dummy_awg.physical import PhysicalDummyAWG
from labnet_sim.dummy_device.physical import PhysicalDummyDevice
from labnet_sim.simulated_wavemeter.physical import PhysicalSimulatedWavemeter
from labnet.devices.core import site_limits


def create_devices() -> list:
    # Three device families on one Lab PC; IDs are unique network-wide.
    return [
        # Site limits (docs/drivers.md, "Site limits"): enforced by this Lab PC.
        site_limits(PhysicalDummyDevice(device_id="dev02-dummy-a"), limits={"write_value.value": (-100.0, 100.0)}),
        PhysicalSimulatedWavemeter(device_id="dev02-wavemeter"),
        PhysicalDummyAWG(device_id="dev02-awg"),
    ]
