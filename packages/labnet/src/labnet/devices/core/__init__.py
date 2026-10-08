"""Core interfaces shared by physical and remote devices."""

from importlib import import_module
from typing import Any

from .base import BaseDeviceInterface
from .limits import DeviceLimitsError, SiteLimits, get_site_limits, site_limits
from .protocol import AvailabilityAwareDevice, DeviceAvailability, PhysicalDevice

__all__ = [
    "AvailabilityAwareDevice",
    "BaseDeviceInterface",
    "ContractDeviceFacade",
    "DeviceAvailability",
    "DeviceLimitsError",
    "PhysicalDevice",
    "RemoteDeviceBinding",
    "SiteLimits",
    "get_site_limits",
    "site_limits",
]


def __getattr__(name: str) -> Any:
    if name == "ContractDeviceFacade":
        value = getattr(import_module(".facade", __name__), name)
    elif name == "RemoteDeviceBinding":
        value = getattr(import_module(".remote", __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
