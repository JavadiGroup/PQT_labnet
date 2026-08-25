from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from packaging.version import InvalidVersion, Version
from sqlalchemy import func, select

from ..models.common import ErrorCode, ServiceError
from ..models.updates import (
    DeploymentPublic,
    DrainStatusResponse,
    UpdateAbortRequest,
    UpdateAttemptPublic,
    UpdateCompleteRequest,
    UpdateDirectiveResponse,
    UpdatePhaseRequest,
)
from ..models.users import AuthenticatedUser
from ..storage.database import Database
from ..storage.orm import (
    AuditEventRow,
    DeploymentAssignmentRow,
    DeviceManagerRow,
    DeviceRow,
    LeaseRow,
    ReleaseArtifactRow,
    UpdateAttemptRow,
)
from .releases import ReleaseService


_TERMINAL_STATES = {"succeeded", "failed", "rolled_back"}
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"downloading", "staged", "failed"},
    "downloading": {"staged", "failed"},
    "staged": {"draining", "failed"},
    "draining": {"quiescing", "switching", "failed"},
    "quiescing": {"switching", "failed"},
    "switching": {"verifying", "failed", "rolled_back"},
    "verifying": {"succeeded", "failed", "rolled_back"},
    "succeeded": set(),
    "failed": set(),
    "rolled_back": set(),
}


class DeploymentService:
    """Coordinate per-Lab-PC desired versions and transactional draining."""

    def __init__(self, database: Database, releases: ReleaseService) -> None:
        self.database = database
        self.releases = releases

    def assign(
        self,
        *,
        manager_id: str,
        version: str,
        allow_downgrade: bool,
        actor: AuthenticatedUser,
    ) -> DeploymentPublic:
        release = self.releases.get_by_version(version)
        if release.state != "promoted":
            raise ServiceError(
                ErrorCode.RELEASE_CONFLICT,
                "Only a promoted release can be assigned.",
                status_code=409,
                metadata={"version": version},
            )
        if "lab_pc" not in set((release.manifest or {}).get("roles", [])):
            raise ServiceError(
                ErrorCode.RELEASE_CONFLICT,
                "Only a release with the lab_pc role can be assigned to a Device Manager.",
                status_code=409,
                metadata={"version": version},
            )

        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as session:
            manager = session.scalar(
                select(DeviceManagerRow)
                .where(DeviceManagerRow.manager_id == manager_id)
                .with_for_update()
            )
            if manager is None:
                raise ServiceError(
                    ErrorCode.DEVICE_MANAGER_NOT_FOUND,
                    "Device Manager not found.",
                    status_code=404,
                    metadata={"manager_id": manager_id},
                )
            assignment = session.get(DeploymentAssignmentRow, manager_id)
            observed = (
                assignment.observed_version
                if assignment is not None
                else _reported_manager_version(manager)
            )
            if (
                assignment is not None
                and assignment.active_attempt_id is not None
                and assignment.state not in _TERMINAL_STATES
            ):
                current_release = session.get(
                    ReleaseArtifactRow, assignment.desired_release_id
                )
                if current_release is not None and current_release.version == version:
                    return _deployment_public(assignment, current_release.version)
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "The Device Manager already has an active update attempt.",
                    status_code=409,
                    metadata={"state": assignment.state},
                )
            if (
                observed is not None
                and Version(version) < Version(observed)
                and not allow_downgrade
            ):
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "Assigning an older release requires allow_downgrade=true.",
                    status_code=409,
                    metadata={
                        "observed_version": observed,
                        "desired_version": version,
                    },
                )
            if observed == release.version:
                if assignment is None:
                    assignment = DeploymentAssignmentRow(
                        manager_id=manager_id,
                        desired_release_id=release.release_id,
                        observed_version=observed,
                        previous_version=observed,
                        state="succeeded",
                        active_attempt_id=None,
                        assigned_by_user_id=actor.user_id,
                        assigned_at=now,
                        updated_at=now,
                    )
                    session.add(assignment)
                else:
                    assignment.desired_release_id = release.release_id
                    assignment.state = "succeeded"
                    assignment.active_attempt_id = None
                    assignment.assigned_by_user_id = actor.user_id
                    assignment.assigned_at = now
                    assignment.updated_at = now
                    assignment.last_error = None
                session.flush()
                return _deployment_public(assignment, release.version)

            attempt_id = f"upd_{uuid4().hex}"
            if assignment is None:
                assignment = DeploymentAssignmentRow(
                    manager_id=manager_id,
                    desired_release_id=release.release_id,
                    observed_version=observed,
                    previous_version=observed,
                    state="assigned",
                    active_attempt_id=attempt_id,
                    assigned_by_user_id=actor.user_id,
                    assigned_at=now,
                    updated_at=now,
                )
                session.add(assignment)
            else:
                assignment.previous_version = assignment.observed_version
                assignment.desired_release_id = release.release_id
                assignment.state = "assigned"
                assignment.active_attempt_id = attempt_id
                assignment.assigned_by_user_id = actor.user_id
                assignment.assigned_at = now
                assignment.updated_at = now
                assignment.last_error = None
            session.add(
                UpdateAttemptRow(
                    attempt_id=attempt_id,
                    manager_id=manager_id,
                    release_id=release.release_id,
                    from_version=observed,
                    state="assigned",
                    created_at=now,
                    updated_at=now,
                    details={"allow_downgrade": allow_downgrade},
                )
            )
            session.add(
                AuditEventRow(
                    event_type="UPDATE_ASSIGNED",
                    actor_user_id=actor.user_id,
                    manager_id=manager_id,
                    details={
                        "attempt_id": attempt_id,
                        "version": version,
                        "allow_downgrade": allow_downgrade,
                    },
                )
            )
            session.flush()
            result = _deployment_public(assignment, release.version)
        return result

    def directive(self, actor: AuthenticatedUser) -> UpdateDirectiveResponse:
        manager_id = self._manager_identity(actor)
        with self.database.session() as session:
            if session.get(DeviceManagerRow, manager_id) is None:
                raise ServiceError(
                    ErrorCode.DEVICE_MANAGER_NOT_FOUND,
                    "Device Manager must register before requesting updates.",
                    status_code=404,
                )
            assignment = session.get(DeploymentAssignmentRow, manager_id)
            if assignment is None:
                return UpdateDirectiveResponse(
                    manager_id=manager_id,
                    update_available=False,
                    observed_version=None,
                    desired_version=None,
                    attempt_id=None,
                    state=None,
                    manifest=None,
                    artifact_url=None,
                )
            release = session.get(
                ReleaseArtifactRow, assignment.desired_release_id
            )
            if release is None:
                raise ServiceError(
                    ErrorCode.RELEASE_NOT_FOUND,
                    "The assigned release no longer exists.",
                    status_code=409,
                )
            available = (
                assignment.observed_version != release.version
                and assignment.state not in {"failed", "rolled_back"}
            )
            return UpdateDirectiveResponse(
                manager_id=manager_id,
                update_available=available,
                observed_version=assignment.observed_version,
                desired_version=release.version,
                attempt_id=assignment.active_attempt_id,
                state=assignment.state,  # type: ignore[arg-type]
                manifest=release.manifest if available else None,
                artifact_url=(
                    f"/api/v1/updates/artifacts/{release.release_id}"
                    if available
                    else None
                ),
            )

    def report_phase(
        self,
        attempt_id: str,
        request: UpdatePhaseRequest,
        actor: AuthenticatedUser,
    ) -> UpdateAttemptPublic:
        manager_id = self._manager_identity(actor)
        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as session:
            manager = self._locked_manager(session, manager_id)
            attempt, assignment, release = self._locked_attempt(
                session, attempt_id, manager_id
            )
            self._require_transition(attempt.state, request.state)
            if request.state in {"switching", "verifying"}:
                active = self._active_lease_count(session, manager_id)
                if active:
                    raise ServiceError(
                        ErrorCode.UPDATE_STATE_CONFLICT,
                        "The update cannot switch while active leases remain.",
                        status_code=409,
                        metadata={"active_lease_count": active},
                    )
                for device in session.scalars(
                    _locked_manager_devices_statement(manager_id)
                ):
                    device.admission_state = "updating"
                manager.admission_state = "updating"
            attempt.state = request.state
            attempt.details = {**dict(attempt.details or {}), **request.details}
            attempt.updated_at = now
            assignment.state = request.state
            assignment.updated_at = now
            session.add(
                AuditEventRow(
                    event_type="UPDATE_PHASE_CHANGED",
                    actor_user_id=actor.user_id,
                    manager_id=manager_id,
                    details={
                        "attempt_id": attempt_id,
                        "state": request.state,
                    },
                )
            )
            session.flush()
            result = _attempt_public(attempt, release.version)
        return result

    def begin_drain(
        self, attempt_id: str, actor: AuthenticatedUser
    ) -> DrainStatusResponse:
        manager_id = self._manager_identity(actor)
        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as session:
            manager = self._locked_manager(session, manager_id)
            attempt, assignment, _release = self._locked_attempt(
                session, attempt_id, manager_id
            )
            if attempt.state not in {"staged", "draining"}:
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "An update must be staged before Central can begin draining.",
                    status_code=409,
                    metadata={"state": attempt.state},
                )
            devices = session.scalars(
                _locked_manager_devices_statement(manager_id)
            ).all()
            if not devices:
                raise ServiceError(
                    ErrorCode.DEVICE_NOT_FOUND,
                    "The Device Manager has no registered devices.",
                    status_code=409,
                )
            for device in devices:
                device.admission_state = "draining"
            manager.admission_state = "draining"
            attempt.state = "draining"
            attempt.updated_at = now
            assignment.state = "draining"
            assignment.updated_at = now
            active_count = self._active_lease_count(session, manager_id)
            session.add(
                AuditEventRow(
                    event_type="UPDATE_DRAIN_STARTED",
                    actor_user_id=actor.user_id,
                    manager_id=manager_id,
                    details={
                        "attempt_id": attempt_id,
                        "active_lease_count": active_count,
                    },
                )
            )
        return DrainStatusResponse(
            attempt_id=attempt_id,
            manager_id=manager_id,
            admission_state="draining",
            active_lease_count=active_count,
            ready_to_apply=active_count == 0,
        )

    def drain_status(
        self, attempt_id: str, actor: AuthenticatedUser
    ) -> DrainStatusResponse:
        manager_id = self._manager_identity(actor)
        with self.database.session() as session:
            attempt, _assignment, _release = self._attempt(
                session, attempt_id, manager_id
            )
            if attempt.state not in {"draining", "quiescing", "switching", "verifying"}:
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "The update attempt is not draining.",
                    status_code=409,
                    metadata={"state": attempt.state},
                )
            manager = session.get(DeviceManagerRow, manager_id)
            if manager is None:
                raise ServiceError(
                    ErrorCode.DEVICE_MANAGER_NOT_FOUND,
                    "Device Manager not found.",
                    status_code=404,
                )
            admission_state = manager.admission_state
            active_count = self._active_lease_count(session, manager_id)
        return DrainStatusResponse(
            attempt_id=attempt_id,
            manager_id=manager_id,
            admission_state=admission_state,  # type: ignore[arg-type]
            active_lease_count=active_count,
            ready_to_apply=active_count == 0,
        )

    def complete(
        self,
        attempt_id: str,
        request: UpdateCompleteRequest,
        actor: AuthenticatedUser,
    ) -> UpdateAttemptPublic:
        manager_id = self._manager_identity(actor)
        now = datetime.now(timezone.utc)
        with self.database.transaction(immediate=True) as session:
            manager = self._locked_manager(session, manager_id)
            attempt, assignment, release = self._locked_attempt(
                session, attempt_id, manager_id
            )
            if request.installed_version != release.version:
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "The reported installed version does not match the assignment.",
                    status_code=409,
                    metadata={
                        "reported": request.installed_version,
                        "expected": release.version,
                    },
                )
            if attempt.state not in {"verifying", "succeeded"}:
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "The update cannot complete from its current phase.",
                    status_code=409,
                    metadata={"state": attempt.state},
                )
            reported_version = _reported_manager_version(manager)
            if reported_version != release.version:
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "Central has not observed the assigned version in a Lab-PC registration.",
                    status_code=409,
                    metadata={
                        "observed_version": reported_version,
                        "expected_version": release.version,
                    },
                )
            for device in session.scalars(
                _locked_manager_devices_statement(manager_id)
            ):
                device.admission_state = "accepting"
            manager.admission_state = "accepting"
            attempt.state = "succeeded"
            attempt.updated_at = now
            attempt.completed_at = now
            assignment.previous_version = assignment.observed_version
            assignment.observed_version = release.version
            assignment.state = "succeeded"
            assignment.updated_at = now
            assignment.last_error = None
            session.add(
                AuditEventRow(
                    event_type="UPDATE_COMPLETED",
                    actor_user_id=actor.user_id,
                    manager_id=manager_id,
                    details={
                        "attempt_id": attempt_id,
                        "version": release.version,
                    },
                )
            )
            session.flush()
            result = _attempt_public(attempt, release.version)
        return result

    def abort(
        self,
        attempt_id: str,
        request: UpdateAbortRequest,
        actor: AuthenticatedUser,
    ) -> UpdateAttemptPublic:
        if actor.role == "admin":
            with self.database.session() as lookup_session:
                lookup_attempt = lookup_session.get(UpdateAttemptRow, attempt_id)
                if lookup_attempt is None:
                    raise _update_not_found(attempt_id)
                manager_id = lookup_attempt.manager_id
        else:
            manager_id = self._manager_identity(actor)
        now = datetime.now(timezone.utc)
        terminal_state = "rolled_back" if request.rolled_back else "failed"
        with self.database.transaction(immediate=True) as session:
            manager = self._locked_manager(session, manager_id)
            attempt, assignment, release = self._locked_attempt(
                session, attempt_id, manager_id
            )
            if attempt.state == "succeeded":
                raise ServiceError(
                    ErrorCode.UPDATE_STATE_CONFLICT,
                    "A completed update cannot be aborted.",
                    status_code=409,
                )
            if request.reopen_admission:
                for device in session.scalars(
                    _locked_manager_devices_statement(manager_id)
                ):
                    device.admission_state = "accepting"
                manager.admission_state = "accepting"
            attempt.state = terminal_state
            attempt.updated_at = now
            attempt.completed_at = now
            attempt.error = request.error
            assignment.state = terminal_state
            assignment.observed_version = (
                request.observed_version or assignment.observed_version
            )
            assignment.updated_at = now
            assignment.last_error = request.error
            session.add(
                AuditEventRow(
                    event_type="UPDATE_ROLLED_BACK" if request.rolled_back else "UPDATE_FAILED",
                    actor_user_id=actor.user_id,
                    manager_id=manager_id,
                    details={
                        "attempt_id": attempt_id,
                        "version": release.version,
                        "error": request.error,
                    },
                )
            )
            session.flush()
            result = _attempt_public(attempt, release.version)
        return result

    def list_deployments(self) -> list[DeploymentPublic]:
        with self.database.session() as session:
            rows = session.execute(
                select(DeploymentAssignmentRow, ReleaseArtifactRow)
                .join(
                    ReleaseArtifactRow,
                    ReleaseArtifactRow.release_id
                    == DeploymentAssignmentRow.desired_release_id,
                )
                .order_by(DeploymentAssignmentRow.manager_id)
            ).all()
            return [
                _deployment_public(assignment, release.version)
                for assignment, release in rows
            ]

    @staticmethod
    def _manager_identity(actor: AuthenticatedUser) -> str:
        if actor.role != "device_manager":
            raise ServiceError(
                ErrorCode.PERMISSION_DENIED,
                "Only a Device Manager may operate its update assignment.",
                status_code=403,
            )
        return actor.username

    @staticmethod
    def _require_transition(current: str, requested: str) -> None:
        if current == requested:
            return
        if requested not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise ServiceError(
                ErrorCode.UPDATE_STATE_CONFLICT,
                "Invalid update phase transition.",
                status_code=409,
                metadata={"current": current, "requested": requested},
            )

    def _locked_attempt(self, session, attempt_id: str, manager_id: str):
        attempt = session.scalar(
            select(UpdateAttemptRow)
            .where(UpdateAttemptRow.attempt_id == attempt_id)
            .with_for_update()
        )
        if attempt is None or attempt.manager_id != manager_id:
            raise _update_not_found(attempt_id)
        assignment = session.scalar(
            select(DeploymentAssignmentRow)
            .where(DeploymentAssignmentRow.manager_id == manager_id)
            .with_for_update()
        )
        release = session.get(ReleaseArtifactRow, attempt.release_id)
        if assignment is None or release is None:
            raise _update_not_found(attempt_id)
        if assignment.active_attempt_id != attempt_id:
            raise ServiceError(
                ErrorCode.UPDATE_STATE_CONFLICT,
                "This is no longer the active update attempt.",
                status_code=409,
            )
        return attempt, assignment, release

    @staticmethod
    def _locked_manager(session, manager_id: str) -> DeviceManagerRow:
        manager = session.scalar(
            select(DeviceManagerRow)
            .where(DeviceManagerRow.manager_id == manager_id)
            .with_for_update()
        )
        if manager is None:
            raise ServiceError(
                ErrorCode.DEVICE_MANAGER_NOT_FOUND,
                "Device Manager not found.",
                status_code=404,
                metadata={"manager_id": manager_id},
            )
        return manager

    def _attempt(self, session, attempt_id: str, manager_id: str):
        attempt = session.get(UpdateAttemptRow, attempt_id)
        if attempt is None or attempt.manager_id != manager_id:
            raise _update_not_found(attempt_id)
        assignment = session.get(DeploymentAssignmentRow, manager_id)
        release = session.get(ReleaseArtifactRow, attempt.release_id)
        if assignment is None or release is None:
            raise _update_not_found(attempt_id)
        return attempt, assignment, release

    @staticmethod
    def _active_lease_count(session, manager_id: str) -> int:
        return int(
            session.scalar(
                select(func.count(LeaseRow.lease_id))
                .join(DeviceRow, DeviceRow.device_id == LeaseRow.device_id)
                .where(
                    DeviceRow.manager_id == manager_id,
                    LeaseRow.state == "active",
                )
            )
            or 0
        )


def _locked_manager_devices_statement(manager_id: str):
    return (
        select(DeviceRow)
        .where(DeviceRow.manager_id == manager_id)
        .order_by(DeviceRow.device_id)
        .with_for_update(of=DeviceRow)
    )


def _deployment_public(
    row: DeploymentAssignmentRow, desired_version: str
) -> DeploymentPublic:
    return DeploymentPublic(
        manager_id=row.manager_id,
        desired_version=desired_version,
        observed_version=row.observed_version,
        previous_version=row.previous_version,
        state=row.state,  # type: ignore[arg-type]
        active_attempt_id=row.active_attempt_id,
        assigned_at=row.assigned_at,
        updated_at=row.updated_at,
        last_error=row.last_error,
    )


def _attempt_public(row: UpdateAttemptRow, version: str) -> UpdateAttemptPublic:
    return UpdateAttemptPublic(
        attempt_id=row.attempt_id,
        manager_id=row.manager_id,
        version=version,
        state=row.state,  # type: ignore[arg-type]
        from_version=row.from_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        error=row.error,
        details=dict(row.details or {}),
    )


def _update_not_found(attempt_id: str) -> ServiceError:
    return ServiceError(
        ErrorCode.UPDATE_NOT_FOUND,
        "Update attempt not found.",
        status_code=404,
        metadata={"attempt_id": attempt_id},
    )


def _reported_manager_version(manager: DeviceManagerRow) -> str | None:
    attributes = dict(manager.endpoint_attributes or {})
    version = attributes.get("package_version")
    if not isinstance(version, str) or not version:
        return None
    try:
        return version if str(Version(version)) == version else None
    except InvalidVersion:
        return None


__all__ = ["DeploymentService", "_locked_manager_devices_statement"]
