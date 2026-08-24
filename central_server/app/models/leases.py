from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .common import LabNetIdentifier
from .devices import EndpointDescriptor


LeaseState = Literal["active", "released", "expired", "revoked"]


class LeaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: LabNetIdentifier
    duration_seconds: int


class LeaseRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int


class LeaseValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: LabNetIdentifier


class LeasePublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: LabNetIdentifier
    device_id: LabNetIdentifier
    user: str
    created_at: datetime
    expires_at: datetime
    state: LeaseState

    @model_validator(mode="after")
    def valid_times(self) -> "LeasePublic":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        return self


class LeaseDeviceConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: LabNetIdentifier
    manager_id: LabNetIdentifier
    endpoint: EndpointDescriptor


class LeaseGrantResponse(BaseModel):
    lease: LeasePublic
    device: LeaseDeviceConnection


class LeaseReleaseResponse(BaseModel):
    lease_id: LabNetIdentifier
    state: LeaseState
    released: bool


class LeaseValidationResponse(BaseModel):
    valid: Literal[True] = True
    lease_id: LabNetIdentifier
    device_id: LabNetIdentifier
    user: str
    expires_at: datetime


class LeaseListResponse(BaseModel):
    leases: list[LeasePublic]
