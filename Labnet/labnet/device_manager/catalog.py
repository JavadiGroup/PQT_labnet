from __future__ import annotations

from collections.abc import Iterable, Iterator

from labnet.devices.core.protocol import PhysicalDevice

from .errors import (
    DeviceNotFoundError,
    DuplicateDeviceIdError,
    InvalidConfigurationError,
)


class DeviceCatalog:
    """Immutable, duplicate-rejecting catalog of physical devices."""

    def __init__(self, devices: Iterable[PhysicalDevice]) -> None:
        by_id: dict[str, PhysicalDevice] = {}
        for device in devices:
            device_id = device.device_id
            if not isinstance(device_id, str) or not device_id.strip():
                raise InvalidConfigurationError(
                    "Every physical device must have a non-empty string device_id."
                )
            device_id = device_id.strip()
            if device_id in by_id:
                raise DuplicateDeviceIdError(
                    f"Duplicate physical device ID: {device_id}",
                    details={"device_id": device_id},
                )
            by_id[device_id] = device
        self._by_id = by_id

    def get(self, device_id: str) -> PhysicalDevice:
        try:
            return self._by_id[device_id]
        except KeyError as exc:
            raise DeviceNotFoundError(
                f"Device not found: {device_id}",
                details={"device_id": device_id},
            ) from exc

    def find(self, device_id: str) -> PhysicalDevice | None:
        return self._by_id.get(device_id)

    @property
    def device_ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    @property
    def devices(self) -> tuple[PhysicalDevice, ...]:
        return tuple(self._by_id.values())

    def items(self) -> tuple[tuple[str, PhysicalDevice], ...]:
        return tuple(self._by_id.items())

    def __iter__(self) -> Iterator[PhysicalDevice]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = ["DeviceCatalog"]
