from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


DeviceStatus = Literal["disconnected", "idle", "in_use"]


class DeviceMetadata(BaseModel):
    """Optional descriptive information supplied by a Device Manager."""

    model_config = ConfigDict(extra="allow")

    manufacturer: str | None = None
    model: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class DeviceRecord(BaseModel):
    """Complete device record stored in devices.json."""

    device_id: str = Field(..., min_length=1)
    device_type: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    rpc_url: str
    status: DeviceStatus
    last_seen: datetime
    metadata: DeviceMetadata = Field(default_factory=DeviceMetadata)

    @field_validator("rpc_url")
    @classmethod
    def validate_rpc_url(cls, value: str) -> str:
        """Require a WebSocket RPC URL."""
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("rpc_url must begin with ws:// or wss://")

        return value


class DeviceRegistrationItem(BaseModel):
    """One device reported during Device Manager registration."""

    device_id: str = Field(..., min_length=1)
    device_type: str = Field(..., min_length=1)
    status: DeviceStatus = "idle"
    metadata: DeviceMetadata = Field(default_factory=DeviceMetadata)


class DeviceRegistrationRequest(BaseModel):
    """Request body sent when one Device Manager registers its devices."""

    host: str = Field(..., min_length=1)
    rpc_url: str
    devices: list[DeviceRegistrationItem] = Field(min_length=1)

    @field_validator("rpc_url")
    @classmethod
    def validate_rpc_url(cls, value: str) -> str:
        """Require a WebSocket RPC URL."""
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("rpc_url must begin with ws:// or wss://")

        return value


class DeviceStatusUpdateRequest(BaseModel):
    """Request body for updating one device's reported status."""

    device_id: str = Field(..., min_length=1)
    status: DeviceStatus


class DeviceHeartbeatItem(BaseModel):
    """Status information for one device in a heartbeat."""

    device_id: str = Field(..., min_length=1)
    status: DeviceStatus
    metadata: DeviceMetadata | None = None


class DeviceHeartbeatRequest(BaseModel):
    """Heartbeat sent periodically by one Device Manager."""

    host: str = Field(..., min_length=1)
    rpc_url: str
    timestamp: datetime
    devices: list[DeviceHeartbeatItem]

    @field_validator("rpc_url")
    @classmethod
    def validate_rpc_url(cls, value: str) -> str:
        """Require a WebSocket RPC URL."""
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("rpc_url must begin with ws:// or wss://")

        return value


class DeviceListResponse(BaseModel):
    """Response containing all devices visible to a user."""

    devices: list[DeviceRecord]


class DeviceStatusResponse(BaseModel):
    """Response returned after a device status update."""

    device_id: str
    status: DeviceStatus
    last_seen: datetime