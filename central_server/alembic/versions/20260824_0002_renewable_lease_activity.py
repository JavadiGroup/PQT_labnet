"""Add renewable-lease liveness and command-activity policy.

Revision ID: 20260824_0002
Revises: 20260824_0001
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0002"
down_revision = "20260824_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable first so installations containing historical lease rows can
    # be backfilled without relying on a database-specific datetime default.
    with op.batch_alter_table("leases") as batch_op:
        batch_op.add_column(
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("idle_timeout_seconds", sa.Integer(), nullable=True)
        )

    op.execute(
        sa.text(
            "UPDATE leases "
            "SET renewed_at = COALESCE(renewed_at, created_at), "
            "last_activity_at = created_at, "
            "idle_timeout_seconds = 900"
        )
    )

    with op.batch_alter_table("leases") as batch_op:
        batch_op.alter_column(
            "renewed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.alter_column(
            "last_activity_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_leases_idle_timeout_positive",
            "idle_timeout_seconds IS NULL OR idle_timeout_seconds > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("leases") as batch_op:
        batch_op.drop_constraint(
            "ck_leases_idle_timeout_positive", type_="check"
        )
        batch_op.alter_column(
            "renewed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch_op.drop_column("idle_timeout_seconds")
        batch_op.drop_column("last_activity_at")
