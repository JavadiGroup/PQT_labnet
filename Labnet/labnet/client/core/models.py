from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def parse_datetime(value: str) -> datetime:
    """Parse an ISO UTC timestamp."""
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


@dataclass(frozen=True)
class AuthenticatedUser:
    user: str
    role: str


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    device_type: str
    host: str
    rpc_url: str
    status: str
    last_seen: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceInfo":
        return cls(
            device_id=data["device_id"],
            device_type=data["device_type"],
            host=data["host"],
            rpc_url=data["rpc_url"],
            status=data["status"],
            last_seen=parse_datetime(data["last_seen"]),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class LeaseInfo:
    lease_id: str
    device_id: str
    user: str
    created_at: datetime
    expires_at: datetime
    status: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LeaseInfo":
        return cls(
            lease_id=data["lease_id"],
            device_id=data["device_id"],
            user=data["user"],
            created_at=parse_datetime(data["created_at"]),
            expires_at=parse_datetime(data["expires_at"]),
            status=data["status"],
        )


@dataclass(frozen=True)
class LeaseGrant:
    lease: LeaseInfo
    device: DeviceInfo