from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePath
from typing import Literal

from packaging.version import InvalidVersion, Version
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ReleaseState = Literal["pending_upload", "candidate", "promoted", "withdrawn"]
ReleaseRole = Literal["lab_pc", "client", "central"]


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package: Literal["labnet"] = "labnet"
    version: str = Field(min_length=1, max_length=64)
    artifact_filename: str = Field(min_length=1, max_length=255)
    artifact_sha256: str
    artifact_size: int = Field(gt=0)
    wheel_filename: str = Field(min_length=1, max_length=255)
    wheel_sha256: str
    files: dict[str, str] = Field(default_factory=dict)
    git_commit: str = Field(min_length=1, max_length=128)
    roles: list[ReleaseRole] = Field(min_length=1)
    python_version: str = Field(min_length=1, max_length=32)
    platform_tag: str = Field(min_length=1, max_length=128)
    contract_digest: str | None = None
    minimum_updater_version: str = Field(default="1.0.0", max_length=64)
    created_at: AwareDatetime
    signature: str | None = None

    @field_validator("version", "minimum_updater_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        try:
            normalized = str(Version(value))
        except InvalidVersion as exc:
            raise ValueError("must be a valid PEP 440 version") from exc
        if normalized != value:
            raise ValueError(f"must use normalized PEP 440 form {normalized!r}")
        return value

    @field_validator("artifact_filename", "wheel_filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if PurePath(value).name != value or value in {".", ".."}:
            raise ValueError("must be a plain filename without path components")
        return value

    @field_validator("artifact_sha256", "wheel_sha256", "contract_digest")
    @classmethod
    def valid_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("files")
    @classmethod
    def valid_file_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            if not name or PurePath(name).is_absolute() or ".." in PurePath(name).parts:
                raise ValueError("manifest file paths must stay within the bundle")
            if _SHA256.fullmatch(digest) is None:
                raise ValueError(f"invalid SHA-256 for manifest file {name!r}")
        return value

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, value: list[ReleaseRole]) -> list[ReleaseRole]:
        if len(value) != len(set(value)):
            raise ValueError("release roles must be unique")
        return value


class ReleaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: ReleaseManifest


class ReleasePublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: str
    version: str
    state: ReleaseState
    artifact_filename: str
    artifact_sha256: str
    artifact_size: int
    manifest: ReleaseManifest
    created_at: datetime
    uploaded_at: datetime | None
    promoted_at: datetime | None
    upload_url: str | None = None


class ReleaseListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    releases: list[ReleasePublic]


__all__ = [
    "ReleaseCreateRequest",
    "ReleaseListResponse",
    "ReleaseManifest",
    "ReleasePublic",
    "ReleaseRole",
    "ReleaseState",
]
