"""Typed models shared by the Central HTTP and device gRPC clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


def parse_datetime(value: str | datetime) -> datetime:
    """Parse an ISO-8601 timestamp and always return an aware UTC value."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise TypeError("timestamp must be an ISO-8601 string or datetime.")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: str
    role: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuthenticatedUser":
        user = data.get("user", data.get("username"))
        if not isinstance(user, str) or not user.strip():
            raise ValueError("user must be a non-empty string.")
        return cls(user=user.strip(), role=_required_text(data, "role"))


@dataclass(frozen=True, slots=True)
class EndpointDescriptor:
    """A versioned direct device-service endpoint returned by Central."""

    transport: str
    target: str
    api_version: str
    secure: bool
    tls_server_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.transport != "grpc":
            raise ValueError("endpoint transport must be 'grpc'.")
        for name in ("target", "api_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"endpoint {name} must be a non-empty string.")
        if not isinstance(self.secure, bool):
            raise TypeError("endpoint secure must be a boolean.")
        if self.tls_server_name is not None:
            if not self.tls_server_name.strip():
                raise ValueError("tls_server_name cannot be empty when supplied.")
            if any(ord(character) < 0x21 for character in self.tls_server_name):
                raise ValueError("tls_server_name contains invalid characters.")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EndpointDescriptor":
        attributes = data.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError("endpoint attributes must be an object.")
        secure = data.get("secure")
        if not isinstance(secure, bool):
            raise ValueError("endpoint secure must be a boolean.")
        tls_server_name = data.get("tls_server_name")
        if tls_server_name is not None and not isinstance(tls_server_name, str):
            raise ValueError("tls_server_name must be a string or null.")
        return cls(
            transport=_required_text(data, "transport").lower(),
            target=_required_text(data, "target"),
            api_version=_required_text(data, "api_version"),
            secure=secure,
            tls_server_name=tls_server_name,
            attributes=dict(attributes),
        )


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    device_id: str
    manager_id: str
    endpoint: EndpointDescriptor
    device_type: str = ""
    runtime_state: str = "unknown"
    availability: str = "unknown"
    status_text: str = ""
    last_seen: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    effective_available: bool = False

    @property
    def type(self) -> str:
        """The v1 wire field is ``type``; ``device_type`` is clearer in Python."""

        return self.device_type

    @property
    def status(self) -> str:
        """Compatibility alias for callers migrating from the legacy client."""

        return self.runtime_state

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeviceInfo":
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, Mapping):
            raise ValueError("device endpoint must be an object.")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("device metadata must be an object.")
        raw_last_seen = data.get("last_seen")
        effective_available = data.get("effective_available", False)
        if not isinstance(effective_available, bool):
            raise ValueError("effective_available must be a boolean.")
        return cls(
            device_id=_required_text(data, "device_id"),
            manager_id=_required_text(data, "manager_id"),
            endpoint=EndpointDescriptor.from_dict(endpoint),
            device_type=str(data.get("type", data.get("device_type", ""))).strip(),
            runtime_state=str(data.get("runtime_state", "unknown")).strip(),
            availability=str(data.get("availability", "unknown")).strip(),
            status_text=str(data.get("status_text", "")).strip(),
            last_seen=parse_datetime(raw_last_seen) if raw_last_seen is not None else None,
            metadata=dict(metadata),
            effective_available=effective_available,
        )


@dataclass(frozen=True, slots=True)
class LeaseInfo:
    lease_id: str
    device_id: str
    user: str
    created_at: datetime
    expires_at: datetime
    state: str
    last_renewed_at: datetime | None = None
    last_activity_at: datetime | None = None
    idle_timeout_seconds: int | None = None

    @property
    def status(self) -> str:
        """Compatibility alias for the pre-v1 field name."""

        return self.state

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LeaseInfo":
        state = data.get("state", data.get("status"))
        if not isinstance(state, str) or not state.strip():
            raise ValueError("lease state must be a non-empty string.")
        last_renewed_at = data.get("last_renewed_at", data.get("renewed_at"))
        last_activity_at = data.get("last_activity_at")
        idle_timeout_seconds = data.get("idle_timeout_seconds")
        if idle_timeout_seconds is not None and (
            isinstance(idle_timeout_seconds, bool)
            or not isinstance(idle_timeout_seconds, int)
            or idle_timeout_seconds <= 0
        ):
            raise ValueError("idle_timeout_seconds must be a positive integer or null.")
        return cls(
            lease_id=_required_text(data, "lease_id"),
            device_id=_required_text(data, "device_id"),
            user=_required_text(data, "user"),
            created_at=parse_datetime(data["created_at"]),
            expires_at=parse_datetime(data["expires_at"]),
            state=state.strip(),
            last_renewed_at=(
                parse_datetime(last_renewed_at)
                if last_renewed_at is not None
                else None
            ),
            last_activity_at=(
                parse_datetime(last_activity_at)
                if last_activity_at is not None
                else None
            ),
            idle_timeout_seconds=idle_timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    lease: LeaseInfo
    device: DeviceInfo

    def __post_init__(self) -> None:
        if self.lease.device_id != self.device.device_id:
            raise ValueError("lease and device IDs in a grant must match.")
        if self.lease.state != "active":
            raise ValueError("a lease grant must contain an active lease.")
        if self.lease.expired:
            raise ValueError("a lease grant cannot contain an expired lease.")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LeaseGrant":
        lease = data.get("lease")
        device = data.get("device")
        if not isinstance(lease, Mapping) or not isinstance(device, Mapping):
            raise ValueError("lease grant must contain lease and device objects.")
        return cls(lease=LeaseInfo.from_dict(lease), device=DeviceInfo.from_dict(device))


class MeasurementQuality(StrEnum):
    UNSPECIFIED = "unspecified"
    GOOD = "good"
    SUSPECT = "suspect"
    BAD = "bad"


@dataclass(frozen=True, slots=True)
class Measurement:
    """One typed scalar measurement returned by a device RPC."""

    operation_id: str
    sequence: int
    timestamp_ns: int
    value: float
    unit: str
    quality: MeasurementQuality
    status: str
    channel: str | None = None

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ns / 1_000_000_000, timezone.utc)


__all__ = [
    "AuthenticatedUser",
    "DeviceInfo",
    "EndpointDescriptor",
    "LeaseGrant",
    "LeaseInfo",
    "Measurement",
    "MeasurementQuality",
    "parse_datetime",
]
