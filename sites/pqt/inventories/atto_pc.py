"""Instruments attached to atto-pc (192.168.1.149).

There is one module like this per Lab PC. Select it in that computer's
lab-pc.env:

    LABNET_DEVICE_INVENTORY=C:/ProgramData/LabNet/site/inventories/atto_pc.py:create_devices

Device IDs must be unique across the whole network and must not change, so
name them after the instrument or its fixed position, not a USB port.
Creating a device never touches hardware; the Lab PC connects them at startup.
"""

from __future__ import annotations

from typing import Any

from labnet.devices.core import site_limits
from pqt_drivers import PhysicalHoribaSpectrometer, PhysicalSacherLaser, PhysicalWs7Wavemeter

# The VIS calibration covers 755.698-783.686 nm. Keep 1 nm from each end,
# rounded inward to whole nanometres.
SACHER_VIS_RANGE_NM = (757.0, 782.0)


def create_devices() -> list[Any]:
    return [
        # Reads only. HighFinesse keeps running when the Lab PC stops.
        PhysicalWs7Wavemeter("ws7", channel=1),
        # Moves and rebases are refused outside the site range for every client,
        # including the Control Center's rebase box and automation.
        site_limits(
            PhysicalSacherLaser("sacher-vis", port="USB0", laser_type="VIS"),
            limits={
                "move_to_wavelength_nm.wavelength_nm": SACHER_VIS_RANGE_NM,
                "rebase_position_axis.measured_wavelength_nm": SACHER_VIS_RANGE_NM,
            },
        ),
        # LabSpec must be running with its Raman-AFM server enabled (127.0.0.1:1234).
        PhysicalHoribaSpectrometer("horiba-spec-vis"),
    ]
