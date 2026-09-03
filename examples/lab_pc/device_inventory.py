from __future__ import annotations

import os

from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.devices.SacherLaser.physical import PhysicalSacherLaser
from labnet.devices.core import PhysicalDevice


def _enabled(name: str) -> bool:
    value = os.getenv(name, "false").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")


def create_devices() -> list[PhysicalDevice]:
    """Instantiate the physical devices attached to this particular Lab PC."""

    devices: list[PhysicalDevice] = [
        PhysicalDummyDevice(device_id="dummy_device_1")
    ]
    if _enabled("LABNET_SACHER_LASER_ENABLED"):
        devices.append(
            PhysicalSacherLaser(
                device_id=os.getenv(
                    "LABNET_SACHER_LASER_DEVICE_ID", "sacher_laser_1"
                ),
                port=os.getenv("LABNET_SACHER_LASER_PORT", "USB0"),
                laser_type=os.getenv("LABNET_SACHER_LASER_TYPE", "VIS"),
                dll_path=os.getenv("LABNET_SACHER_LASER_DLL_PATH") or None,
                velocity_nm_s=float(
                    os.getenv("LABNET_SACHER_LASER_VELOCITY_NM_S", "2.0")
                ),
                positioning_velocity_nm_s=float(
                    os.getenv(
                        "LABNET_SACHER_LASER_POSITIONING_VELOCITY_NM_S",
                        "2.0",
                    )
                ),
                movement_timeout_s=float(
                    os.getenv(
                        "LABNET_SACHER_LASER_MOVEMENT_TIMEOUT_SECONDS",
                        "120.0",
                    )
                ),
            )
        )
    return devices


__all__ = ["create_devices"]
