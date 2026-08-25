from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models.common import ErrorCode, ServiceError
from ..models.releases import ReleaseCreateRequest, ReleaseManifest, ReleasePublic
from ..models.users import AuthenticatedUser
from ..storage.database import Database
from ..storage.orm import AuditEventRow, ReleaseArtifactRow


class ReleaseService:
    """Index and store immutable, pre-built LabNet release bundles."""

    def __init__(
        self,
        database: Database,
        release_root: Path,
        *,
        maximum_upload_bytes: int,
    ) -> None:
        self.database = database
        self.release_root = release_root.expanduser().resolve()
        self.maximum_upload_bytes = maximum_upload_bytes
        self.staging_root = self.release_root / ".staging"
        self.versions_root = self.release_root / "versions"

    def create(
        self,
        request: ReleaseCreateRequest,
        actor: AuthenticatedUser,
    ) -> ReleasePublic:
        manifest = request.manifest
        if not manifest.artifact_filename.lower().endswith(".zip"):
            raise _release_invalid("Release artifact_filename must end in .zip.")
        if not manifest.wheel_filename.lower().endswith(".whl"):
            raise _release_invalid("Release wheel_filename must end in .whl.")
        if manifest.artifact_size > self.maximum_upload_bytes:
            raise _release_invalid(
                "Release artifact exceeds the configured upload limit.",
                metadata={
                    "artifact_size": manifest.artifact_size,
                    "maximum_upload_bytes": self.maximum_upload_bytes,
                },
            )
        self._ensure_roots()
        release_id = f"rel_{uuid4().hex}"
        staging = self._staging_directory(release_id)
        staging.mkdir(parents=False, exist_ok=False)
        self._write_manifest(staging / "manifest.json", manifest)
        row = ReleaseArtifactRow(
            release_id=release_id,
            version=manifest.version,
            artifact_filename=manifest.artifact_filename,
            artifact_sha256=manifest.artifact_sha256,
            artifact_size=manifest.artifact_size,
            manifest=manifest.model_dump(mode="json"),
            state="pending_upload",
            created_by_user_id=actor.user_id,
        )
        try:
            with self.database.transaction() as session:
                session.add(row)
                session.flush()
                session.add(
                    AuditEventRow(
                        event_type="RELEASE_CREATED",
                        actor_user_id=actor.user_id,
                        details={
                            "release_id": release_id,
                            "version": manifest.version,
                        },
                    )
                )
        except IntegrityError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ServiceError(
                ErrorCode.RELEASE_CONFLICT,
                "A release with this version already exists.",
                status_code=409,
                metadata={"version": manifest.version},
            ) from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.get(release_id, include_upload_url=True)

    def prepare_upload(
        self,
        release_id: str,
        actor: AuthenticatedUser,
    ) -> tuple[Path, int]:
        row = self._get_row(release_id)
        if row.state != "pending_upload":
            raise _release_conflict("This release no longer accepts an artifact upload.")
        self._ensure_roots()
        staging = self._staging_directory(release_id)
        if not staging.is_dir():
            raise _release_invalid("The release staging directory is missing.")
        filename = f".upload-{uuid4().hex}.partial"
        target = (staging / filename).resolve()
        _require_child(target, staging)
        return target, min(self.maximum_upload_bytes, row.artifact_size)

    def complete_upload(
        self,
        release_id: str,
        temporary_path: Path,
        actor: AuthenticatedUser,
    ) -> ReleasePublic:
        temporary_path = temporary_path.resolve()
        staging = self._staging_directory(release_id)
        _require_child(temporary_path, staging)
        if not temporary_path.is_file():
            raise _release_invalid("The uploaded artifact is missing.")

        size = temporary_path.stat().st_size
        digest = _sha256_file(temporary_path)
        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as session:
            row = session.scalar(
                select(ReleaseArtifactRow)
                .where(ReleaseArtifactRow.release_id == release_id)
                .with_for_update()
            )
            if row is None:
                temporary_path.unlink(missing_ok=True)
                raise _release_not_found(release_id)
            if row.state != "pending_upload":
                temporary_path.unlink(missing_ok=True)
                raise _release_conflict("This release has already been uploaded.")
            if size != row.artifact_size or digest != row.artifact_sha256:
                temporary_path.unlink(missing_ok=True)
                raise _release_invalid(
                    "The uploaded artifact does not match its manifest.",
                    metadata={
                        "expected_size": row.artifact_size,
                        "actual_size": size,
                        "expected_sha256": row.artifact_sha256,
                        "actual_sha256": digest,
                    },
                )

            final_directory = self._version_directory(row.version)
            if final_directory.exists():
                temporary_path.unlink(missing_ok=True)
                raise _release_conflict(
                    "The immutable release version directory already exists."
                )
            artifact_path = (staging / row.artifact_filename).resolve()
            _require_child(artifact_path, staging)
            os.replace(temporary_path, artifact_path)
            os.replace(staging, final_directory)
            row.state = "candidate"
            row.uploaded_at = now
            session.add(
                AuditEventRow(
                    event_type="RELEASE_UPLOADED",
                    actor_user_id=actor.user_id,
                    details={
                        "release_id": row.release_id,
                        "version": row.version,
                        "sha256": digest,
                    },
                )
            )
        return self.get(release_id)

    def promote(self, release_id: str, actor: AuthenticatedUser) -> ReleasePublic:
        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as session:
            row = session.scalar(
                select(ReleaseArtifactRow)
                .where(ReleaseArtifactRow.release_id == release_id)
                .with_for_update()
            )
            if row is None:
                raise _release_not_found(release_id)
            if row.state == "promoted":
                return _public(row)
            if row.state != "candidate":
                raise _release_conflict(
                    "Only a completely uploaded candidate release can be promoted."
                )
            if not self.artifact_path(row).is_file():
                raise _release_invalid("The release artifact is missing from storage.")
            row.state = "promoted"
            row.promoted_at = now
            session.add(
                AuditEventRow(
                    event_type="RELEASE_PROMOTED",
                    actor_user_id=actor.user_id,
                    details={
                        "release_id": row.release_id,
                        "version": row.version,
                    },
                )
            )
            session.flush()
            result = _public(row)
        return result

    def get(
        self,
        release_id: str,
        *,
        include_upload_url: bool = False,
    ) -> ReleasePublic:
        row = self._get_row(release_id)
        result = _public(row)
        if include_upload_url and row.state == "pending_upload":
            return result.model_copy(
                update={
                    "upload_url": f"/api/v1/releases/{release_id}/artifact"
                }
            )
        return result

    def get_by_version(self, version: str) -> ReleaseArtifactRow:
        with self.database.session() as session:
            row = session.scalar(
                select(ReleaseArtifactRow).where(
                    ReleaseArtifactRow.version == version
                )
            )
            if row is None:
                raise ServiceError(
                    ErrorCode.RELEASE_NOT_FOUND,
                    "Release version not found.",
                    status_code=404,
                    metadata={"version": version},
                )
            session.expunge(row)
            return row

    def list(self) -> list[ReleasePublic]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ReleaseArtifactRow).order_by(
                    ReleaseArtifactRow.created_at.desc()
                )
            ).all()
            return [_public(row) for row in rows]

    def artifact_path(self, row: ReleaseArtifactRow) -> Path:
        directory = self._version_directory(row.version)
        path = (directory / row.artifact_filename).resolve()
        _require_child(path, directory)
        return path

    def _get_row(self, release_id: str) -> ReleaseArtifactRow:
        with self.database.session() as session:
            row = session.get(ReleaseArtifactRow, release_id)
            if row is None:
                raise _release_not_found(release_id)
            session.expunge(row)
            return row

    def _staging_directory(self, release_id: str) -> Path:
        directory = (self.staging_root / release_id).resolve()
        _require_child(directory, self.staging_root)
        return directory

    def _ensure_roots(self) -> None:
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.versions_root.mkdir(parents=True, exist_ok=True)

    def _version_directory(self, version: str) -> Path:
        directory = (self.versions_root / version).resolve()
        _require_child(directory, self.versions_root)
        return directory

    @staticmethod
    def _write_manifest(path: Path, manifest: ReleaseManifest) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                manifest.model_dump(mode="json"),
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


def _public(row: ReleaseArtifactRow) -> ReleasePublic:
    return ReleasePublic(
        release_id=row.release_id,
        version=row.version,
        state=row.state,  # type: ignore[arg-type]
        artifact_filename=row.artifact_filename,
        artifact_sha256=row.artifact_sha256,
        artifact_size=row.artifact_size,
        manifest=ReleaseManifest.model_validate(row.manifest),
        created_at=row.created_at,
        uploaded_at=row.uploaded_at,
        promoted_at=row.promoted_at,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_child(path: Path, parent: Path) -> None:
    parent = parent.resolve()
    try:
        path.resolve().relative_to(parent)
    except ValueError as exc:
        raise _release_invalid("Release path escaped its configured storage root.") from exc


def _release_not_found(release_id: str) -> ServiceError:
    return ServiceError(
        ErrorCode.RELEASE_NOT_FOUND,
        "Release not found.",
        status_code=404,
        metadata={"release_id": release_id},
    )


def _release_conflict(message: str) -> ServiceError:
    return ServiceError(ErrorCode.RELEASE_CONFLICT, message, status_code=409)


def _release_invalid(
    message: str, *, metadata: dict[str, object] | None = None
) -> ServiceError:
    return ServiceError(
        ErrorCode.RELEASE_INVALID,
        message,
        status_code=422,
        metadata=dict(metadata or {}),
    )


__all__ = ["ReleaseService"]
