from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from labnet.identifiers import LABNET_IDENTIFIER_PATTERN


LabNetIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=rf"^(?:{LABNET_IDENTIFIER_PATTERN})$",
    ),
]


class ErrorCode(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    DEVICE_MANAGER_NOT_FOUND = "DEVICE_MANAGER_NOT_FOUND"
    DEVICE_OWNERSHIP_CONFLICT = "DEVICE_OWNERSHIP_CONFLICT"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    DEVICE_BUSY = "DEVICE_BUSY"
    LEASE_NOT_FOUND = "LEASE_NOT_FOUND"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_INVALID = "LEASE_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    RELEASE_NOT_FOUND = "RELEASE_NOT_FOUND"
    RELEASE_CONFLICT = "RELEASE_CONFLICT"
    RELEASE_INVALID = "RELEASE_INVALID"
    UPDATE_NOT_FOUND = "UPDATE_NOT_FOUND"
    UPDATE_STATE_CONFLICT = "UPDATE_STATE_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[False] = False
    error: ErrorDetail


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "not_ready"]
    service: Literal["central-server"] = "central-server"


class ServiceError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.metadata = metadata or {}
