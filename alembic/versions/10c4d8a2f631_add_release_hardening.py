"""add release hardening

Revision ID: 10c4d8a2f631
Revises: 9b7a3e1c5d20
Create Date: 2026-10-03 19:08:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "10c4d8a2f631"
down_revision: str | None = "9b7a3e1c5d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_deleted_at_purge_after", "users", ["deleted_at", "purge_after"])
    op.create_index(
        "ix_health_records_profile_type_recorded_at",
        "health_records",
        ["profile_id", "type", "recorded_at"],
    )
    op.create_index(
        "ix_appointments_profile_starts_at",
        "appointments",
        ["profile_id", "starts_at"],
    )
    op.create_index(
        "ix_reminders_status_due_at_profile",
        "reminders",
        ["status", "due_at", "profile_id"],
    )
    op.create_table(
        "api_rate_limits",
        sa.Column("bucket_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket_hash"),
    )
    op.create_index(
        "ix_api_rate_limits_window_started_at",
        "api_rate_limits",
        ["window_started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_rate_limits_window_started_at", table_name="api_rate_limits")
    op.drop_table("api_rate_limits")
    op.drop_index("ix_reminders_status_due_at_profile", table_name="reminders")
    op.drop_index("ix_appointments_profile_starts_at", table_name="appointments")
    op.drop_index("ix_health_records_profile_type_recorded_at", table_name="health_records")
    op.drop_index("ix_users_deleted_at_purge_after", table_name="users")
    op.drop_column("users", "purge_after")
