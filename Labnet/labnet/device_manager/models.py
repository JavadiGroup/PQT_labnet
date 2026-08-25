from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from labnet.devices.core import DeviceAvailability


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class ManagerState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class WorkerActivity:
    """Atomic activity snapshot used by the update drain barrier."""

    queued_count: int
    running_count: int
    quiescing: bool

    @property
    def idle(self) -> bool:
        return self.queued_count == 0 and self.running_count == 0


@dataclass(frozen=True, slots=True)
class FailureInfo:
    code: str
    message: str
    exception_type: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "exception_type": self.exception_type,
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EndpointDescriptor:
    """Versioned network endpoint advertised to the Central Server."""

    transport: str
    target: str
    api_version: str
    secure: bool = True
    tls_server_name: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("transport", "target", "api_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Endpoint {name} must be a non-empty string.")
            object.__setattr__(self, name, value.strip())

        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(self.attributes)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "transport": self.transport,
            "target": self.target,
            "api_version": self.api_version,
            "secure": self.secure,
        }
        if self.tls_server_name is not None:
            payload["tls_server_name"] = self.tls_server_name
        if self.attributes:
            payload["attributes"] = dict(self.attributes)
        return payload


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    device_id: str
    device_type: str
    state: DeviceState
    status: str
    availability: DeviceAvailability = DeviceAvailability.UNKNOWN
    metadata: Mapping[str, Any] = field(default_factory=dict)
    allowed_methods: tuple[str, ...] = ()
    failure: FailureInfo | None = None
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.availability, DeviceAvailability):
            object.__setattr__(
                self,
                "availability",
                DeviceAvailability(self.availability),
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "allowed_methods", tuple(self.allowed_methods))

    @property
    def available(self) -> bool:
        return (
            self.state is DeviceState.READY
            and self.availability is DeviceAvailability.AVAILABLE
        )

    def to_registration_dict(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        metadata.setdefault("capabilities", list(self.allowed_methods))
        metadata["runtime_state"] = self.state.value
        metadata["availability"] = self.availability.value
        if self.failure is not None:
            metadata["failure"] = self.failure.to_dict()
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "status": self.status,
            "metadata": metadata,
        }

    def to_heartbeat_dict(self) -> dict[str, Any]:
        payload = self.to_registration_dict()
        payload.pop("device_type")
        return payload


@dataclass(frozen=True, slots=True)
class StartupReport:
    snapshots: tuple[DeviceSnapshot, ...]

    @property
    def ready_device_ids(self) -> tuple[str, ...]:
        return tuple(item.device_id for item in self.snapshots if item.available)

    @property
    def failed_device_ids(self) -> tuple[str, ...]:
        return tuple(item.device_id for item in self.snapshots if not item.available)

    @property
    def any_ready(self) -> bool:
        return bool(self.ready_device_ids)


__all__ = [
    "DeviceAvailability",
    "DeviceSnapshot",
    "DeviceState",
    "EndpointDescriptor",
    "FailureInfo",
    "ManagerState",
    "StartupReport",
    "WorkerActivity",
]
