from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseDeviceInterface(ABC):
    """
    Generic interface that every physical and remote device should satisfy. This is meant to
    be minimal in nature and simply ensure that each device class has some minimum functionality
    for diagnostics and testing.
    """

    device_type: str

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Return the unique device ID."""
        raise NotImplementedError
