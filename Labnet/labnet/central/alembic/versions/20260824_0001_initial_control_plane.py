"""Create the transactional Central control-plane schema.

Revision ID: 20260824_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False, unique=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin','user','device_manager')", name="ck_users_role"
        ),
    )
    op.create_table(
        "api_keys",
        sa.Column("api_key_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "device_managers",
        sa.Column("manager_id", sa.String(128), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(64),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("api_version", sa.String(128), nullable=False),
        sa.Column("secure", sa.Boolean(), nullable=False),
        sa.Column("tls_server_name", sa.String(255)),
        sa.Column("endpoint_attributes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("transport = 'grpc'", name="ck_managers_grpc_transport"),
        sa.CheckConstraint(
            "status IN ('online','offline')", name="ck_managers_status"
        ),
    )
    op.create_index(
        "ix_device_managers_last_seen", "device_managers", ["last_seen"]
    )
    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(128), primary_key=True),
        sa.Column(
            "manager_id",
            sa.String(128),
            sa.ForeignKey("device_managers.manager_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_type", sa.String(128), nullable=False),
        sa.Column("runtime_state", sa.String(32), nullable=False),
        sa.Column("availability", sa.String(32), nullable=False),
        sa.Column("status_text", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "availability IN ('available','unavailable','unknown')",
            name="ck_devices_availability",
        ),
    )
    op.create_index("ix_devices_manager_id", "devices", ["manager_id"])
    op.create_index("ix_devices_device_type", "devices", ["device_type"])
    op.create_index("ix_devices_last_seen", "devices", ["last_seen"])
    op.create_table(
        "leases",
        sa.Column("lease_id", sa.String(80), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(128),
            sa.ForeignKey("devices.device_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("active_slot", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_reason", sa.String(255)),
        sa.UniqueConstraint("device_id", "active_slot", name="uq_active_lease_device"),
        sa.CheckConstraint(
            "state IN ('active','released','expired','revoked')",
            name="ck_leases_state",
        ),
        sa.CheckConstraint(
            "(state = 'active' AND active_slot IS TRUE) OR "
            "(state <> 'active' AND active_slot IS NULL)",
            name="ck_leases_active_slot",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_leases_expiration"),
    )
    op.create_index("ix_leases_user_id", "leases", ["user_id"])
    op.create_index("ix_leases_device_state", "leases", ["device_id", "state"])
    op.create_table(
        "lease_requests",
        sa.Column("request_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "lease_id",
            sa.String(80),
            sa.ForeignKey("leases.lease_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_lease_request_user_key"
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.String(64),
            sa.ForeignKey("users.user_id", ondelete="SET NULL"),
        ),
        sa.Column("manager_id", sa.String(128)),
        sa.Column("device_id", sa.String(128)),
        sa.Column("lease_id", sa.String(80)),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("lease_requests")
    op.drop_table("leases")
    op.drop_table("devices")
    op.drop_table("device_managers")
    op.drop_table("api_keys")
    op.drop_table("users")
