from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseDeviceInterface(ABC):
    """
    Generic interface that every physical and remote device should satisfy.
    """

    device_type: str

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Return the unique device ID."""
        raise NotImplementedError

    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        """Return device metadata."""
        raise NotImplementedError