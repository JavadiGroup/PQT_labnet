from __future__ import annotations

from enum import Enum
from typing import Any, Collection, Mapping, Protocol, runtime_checkable


class DeviceAvailability(str, Enum):
    """Standardized control-readiness reported by a physical device."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@runtime_checkable
class AvailabilityAwareDevice(Protocol):
    """Optional extension for devices that explicitly report availability."""

    def availability(self) -> DeviceAvailability:
        """Return whether control operations may currently be attempted."""
        ...


@runtime_checkable
class PhysicalDevice(Protocol):
    """Runtime contract required by the Lab PC device manager.

    With the exception of the immutable ``device_id`` used to build the
    catalog, the device manager invokes this contract exclusively on the
    device's dedicated worker thread. Hardware wrappers may therefore keep
    thread-affine driver sessions without adding their own executors.
    """

    device_type: str

    @property
    def device_id(self) -> str:
        """Return the unique, stable identifier for this physical device."""
        ...

    def connect(self) -> None:
        """Open the physical device and any associated driver resources."""
        ...

    def disconnect(self) -> None:
        """Close the physical device and release driver resources."""
        ...

    def status(self) -> str:
        """Return a human-readable transport-neutral status string.

        New physical drivers should additionally implement
        :class:`AvailabilityAwareDevice`; status text is retained for display
        and diagnostics rather than used as the primary health contract.
        """
        ...

    def metadata(self) -> Mapping[str, Any]:
        """Return JSON-compatible descriptive device metadata."""
        ...

    def allowed_methods(self) -> Collection[str]:
        """Return the physical methods that may be remotely invoked."""
        ...


__all__ = ["AvailabilityAwareDevice", "DeviceAvailability", "PhysicalDevice"]
