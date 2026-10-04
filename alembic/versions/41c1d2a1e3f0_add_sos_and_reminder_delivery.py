"""add SOS and reminder delivery

Revision ID: 41c1d2a1e3f0
Revises: 7bf041a7d912
Create Date: 2026-10-03 18:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "41c1d2a1e3f0"
down_revision: str | None = "7bf041a7d912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sos_events", sa.Column("public_token_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "sos_events",
        sa.Column("public_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sos_events_public_token_hash",
        "sos_events",
        ["public_token_hash"],
        unique=True,
    )
    op.add_column(
        "reminders",
        sa.Column(
            "recurrence",
            sa.String(length=16),
            server_default="none",
            nullable=False,
        ),
    )
    op.add_column(
        "reminders",
        sa.Column("recurrence_interval", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "reminders",
        sa.Column("recurrence_anchor_day", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "reminders",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reminders",
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "notification_outbox",
        sa.Column("sos_event_id", sa.Uuid(), nullable=True),
        sa.Column("reminder_id", sa.Uuid(), nullable=True),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["reminder_id"], ["reminders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sos_event_id"], ["sos_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_outbox_status",
        "notification_outbox",
        ["status"],
        unique=False,
    )
    op.create_table(
        "sos_public_access",
        sa.Column("client_key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_key_hash"),
    )


def downgrade() -> None:
    op.drop_table("sos_public_access")
    op.drop_index("ix_notification_outbox_status", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_column("reminders", "last_notified_at")
    op.drop_column("reminders", "snoozed_until")
    op.drop_column("reminders", "recurrence_anchor_day")
    op.drop_column("reminders", "recurrence_interval")
    op.drop_column("reminders", "recurrence")
    op.drop_index("ix_sos_events_public_token_hash", table_name="sos_events")
    op.drop_column("sos_events", "public_expires_at")
    op.drop_column("sos_events", "public_token_hash")
