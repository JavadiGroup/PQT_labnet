"""Instruments attached to lab_pc_1 (192.168.50.20).

There is one module like this per Lab PC. Select it in that computer's
lab-pc.env:

    LABNET_DEVICE_INVENTORY=C:/ProgramData/LabNet/site/inventories/lab_pc_01.py:create_devices

Device IDs must be unique across the whole network and must not change, so
name them after the instrument or its fixed position, not a USB port.
"""

from __future__ import annotations

from labnet.devices.SimulatedWavemeter.physical import PhysicalSimulatedWavemeter


def create_devices() -> list[PhysicalSimulatedWavemeter]:
    # No real PQT instrument drivers exist yet; replace this simulated device
    # with the real drivers for this computer when they do.
    return [PhysicalSimulatedWavemeter(device_id="lab-pc-01-wavemeter")]
