"""Core interfaces shared by physical and remote devices."""

from .base import BaseDeviceInterface
from .protocol import AvailabilityAwareDevice, DeviceAvailability, PhysicalDevice

__all__ = [
    "AvailabilityAwareDevice",
    "BaseDeviceInterface",
    "DeviceAvailability",
    "PhysicalDevice",
]
