"""Add immutable releases, deployment attempts, and update admission state.

Revision ID: 20260825_0003
Revises: 20260824_0002
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_0003"
down_revision = "20260824_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_managers",
        sa.Column(
            "admission_state",
            sa.String(length=32),
            sa.CheckConstraint(
                "admission_state IN ('accepting','draining','updating')",
                name="ck_managers_admission_state",
            ),
            nullable=False,
            server_default="accepting",
        ),
    )
    # Adding this column directly avoids SQLite's batch-table recreation, which
    # cannot drop the referenced devices table while leases already exist.
    op.add_column(
        "devices",
        sa.Column(
            "admission_state",
            sa.String(length=32),
            sa.CheckConstraint(
                "admission_state IN ('accepting','draining','updating')",
                name="ck_devices_admission_state",
            ),
            nullable=False,
            server_default="accepting",
        ),
    )

    op.create_table(
        "release_artifacts",
        sa.Column("release_id", sa.String(length=80), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False, unique=True),
        sa.Column("artifact_filename", sa.String(length=255), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size", sa.BigInteger(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(length=64),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("promoted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('pending_upload','candidate','promoted','withdrawn')",
            name="ck_release_artifacts_state",
        ),
        sa.CheckConstraint(
            "artifact_size > 0",
            name="ck_release_artifact_size",
        ),
    )

    op.create_table(
        "deployment_assignments",
        sa.Column(
            "manager_id",
            sa.String(length=128),
            sa.ForeignKey("device_managers.manager_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "desired_release_id",
            sa.String(length=80),
            sa.ForeignKey("release_artifacts.release_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("observed_version", sa.String(length=64)),
        sa.Column("previous_version", sa.String(length=64)),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("active_attempt_id", sa.String(length=80)),
        sa.Column(
            "assigned_by_user_id",
            sa.String(length=64),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint(
            "state IN ('assigned','downloading','staged','draining','quiescing',"
            "'switching','verifying','succeeded','failed','rolled_back')",
            name="ck_deployment_assignments_state",
        ),
    )

    op.create_table(
        "update_attempts",
        sa.Column("attempt_id", sa.String(length=80), primary_key=True),
        sa.Column(
            "manager_id",
            sa.String(length=128),
            sa.ForeignKey("device_managers.manager_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "release_id",
            sa.String(length=80),
            sa.ForeignKey("release_artifacts.release_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("from_version", sa.String(length=64)),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "state IN ('assigned','downloading','staged','draining','quiescing',"
            "'switching','verifying','succeeded','failed','rolled_back')",
            name="ck_update_attempts_state",
        ),
    )
    op.create_index(
        "ix_update_attempts_manager_id",
        "update_attempts",
        ["manager_id"],
    )
    op.create_index(
        "ix_update_attempts_release_id",
        "update_attempts",
        ["release_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_update_attempts_release_id", table_name="update_attempts")
    op.drop_index("ix_update_attempts_manager_id", table_name="update_attempts")
    op.drop_table("update_attempts")
    op.drop_table("deployment_assignments")
    op.drop_table("release_artifacts")
    op.drop_column("devices", "admission_state")
    op.drop_column("device_managers", "admission_state")
