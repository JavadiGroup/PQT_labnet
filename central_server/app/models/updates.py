from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import LabNetIdentifier
from .releases import ReleaseManifest


DeploymentState = Literal[
    "assigned",
    "downloading",
    "staged",
    "draining",
    "quiescing",
    "switching",
    "verifying",
    "succeeded",
    "failed",
    "rolled_back",
]
UpdateReportedPhase = Literal[
    "downloading",
    "staged",
    "quiescing",
    "switching",
    "verifying",
]


class DeploymentAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=64)
    allow_downgrade: bool = False

    @field_validator("version")
    @classmethod
    def normalized_version(cls, value: str) -> str:
        return _normalized_version(value)


class DeploymentPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_id: LabNetIdentifier
    desired_version: str
    observed_version: str | None
    previous_version: str | None
    state: DeploymentState
    active_attempt_id: str | None
    assigned_at: datetime
    updated_at: datetime
    last_error: str | None


class DeploymentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployments: list[DeploymentPublic]


class UpdateDirectiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_id: LabNetIdentifier
    update_available: bool
    observed_version: str | None
    desired_version: str | None
    attempt_id: str | None
    state: DeploymentState | None
    manifest: ReleaseManifest | None
    artifact_url: str | None


class UpdatePhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: UpdateReportedPhase
    details: dict[str, Any] = Field(default_factory=dict)


class DrainStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manager_id: LabNetIdentifier
    admission_state: Literal["accepting", "draining", "updating"]
    active_lease_count: int = Field(ge=0)
    ready_to_apply: bool


class UpdateCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installed_version: str = Field(min_length=1, max_length=64)

    @field_validator("installed_version")
    @classmethod
    def normalized_version(cls, value: str) -> str:
        return _normalized_version(value)


class UpdateAbortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_version: str | None = Field(default=None, max_length=64)
    error: str = Field(min_length=1, max_length=8192)
    rolled_back: bool = False
    reopen_admission: bool = False

    @field_validator("observed_version")
    @classmethod
    def normalized_version(cls, value: str | None) -> str | None:
        return _normalized_version(value) if value is not None else None


class UpdateAttemptPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    manager_id: LabNetIdentifier
    version: str
    state: DeploymentState
    from_version: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error: str | None
    details: dict[str, Any]


def _normalized_version(value: str) -> str:
    try:
        normalized = str(Version(value))
    except InvalidVersion as exc:
        raise ValueError("must be a valid PEP 440 version") from exc
    if normalized != value:
        raise ValueError(f"must use normalized PEP 440 form {normalized!r}")
    return value


__all__ = [
    "DeploymentAssignRequest",
    "DeploymentListResponse",
    "DeploymentPublic",
    "DeploymentState",
    "DrainStatusResponse",
    "UpdateAbortRequest",
    "UpdateAttemptPublic",
    "UpdateCompleteRequest",
    "UpdateDirectiveResponse",
    "UpdatePhaseRequest",
    "UpdateReportedPhase",
]
