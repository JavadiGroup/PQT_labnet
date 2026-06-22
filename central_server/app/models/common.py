from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, Enum):
    """Stable error codes returned by the API."""

    AUTH_FAILED = "AUTH_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    DEVICE_BUSY = "DEVICE_BUSY"

    LEASE_NOT_FOUND = "LEASE_NOT_FOUND"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_INVALID = "LEASE_INVALID"

    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    DEVICE_METHOD_ERROR = "DEVICE_METHOD_ERROR"

    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """Details describing one API error."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard error response returned by HTTP endpoints."""

    model_config = ConfigDict(extra="forbid")

    success: Literal[False] = False
    error: ErrorDetail


class MessageResponse(BaseModel):
    """Standard successful response containing a message."""

    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    message: str = Field(..., min_length=1)


class HealthResponse(BaseModel):
    """Response returned by GET /health."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    service: str = "central-server"


class OperationResponse(BaseModel):
    """Generic response for simple successful operations."""

    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    operation: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)