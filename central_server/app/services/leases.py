from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from labnet.lease_policy import DEFAULT_IDLE_TIMEOUT_SECONDS

from ..models.common import ErrorCode, ServiceError
from ..models.devices import EndpointDescriptor
from ..models.leases import (
    LeaseDeviceConnection,
    LeaseGrantResponse,
    LeasePublic,
    LeaseReleaseResponse,
    LeaseValidationResponse,
)
from ..models.users import AuthenticatedUser
from ..storage.database import Database
from ..storage.orm import (
    AuditEventRow,
    DeviceManagerRow,
    DeviceRow,
    LeaseRequestRow,
    LeaseRow,
)


class LeaseService:
    def __init__(
        self,
        database: Database,
        *,
        min_duration_seconds: int,
        max_duration_seconds: int,
        default_idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
        heartbeat_timeout_seconds: int = 30,
    ) -> None:
        self.database = database
        self.min_duration_seconds = min_duration_seconds
        self.max_duration_seconds = max_duration_seconds
        self.default_idle_timeout_seconds = default_idle_timeout_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

    def request_lease(
        self,
        *,
        actor: AuthenticatedUser,
        device_id: str,
        duration_seconds: int,
        idempotency_key: str,
    ) -> LeaseGrantResponse:
        _require_client_role(actor)
        self._validate_duration(duration_seconds)
        key = self._validate_idempotency_key(idempotency_key)
        request_hash = _request_hash(device_id, duration_seconds)
        try:
            return self._create_transaction(
                actor=actor,
                device_id=device_id,
                duration_seconds=duration_seconds,
                idle_timeout_seconds=self.default_idle_timeout_seconds,
                idempotency_key=key,
                request_hash=request_hash,
            )
        except IntegrityError as exc:
            # A concurrent request may have won either the idempotency-key or
            # active-device uniqueness constraint. Resolve the committed state.
            replay = self._find_idempotent_replay(
                actor=actor,
                idempotency_key=key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ServiceError(
                ErrorCode.DEVICE_BUSY,
                "Device already has an active lease.",
                status_code=409,
                metadata={"device_id": device_id},
            ) from exc

    def _create_transaction(
        self,
        *,
        actor: AuthenticatedUser,
        device_id: str,
        duration_seconds: int,
        idle_timeout_seconds: int | None,
        idempotency_key: str,
        request_hash: str,
    ) -> LeaseGrantResponse:
        with self.database.transaction(immediate=True) as session:
            prior = session.scalar(
                select(LeaseRequestRow).where(
                    LeaseRequestRow.user_id == actor.user_id,
                    LeaseRequestRow.idempotency_key == idempotency_key,
                )
            )
            if prior is not None:
                if prior.request_hash != request_hash:
                    raise _idempotency_conflict()
                lease = _load_lease(session, prior.lease_id)
                return _replay_grant(lease)

            device = session.scalar(_locked_device_statement(device_id))
            if device is None:
                raise ServiceError(
                    ErrorCode.DEVICE_NOT_FOUND,
                    "Device not found.",
                    status_code=404,
                    metadata={"device_id": device_id},
                )

            # A PostgreSQL contender may have checked the key before blocking
            # on this device row. Recheck after the row lock is acquired.
            prior = session.scalar(
                select(LeaseRequestRow).where(
                    LeaseRequestRow.user_id == actor.user_id,
                    LeaseRequestRow.idempotency_key == idempotency_key,
                )
            )
            if prior is not None:
                if prior.request_hash != request_hash:
                    raise _idempotency_conflict()
                return _replay_grant(_load_lease(session, prior.lease_id))

            active = session.scalar(
                select(LeaseRow)
                .where(
                    LeaseRow.device_id == device_id,
                    LeaseRow.state == "active",
                )
                .with_for_update()
            )
            now = datetime.now(timezone.utc)
            expiration_reason = (
                _expiration_reason(active, now) if active is not None else None
            )
            if active is not None and expiration_reason is not None:
                _terminalize(active, "expired", now, expiration_reason)
                session.add(_lease_event("LEASE_EXPIRED", active))
                session.flush()
                active = None
            if active is not None:
                raise ServiceError(
                    ErrorCode.DEVICE_BUSY,
                    "Device already has an active lease.",
                    status_code=409,
                    metadata={"device_id": device_id},
                )
            if not _device_ready(
                device,
                now=now,
                heartbeat_timeout_seconds=self.heartbeat_timeout_seconds,
            ):
                raise ServiceError(
                    ErrorCode.DEVICE_UNAVAILABLE,
                    "Device is not currently available.",
                    status_code=409,
                    metadata={"device_id": device_id},
                )

            lease = LeaseRow(
                lease_id=f"lease_{uuid4().hex}",
                device_id=device_id,
                user_id=actor.user_id,
                state="active",
                active_slot=True,
                created_at=now,
                expires_at=now + timedelta(seconds=duration_seconds),
                last_renewed_at=now,
                last_activity_at=now,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            session.add(lease)
            session.flush()
            session.add(
                LeaseRequestRow(
                    user_id=actor.user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    lease_id=lease.lease_id,
                )
            )
            session.add(
                AuditEventRow(
                    event_type="LEASE_CREATED",
                    actor_user_id=actor.user_id,
                    manager_id=device.manager_id,
                    device_id=device_id,
                    lease_id=lease.lease_id,
                    details={
                        "expires_at": lease.expires_at.isoformat(),
                        "idle_timeout_seconds": lease.idle_timeout_seconds,
                    },
                )
            )
            # Load the relationships needed to build the public response.
            session.expire(lease)
            lease = _load_lease(session, lease.lease_id)
            return _grant(lease)

    def _find_idempotent_replay(
        self,
        *,
        actor: AuthenticatedUser,
        idempotency_key: str,
        request_hash: str,
    ) -> LeaseGrantResponse | None:
        with self.database.session() as session:
            prior = session.scalar(
                select(LeaseRequestRow).where(
                    LeaseRequestRow.user_id == actor.user_id,
                    LeaseRequestRow.idempotency_key == idempotency_key,
                )
            )
            if prior is None:
                return None
            if prior.request_hash != request_hash:
                raise _idempotency_conflict()
            return _replay_grant(_load_lease(session, prior.lease_id))

    def renew(
        self,
        *,
        actor: AuthenticatedUser,
        lease_id: str,
        duration_seconds: int,
    ) -> LeasePublic:
        _require_client_role(actor)
        self._validate_duration(duration_seconds)
        expired = False
        result: LeasePublic | None = None
        with self.database.transaction(immediate=True) as session:
            lease = _load_lease(session, lease_id, for_update=True, required=False)
            if lease is None:
                raise _lease_not_found()
            now = datetime.now(timezone.utc)
            _require_lease_owner(lease, actor)
            if lease.state != "active":
                raise ServiceError(
                    ErrorCode.LEASE_INVALID,
                    "Only an active lease can be renewed.",
                    status_code=409,
                )
            expiration_reason = _expiration_reason(lease, now)
            if expiration_reason is not None:
                _terminalize(lease, "expired", now, expiration_reason)
                session.add(_lease_event("LEASE_EXPIRED", lease))
                expired = True
            else:
                lease.expires_at = now + timedelta(seconds=duration_seconds)
                lease.last_renewed_at = now
                session.add(_lease_event("LEASE_RENEWED", lease))
                result = _lease_public(lease)
        if expired:
            raise ServiceError(
                ErrorCode.LEASE_EXPIRED, "Lease has expired.", status_code=409
            )
        assert result is not None
        return result

    def release(
        self, *, actor: AuthenticatedUser, lease_id: str
    ) -> LeaseReleaseResponse:
        _require_client_role(actor)
        with self.database.transaction(immediate=True) as session:
            lease = _load_lease(session, lease_id, for_update=True, required=False)
            if lease is None:
                raise _lease_not_found()
            now = datetime.now(timezone.utc)
            _require_lease_owner(lease, actor)
            released = False
            expiration_reason = (
                _expiration_reason(lease, now) if lease.state == "active" else None
            )
            if lease.state == "active" and expiration_reason is not None:
                _terminalize(lease, "expired", now, expiration_reason)
                session.add(_lease_event("LEASE_EXPIRED", lease))
            elif lease.state == "active":
                _terminalize(lease, "released", now, "released_by_owner")
                session.add(_lease_event("LEASE_RELEASED", lease, actor.user_id))
                released = True
            return LeaseReleaseResponse(
                lease_id=lease.lease_id,
                state=lease.state,  # type: ignore[arg-type]
                released=released,
            )

    def validate(
        self,
        *,
        actor: AuthenticatedUser,
        lease_id: str,
        device_id: str,
    ) -> LeaseValidationResponse:
        invalid = False
        response: LeaseValidationResponse | None = None
        with self.database.transaction(immediate=True) as session:
            lease = _load_lease(session, lease_id, for_update=True, required=False)
            if lease is None:
                invalid = True
            else:
                now = datetime.now(timezone.utc)
                manager = lease.device.manager
                if actor.role != "device_manager" or manager.owner_user_id != actor.user_id:
                    invalid = True
                elif lease.device_id != device_id or lease.state != "active":
                    invalid = True
                else:
                    expiration_reason = _expiration_reason(lease, now)
                    if expiration_reason is not None:
                        _terminalize(lease, "expired", now, expiration_reason)
                        session.add(_lease_event("LEASE_EXPIRED", lease))
                        invalid = True
                    else:
                        # Successful validation means a real Lab-PC operation
                        # is using the lease. Renewal deliberately does not
                        # touch this independent command-activity clock.
                        lease.last_activity_at = now
                        authorization_deadline = _effective_authorization_deadline(
                            lease
                        )
                        response = LeaseValidationResponse(
                            valid=True,
                            lease_id=lease.lease_id,
                            device_id=lease.device_id,
                            user=lease.user.username,
                            expires_at=authorization_deadline,
                        )
        if invalid:
            raise ServiceError(
                ErrorCode.LEASE_INVALID,
                "Lease is not valid for this device.",
                status_code=403,
            )
        assert response is not None
        return response

    def list_active(self) -> list[LeasePublic]:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            leases = session.scalars(
                select(LeaseRow)
                .options(joinedload(LeaseRow.user))
                .where(LeaseRow.state == "active", LeaseRow.expires_at > now)
                .order_by(LeaseRow.created_at)
            ).all()
            return [
                _lease_public(lease)
                for lease in leases
                if _expiration_reason(lease, now) is None
            ]

    def maintain(self, *, heartbeat_timeout_seconds: int) -> dict[str, int]:
        expired_count = 0
        revoked_count = 0
        offline_count = 0
        now = datetime.now(timezone.utc)

        # Commit the lease-only sweep before acquiring manager/device locks in
        # the offline sweep. Acquisition uses Device -> Lease; holding Lease
        # while later requesting Device would invert that order on PostgreSQL.
        with self.database.transaction(immediate=True) as session:
            candidates = session.execute(
                select(
                    LeaseRow.lease_id,
                    LeaseRow.expires_at,
                    LeaseRow.last_activity_at,
                    LeaseRow.idle_timeout_seconds,
                ).where(LeaseRow.state == "active")
            ).all()
            candidate_ids = [
                lease_id
                for lease_id, expires_at, last_activity_at, idle_timeout in candidates
                if _timestamps_expired(
                    expires_at=expires_at,
                    last_activity_at=last_activity_at,
                    idle_timeout_seconds=idle_timeout,
                    now=now,
                )
            ]
            expiring = (
                session.scalars(_locked_expiring_leases_statement(candidate_ids)).all()
                if candidate_ids
                else []
            )
            for lease in expiring:
                expiration_reason = _expiration_reason(lease, now)
                if expiration_reason is not None:
                    _terminalize(lease, "expired", now, expiration_reason)
                    session.add(_lease_event("LEASE_EXPIRED", lease))
                    expired_count += 1

        # Separate transaction: Manager -> Device -> Lease. It never holds a
        # lease lock while waiting to acquire a device lock.
        with self.database.transaction(immediate=True) as session:
            cutoff = now - timedelta(seconds=heartbeat_timeout_seconds)
            stale = session.scalars(
                select(DeviceManagerRow).where(
                    DeviceManagerRow.status == "online",
                    DeviceManagerRow.last_seen < cutoff,
                ).with_for_update(of=DeviceManagerRow)
            ).all()
            for manager in stale:
                manager.status = "offline"
                offline_count += 1
                # Lock every managed device before taking the active-lease
                # snapshot. A concurrent acquisition either commits first and
                # is included below, or blocks and rechecks manager readiness
                # after this offline transition commits.
                devices = session.scalars(
                    _locked_manager_devices_statement(manager.manager_id)
                ).all()
                device_ids = [device.device_id for device in devices]
                for device in devices:
                    device.availability = "unknown"
                    device.status_text = "Device Manager heartbeat timed out."
                if device_ids:
                    leases = session.scalars(
                        _locked_active_device_leases_statement(device_ids)
                    ).all()
                    for lease in leases:
                        _terminalize(
                            lease, "revoked", now, "device_manager_heartbeat_timeout"
                        )
                        session.add(_lease_event("LEASE_REVOKED", lease))
                        revoked_count += 1
                session.add(
                    AuditEventRow(
                        event_type="DEVICE_MANAGER_OFFLINE",
                        manager_id=manager.manager_id,
                        details={"reason": "heartbeat_timeout"},
                    )
                )
        return {
            "expired": expired_count,
            "revoked": revoked_count,
            "offline": offline_count,
        }

    def _validate_duration(self, duration_seconds: int) -> None:
        if not self.min_duration_seconds <= duration_seconds <= self.max_duration_seconds:
            raise ServiceError(
                ErrorCode.VALIDATION_ERROR,
                "Lease duration is outside the configured range.",
                status_code=422,
                metadata={
                    "minimum": self.min_duration_seconds,
                    "maximum": self.max_duration_seconds,
                },
            )

    @staticmethod
    def _validate_idempotency_key(value: str) -> str:
        value = value.strip()
        if not value or len(value) > 255:
            raise ServiceError(
                ErrorCode.VALIDATION_ERROR,
                "Idempotency-Key must contain 1 to 255 characters.",
                status_code=422,
            )
        return value


def _load_lease(session, lease_id: str, *, for_update=False, required=True):
    statement = (
        select(LeaseRow)
        .options(
            joinedload(LeaseRow.user),
            joinedload(LeaseRow.device).joinedload(DeviceRow.manager),
        )
        .where(LeaseRow.lease_id == lease_id)
    )
    if for_update:
        statement = statement.with_for_update(of=LeaseRow)
    lease = session.scalar(statement)
    if lease is None and required:
        raise _lease_not_found()
    return lease


def _locked_device_statement(device_id: str):
    """Lock only devices, not the nullable side of the eager manager join."""
    return (
        select(DeviceRow)
        .options(joinedload(DeviceRow.manager))
        .where(DeviceRow.device_id == device_id)
        .with_for_update(of=DeviceRow)
    )


def _locked_expiring_leases_statement(lease_ids: list[str]):
    """Lock only already-identified expiry candidates, never all live leases."""
    return (
        select(LeaseRow)
        .where(
            LeaseRow.state == "active",
            LeaseRow.lease_id.in_(lease_ids),
        )
        .with_for_update(of=LeaseRow)
    )


def _locked_manager_devices_statement(manager_id: str):
    return (
        select(DeviceRow)
        .where(DeviceRow.manager_id == manager_id)
        .with_for_update(of=DeviceRow)
    )


def _locked_active_device_leases_statement(device_ids: list[str]):
    return (
        select(LeaseRow)
        .where(
            LeaseRow.state == "active",
            LeaseRow.device_id.in_(device_ids),
        )
        .with_for_update(of=LeaseRow)
    )


def _grant(lease: LeaseRow) -> LeaseGrantResponse:
    manager = lease.device.manager
    return LeaseGrantResponse(
        lease=_lease_public(lease),
        device=LeaseDeviceConnection(
            device_id=lease.device_id,
            manager_id=lease.device.manager_id,
            device_type=lease.device.device_type,
            endpoint=EndpointDescriptor(
                transport=manager.transport,
                target=manager.target,
                api_version=manager.api_version,
                secure=manager.secure,
                tls_server_name=manager.tls_server_name,
                attributes=dict(manager.endpoint_attributes or {}),
            ),
        ),
    )


def _replay_grant(lease: LeaseRow) -> LeaseGrantResponse:
    if (
        lease.state != "active"
        or _expiration_reason(lease, datetime.now(timezone.utc)) is not None
    ):
        raise ServiceError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "The original idempotent lease request is no longer active.",
            status_code=409,
        )
    return _grant(lease)


def _lease_public(lease: LeaseRow) -> LeasePublic:
    return LeasePublic(
        lease_id=lease.lease_id,
        device_id=lease.device_id,
        user=lease.user.username,
        created_at=lease.created_at,
        expires_at=lease.expires_at,
        last_renewed_at=lease.last_renewed_at,
        last_activity_at=lease.last_activity_at,
        idle_timeout_seconds=lease.idle_timeout_seconds,
        state=lease.state,  # type: ignore[arg-type]
    )


def _terminalize(lease: LeaseRow, state: str, now: datetime, reason: str) -> None:
    lease.state = state
    lease.active_slot = None
    lease.terminal_reason = reason
    if state == "released":
        lease.released_at = now
    elif state == "revoked":
        lease.revoked_at = now


def _lease_event(
    event_type: str, lease: LeaseRow, actor_user_id: str | None = None
) -> AuditEventRow:
    details = {}
    if lease.terminal_reason is not None:
        details["reason"] = lease.terminal_reason
    return AuditEventRow(
        event_type=event_type,
        actor_user_id=actor_user_id or lease.user_id,
        device_id=lease.device_id,
        lease_id=lease.lease_id,
        details=details,
    )


def _device_ready(
    device: DeviceRow, *, now: datetime, heartbeat_timeout_seconds: int
) -> bool:
    return (
        device.manager.status == "online"
        and _as_utc(device.manager.last_seen)
        > now - timedelta(seconds=heartbeat_timeout_seconds)
        and device.runtime_state == "ready"
        and device.availability == "available"
        and device.manager.admission_state == "accepting"
        and device.admission_state == "accepting"
    )


def _require_lease_owner(lease: LeaseRow, actor: AuthenticatedUser) -> None:
    if actor.role != "admin" and lease.user_id != actor.user_id:
        raise ServiceError(
            ErrorCode.PERMISSION_DENIED,
            "Lease is owned by another user.",
            status_code=403,
        )


def _require_client_role(actor: AuthenticatedUser) -> None:
    if actor.role not in {"user", "admin"}:
        raise ServiceError(
            ErrorCode.PERMISSION_DENIED,
            "Only a user or administrator may mutate client leases.",
            status_code=403,
        )


def _request_hash(device_id: str, duration_seconds: int) -> str:
    canonical = json.dumps(
        {"device_id": device_id, "duration_seconds": duration_seconds},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expiration_reason(lease: LeaseRow, now: datetime) -> str | None:
    """Return the first terminal expiration policy currently satisfied."""
    if _as_utc(lease.expires_at) <= now:
        return "lease_duration_elapsed"
    if lease.idle_timeout_seconds is not None:
        idle_deadline = _as_utc(lease.last_activity_at) + timedelta(
            seconds=lease.idle_timeout_seconds
        )
        if idle_deadline <= now:
            return "command_idle_timeout_elapsed"
    return None


def _timestamps_expired(
    *,
    expires_at: datetime,
    last_activity_at: datetime,
    idle_timeout_seconds: int | None,
    now: datetime,
) -> bool:
    if _as_utc(expires_at) <= now:
        return True
    return (
        idle_timeout_seconds is not None
        and _as_utc(last_activity_at)
        + timedelta(seconds=idle_timeout_seconds)
        <= now
    )


def _effective_authorization_deadline(lease: LeaseRow) -> datetime:
    deadline = _as_utc(lease.expires_at)
    if lease.idle_timeout_seconds is None:
        return deadline
    idle_deadline = _as_utc(lease.last_activity_at) + timedelta(
        seconds=lease.idle_timeout_seconds
    )
    return min(deadline, idle_deadline)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lease_not_found() -> ServiceError:
    return ServiceError(
        ErrorCode.LEASE_NOT_FOUND, "Lease not found.", status_code=404
    )


def _idempotency_conflict() -> ServiceError:
    return ServiceError(
        ErrorCode.IDEMPOTENCY_CONFLICT,
        "Idempotency-Key was already used for a different request.",
        status_code=409,
    )
