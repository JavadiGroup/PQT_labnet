from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..models.common import ErrorCode, ServiceError
from ..models.devices import (
    DeviceHeartbeatItem,
    DeviceManagerHeartbeatRequest,
    DeviceManagerRegistrationRequest,
    DevicePublic,
    EndpointDescriptor,
)
from ..models.users import AuthenticatedUser
from ..storage.database import Database
from ..storage.orm import (
    AuditEventRow,
    DeviceManagerRow,
    DeviceRow,
    LeaseRow,
)


class DeviceRegistryService:
    def __init__(
        self, database: Database, *, heartbeat_timeout_seconds: int = 30
    ) -> None:
        self.database = database
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

    def register(
        self,
        request: DeviceManagerRegistrationRequest,
        actor: AuthenticatedUser,
    ) -> dict:
        self._require_manager_identity(request.manager_id, actor)
        with self.database.transaction(immediate=True) as session:
            manager = session.scalar(
                select(DeviceManagerRow)
                .where(DeviceManagerRow.manager_id == request.manager_id)
                .with_for_update()
            )
            now = datetime.now(timezone.utc)
            if manager is None:
                manager = DeviceManagerRow(
                    manager_id=request.manager_id,
                    owner_user_id=actor.user_id,
                    hostname=request.host,
                    transport=request.endpoint.transport,
                    target=request.endpoint.target,
                    api_version=request.endpoint.api_version,
                    secure=request.endpoint.secure,
                    tls_server_name=request.endpoint.tls_server_name,
                    endpoint_attributes=request.endpoint.attributes,
                    status="online",
                    registered_at=now,
                    last_seen=now,
                )
                session.add(manager)
                session.flush()
            else:
                self._require_owner(manager, actor)
                _update_manager(manager, request.host, request.endpoint, now)

            reported_ids: set[str] = set()
            for item in request.devices:
                reported_ids.add(item.device_id)
                device = session.get(DeviceRow, item.device_id)
                if device is not None and device.manager_id != request.manager_id:
                    raise ServiceError(
                        ErrorCode.DEVICE_OWNERSHIP_CONFLICT,
                        "Device ID is already owned by another Device Manager.",
                        status_code=409,
                        metadata={"device_id": item.device_id},
                    )
                if device is None:
                    device = DeviceRow(
                        device_id=item.device_id,
                        manager_id=request.manager_id,
                        device_type=item.device_type,
                        runtime_state=item.runtime_state,
                        availability=item.availability,
                        admission_state=manager.admission_state,
                        status_text=item.status_text,
                        device_metadata=item.metadata,
                        first_seen=now,
                        last_seen=now,
                    )
                    session.add(device)
                else:
                    device.device_type = item.device_type
                    _update_device(device, item, now)

            for device in session.scalars(
                select(DeviceRow).where(DeviceRow.manager_id == request.manager_id)
            ):
                if device.device_id not in reported_ids:
                    device.runtime_state = "stopped"
                    device.availability = "unknown"
                    device.status_text = "Not reported during registration."

            session.add(
                AuditEventRow(
                    event_type="DEVICE_MANAGER_REGISTERED",
                    actor_user_id=actor.user_id,
                    manager_id=request.manager_id,
                    details={"device_count": len(request.devices)},
                )
            )
            session.flush()
            devices = self._list_for_manager(session, request.manager_id, now)
            result = {
                "manager_id": manager.manager_id,
                "status": manager.status,
                "last_seen": manager.last_seen,
                "devices": devices,
            }
        return result

    def heartbeat(
        self,
        request: DeviceManagerHeartbeatRequest,
        actor: AuthenticatedUser,
    ) -> dict:
        self._require_manager_identity(request.manager_id, actor)
        with self.database.transaction(immediate=True) as session:
            manager = session.scalar(
                select(DeviceManagerRow)
                .where(DeviceManagerRow.manager_id == request.manager_id)
                .with_for_update()
            )
            now = datetime.now(timezone.utc)
            if manager is None:
                raise ServiceError(
                    ErrorCode.DEVICE_MANAGER_NOT_FOUND,
                    "Device Manager must register before sending heartbeats.",
                    status_code=404,
                )
            self._require_owner(manager, actor)
            _update_manager(manager, request.host, request.endpoint, now)

            reported_ids: set[str] = set()
            for item in request.devices:
                reported_ids.add(item.device_id)
                device = session.get(DeviceRow, item.device_id)
                if device is None or device.manager_id != request.manager_id:
                    raise ServiceError(
                        ErrorCode.DEVICE_NOT_FOUND,
                        "Heartbeat referenced a device not owned by this manager.",
                        status_code=409,
                        metadata={"device_id": item.device_id},
                    )
                _update_device(device, item, now)

            for device in session.scalars(
                select(DeviceRow).where(DeviceRow.manager_id == request.manager_id)
            ):
                if device.device_id not in reported_ids:
                    device.availability = "unknown"
                    device.status_text = "Not reported in latest heartbeat."

            session.add(
                AuditEventRow(
                    event_type="DEVICE_MANAGER_HEARTBEAT",
                    actor_user_id=actor.user_id,
                    manager_id=request.manager_id,
                    details={
                        "device_count": len(request.devices),
                        "reported_timestamp": request.timestamp.isoformat(),
                    },
                )
            )
            session.flush()
            devices = self._list_for_manager(session, request.manager_id, now)
            result = {
                "manager_id": manager.manager_id,
                "status": manager.status,
                "last_seen": manager.last_seen,
                "devices": devices,
            }
        return result

    def list_devices(
        self,
        *,
        device_type: str | None = None,
        availability: str | None = None,
        runtime_state: str | None = None,
        effective_available: bool | None = None,
    ) -> list[DevicePublic]:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            statement = select(DeviceRow).options(joinedload(DeviceRow.manager))
            if device_type is not None:
                statement = statement.where(DeviceRow.device_type == device_type)
            if availability is not None:
                statement = statement.where(DeviceRow.availability == availability)
            if runtime_state is not None:
                statement = statement.where(DeviceRow.runtime_state == runtime_state)
            rows = session.scalars(statement.order_by(DeviceRow.device_id)).all()
            devices = [self._public(session, row, now) for row in rows]
            if effective_available is not None:
                devices = [
                    device
                    for device in devices
                    if device.effective_available is effective_available
                ]
            return devices

    def get_device(self, device_id: str) -> DevicePublic:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            row = session.scalar(
                select(DeviceRow)
                .options(joinedload(DeviceRow.manager))
                .where(DeviceRow.device_id == device_id)
            )
            if row is None:
                raise ServiceError(
                    ErrorCode.DEVICE_NOT_FOUND,
                    "Device not found.",
                    status_code=404,
                    metadata={"device_id": device_id},
                )
            return self._public(session, row, now)

    def _list_for_manager(self, session, manager_id: str, now: datetime) -> list[DevicePublic]:
        rows = session.scalars(
            select(DeviceRow)
            .options(joinedload(DeviceRow.manager))
            .where(DeviceRow.manager_id == manager_id)
            .order_by(DeviceRow.device_id)
        ).all()
        return [self._public(session, row, now) for row in rows]

    def _public(self, session, row: DeviceRow, now: datetime) -> DevicePublic:
        leased = session.scalar(
            select(LeaseRow.lease_id).where(
                LeaseRow.device_id == row.device_id,
                LeaseRow.state == "active",
                LeaseRow.expires_at > now,
            )
        ) is not None
        endpoint = EndpointDescriptor(
            transport=row.manager.transport,
            target=row.manager.target,
            api_version=row.manager.api_version,
            secure=row.manager.secure,
            tls_server_name=row.manager.tls_server_name,
            attributes=dict(row.manager.endpoint_attributes or {}),
        )
        effective = (
            row.manager.status == "online"
            and _as_utc(row.manager.last_seen)
            > now - timedelta(seconds=self.heartbeat_timeout_seconds)
            and row.runtime_state == "ready"
            and row.availability == "available"
            and row.manager.admission_state == "accepting"
            and row.admission_state == "accepting"
            and not leased
        )
        return DevicePublic(
            device_id=row.device_id,
            manager_id=row.manager_id,
            endpoint=endpoint,
            device_type=row.device_type,
            runtime_state=row.runtime_state,  # type: ignore[arg-type]
            availability=row.availability,  # type: ignore[arg-type]
            status_text=row.status_text,
            allocation_state="leased" if leased else "free",
            admission_state=row.admission_state,  # type: ignore[arg-type]
            effective_available=effective,
            last_seen=row.last_seen,
            metadata=dict(row.device_metadata or {}),
        )

    @staticmethod
    def _require_manager_identity(manager_id: str, actor: AuthenticatedUser) -> None:
        if actor.role != "device_manager" or manager_id != actor.username:
            raise ServiceError(
                ErrorCode.PERMISSION_DENIED,
                "A Device Manager may manage only its own manager_id.",
                status_code=403,
            )

    @staticmethod
    def _require_owner(manager: DeviceManagerRow, actor: AuthenticatedUser) -> None:
        if actor.role != "device_manager" or manager.owner_user_id != actor.user_id:
            raise ServiceError(
                ErrorCode.PERMISSION_DENIED,
                "Device Manager ownership does not match the authenticated principal.",
                status_code=403,
            )


def _update_manager(
    row: DeviceManagerRow,
    host: str,
    endpoint: EndpointDescriptor,
    now: datetime,
) -> None:
    row.hostname = host
    row.transport = endpoint.transport
    row.target = endpoint.target
    row.api_version = endpoint.api_version
    row.secure = endpoint.secure
    row.tls_server_name = endpoint.tls_server_name
    row.endpoint_attributes = endpoint.attributes
    row.status = "online"
    row.last_seen = now


def _update_device(
    row: DeviceRow,
    item: DeviceHeartbeatItem | object,
    now: datetime,
) -> None:
    row.runtime_state = item.runtime_state  # type: ignore[attr-defined]
    row.availability = item.availability  # type: ignore[attr-defined]
    row.status_text = item.status_text  # type: ignore[attr-defined]
    metadata = item.metadata  # type: ignore[attr-defined]
    if metadata is not None:
        row.device_metadata = metadata
    row.last_seen = now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
