from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .common import LabNetIdentifier


RuntimeState = Literal[
    "new", "starting", "ready", "failed", "stopping", "stopped"
]
Availability = Literal["available", "unavailable", "unknown"]
AllocationState = Literal["free", "leased"]


class EndpointDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: Literal["grpc"] = "grpc"
    target: str = Field(min_length=1, max_length=512)
    api_version: str = Field(min_length=1, max_length=128)
    secure: bool = True
    tls_server_name: str | None = Field(default=None, max_length=255)
    attributes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport_security(self) -> "EndpointDescriptor":
        host = _target_host(self.target)
        if not self.secure and not _is_loopback(host):
            raise ValueError("Plaintext gRPC endpoints are allowed only on loopback.")
        if self.secure and self.tls_server_name is not None:
            if not self.tls_server_name.strip():
                raise ValueError("tls_server_name must not be blank.")
        return self


class DeviceRegistrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: LabNetIdentifier
    device_type: str = Field(min_length=1, max_length=128)
    runtime_state: RuntimeState = "new"
    availability: Availability = "unknown"
    status_text: str = Field(default="", max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_lab_pc_snapshot(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        metadata = dict(payload.get("metadata") or {})
        payload.setdefault("runtime_state", metadata.pop("runtime_state", "new"))
        payload.setdefault("availability", metadata.pop("availability", "unknown"))
        if "status_text" not in payload and "status" in payload:
            payload["status_text"] = payload.pop("status")
        payload["metadata"] = metadata
        return payload


class DeviceHeartbeatItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: LabNetIdentifier
    runtime_state: RuntimeState
    availability: Availability
    status_text: str = Field(default="", max_length=4096)
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_lab_pc_snapshot(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        metadata = dict(payload.get("metadata") or {})
        if "runtime_state" not in payload and "runtime_state" in metadata:
            payload["runtime_state"] = metadata.pop("runtime_state")
        if "availability" not in payload and "availability" in metadata:
            payload["availability"] = metadata.pop("availability")
        if "status_text" not in payload and "status" in payload:
            payload["status_text"] = payload.pop("status")
        if payload.get("metadata") is not None:
            payload["metadata"] = metadata
        return payload


class DeviceManagerRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_id: LabNetIdentifier
    host: str = Field(min_length=1, max_length=255)
    endpoint: EndpointDescriptor
    devices: list[DeviceRegistrationItem] = Field(min_length=1)

    @field_validator("devices")
    @classmethod
    def unique_device_ids(
        cls, devices: list[DeviceRegistrationItem]
    ) -> list[DeviceRegistrationItem]:
        ids = [device.device_id for device in devices]
        if len(ids) != len(set(ids)):
            raise ValueError("Device IDs must be unique within registration.")
        return devices


class DeviceManagerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_id: LabNetIdentifier
    host: str = Field(min_length=1, max_length=255)
    endpoint: EndpointDescriptor
    timestamp: AwareDatetime
    devices: list[DeviceHeartbeatItem]


class DevicePublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: LabNetIdentifier
    manager_id: LabNetIdentifier
    endpoint: EndpointDescriptor
    device_type: str
    runtime_state: RuntimeState
    availability: Availability
    status_text: str
    allocation_state: AllocationState
    effective_available: bool
    last_seen: datetime
    metadata: dict[str, Any]


class DeviceListResponse(BaseModel):
    devices: list[DevicePublic]


class DeviceManagerResponse(BaseModel):
    manager_id: LabNetIdentifier
    status: Literal["online", "offline"]
    last_seen: datetime
    devices: list[DevicePublic]


class DeviceManagerPublic(BaseModel):
    """A Lab PC as any authenticated user sees it (no ownership details)."""

    manager_id: LabNetIdentifier
    host: str
    endpoint: EndpointDescriptor
    # Online only while heartbeats arrive within the heartbeat timeout.
    status: Literal["online", "offline"]
    registered_at: datetime
    last_seen: datetime
    devices: list[DevicePublic]


class DeviceManagerListResponse(BaseModel):
    device_managers: list[DeviceManagerPublic]


def _target_host(target: str) -> str:
    value = target.strip()
    if "://" in value or "/" in value or "@" in value:
        raise ValueError("gRPC target must be host:port, without URL syntax.")
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or end + 2 > len(value) or value[end + 1] != ":":
            raise ValueError("IPv6 gRPC target must use [address]:port.")
        host, port_text = value[1:end], value[end + 2 :]
    else:
        try:
            host, port_text = value.rsplit(":", 1)
        except ValueError as exc:
            raise ValueError("gRPC target must include a port.") from exc
    if not host.strip():
        raise ValueError("gRPC target host must not be empty.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("gRPC target port must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("gRPC target port must be between 1 and 65535.")
    return host.strip()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
