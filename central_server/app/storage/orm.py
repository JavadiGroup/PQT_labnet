from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC and always restore timezone-aware values (including SQLite)."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetime values must be timezone-aware.")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    api_keys: Mapped[list["ApiKeyRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="user"
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin','user','device_manager')", name="ck_users_role"
        ),
    )


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    api_key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    user: Mapped[UserRow] = relationship(back_populates="api_keys")


class DeviceManagerRow(Base):
    __tablename__ = "device_managers"

    manager_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    api_version: Mapped[str] = mapped_column(String(128), nullable=False)
    secure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tls_server_name: Mapped[str | None] = mapped_column(String(255))
    endpoint_attributes: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    registered_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    last_seen: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, index=True
    )

    devices: Mapped[list["DeviceRow"]] = relationship(back_populates="manager")

    __table_args__ = (
        CheckConstraint("transport = 'grpc'", name="ck_managers_grpc_transport"),
        CheckConstraint(
            "status IN ('online','offline')", name="ck_managers_status"
        ),
    )


class DeviceRow(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    manager_id: Mapped[str] = mapped_column(
        ForeignKey("device_managers.manager_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    device_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    runtime_state: Mapped[str] = mapped_column(String(32), nullable=False)
    availability: Mapped[str] = mapped_column(String(32), nullable=False)
    status_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    device_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    first_seen: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    last_seen: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, index=True
    )

    manager: Mapped[DeviceManagerRow] = relationship(back_populates="devices")
    leases: Mapped[list["LeaseRow"]] = relationship(back_populates="device")

    __table_args__ = (
        CheckConstraint(
            "availability IN ('available','unavailable','unknown')",
            name="ck_devices_availability",
        ),
    )


class LeaseRow(Base):
    __tablename__ = "leases"

    lease_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL for historical leases; TRUE for the one live lease. Multiple NULLs are
    # allowed by both SQLite and PostgreSQL, while the unique constraint makes
    # two active rows for one device impossible even across server processes.
    active_slot: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    # Keep the original database column name for a minimal, safe migration;
    # the public/API name describes its meaning explicitly.
    last_renewed_at: Mapped[datetime] = mapped_column(
        "renewed_at", UTCDateTime(), nullable=False, default=utc_now
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    idle_timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    terminal_reason: Mapped[str | None] = mapped_column(String(255))

    device: Mapped[DeviceRow] = relationship(back_populates="leases")
    user: Mapped[UserRow] = relationship()

    __table_args__ = (
        UniqueConstraint("device_id", "active_slot", name="uq_active_lease_device"),
        CheckConstraint(
            "state IN ('active','released','expired','revoked')",
            name="ck_leases_state",
        ),
        CheckConstraint(
            "(state = 'active' AND active_slot IS TRUE) OR "
            "(state <> 'active' AND active_slot IS NULL)",
            name="ck_leases_active_slot",
        ),
        CheckConstraint("expires_at > created_at", name="ck_leases_expiration"),
        CheckConstraint(
            "idle_timeout_seconds IS NULL OR idle_timeout_seconds > 0",
            name="ck_leases_idle_timeout_positive",
        ),
        Index("ix_leases_device_state", "device_id", "state"),
    )


class LeaseRequestRow(Base):
    __tablename__ = "lease_requests"

    request_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_id: Mapped[str] = mapped_column(
        ForeignKey("leases.lease_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_lease_request_user_key"
        ),
    )


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    manager_id: Mapped[str | None] = mapped_column(String(128))
    device_id: Mapped[str | None] = mapped_column(String(128))
    lease_id: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
