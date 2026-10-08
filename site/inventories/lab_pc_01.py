"""Instruments attached to lab_pc_1 (192.168.50.20).

There is one module like this per Lab PC. Select it in that computer's
lab-pc.env:

    LABNET_DEVICE_INVENTORY=C:/ProgramData/LabNet/site/inventories/lab_pc_01.py:create_devices

Device IDs must be unique across the whole network and must not change, so
name them after the instrument or its fixed position, not a USB port.
"""

from __future__ import annotations

from labnet.devices.SimulatedWavemeter.physical import PhysicalSimulatedWavemeter

# The Sacher driver is its own package (separate repository), installed on this Lab PC:
# from <sacher driver package> import PhysicalSacherLaser
# from labnet.devices.core import site_limits


def create_devices() -> list[PhysicalSimulatedWavemeter]:
    # No real PQT instrument drivers exist yet; replace this simulated device
    # with the real drivers for this computer when they do.
    return [
        PhysicalSimulatedWavemeter(device_id="lab-pc-01-wavemeter"),
        # A Sacher laser with site limits (packages/labnet/README.md, "Site limits").
        # This Lab PC then refuses, for every client, a call outside them; and
        # automation can bound only numbers that have a published limit. The
        # Sacher driver publishes none, so set one for every number to automate:
        #
        # site_limits(
        #     PhysicalSacherLaser(device_id="lab-pc-01-sacher"),
        #     limits={
        #         "go_to_wavelength_nm.wavelength_nm": (770.0, 790.0),
        #         "jog_steps.relative_steps": (-2000, 2000),
        #         "set_velocity_nm_s.velocity_nm_s": (0.01, 1.0),
        #     },
        #     # Only the slower, configured scan speed. With a site choice the
        #     # argument must be sent in every call (the driver's default isn't used).
        #     choices={"go_to_wavelength_nm.velocity_mode": ["scan"]},
        # ),
    ]
