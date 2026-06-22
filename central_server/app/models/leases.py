from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


LeaseStatus = Literal["active"]


class LeaseRecord(BaseModel):
    """Complete active lease stored in leases.json."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(..., min_length=1)
    device_id: str = Field(..., min_length=1)
    user: str = Field(..., min_length=1)
    created_at: datetime
    expires_at: datetime
    status: LeaseStatus = "active"

    @model_validator(mode="after")
    def validate_expiration(self) -> "LeaseRecord":
        """Require the expiration time to follow the creation time."""
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")

        return self


class LeaseRequest(BaseModel):
    """Request body for acquiring a device lease."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1)
    duration_seconds: int = Field(..., gt=0)


class LeaseDeviceInfo(BaseModel):
    """Device connection information returned with a new lease."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    host: str
    rpc_url: str


class LeaseRequestResponse(BaseModel):
    """Response returned after successfully creating a lease."""

    success: bool = True
    lease: LeaseRecord
    device: LeaseDeviceInfo


class LeaseReleaseRequest(BaseModel):
    """Request body for releasing a lease."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(..., min_length=1)


class LeaseReleaseResponse(BaseModel):
    """Response returned after successfully releasing a lease."""

    success: bool = True
    released: bool = True


class LeaseValidationRequest(BaseModel):
    """Request body used by a Device Manager to validate a lease."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1)


class LeaseValidationResponse(BaseModel):
    """Result of validating a lease for a particular device."""

    valid: bool
    lease_id: str
    device_id: str
    user: str
    expires_at: datetime


class LeaseListResponse(BaseModel):
    """Admin response containing all active leases."""

    leases: list[LeaseRecord]