"""add diet and wellness tracking

Revision ID: 8a2f9c1d4e70
Revises: 41c1d2a1e3f0
Create Date: 2026-10-03 18:27:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8a2f9c1d4e70"
down_revision: str | None = "41c1d2a1e3f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("diet_plans", sa.Column("target_calories", sa.Integer(), nullable=True))
    op.add_column(
        "diet_plans",
        sa.Column("macro_targets", sa.JSON(), server_default="{}", nullable=False),
    )
    op.add_column(
        "diet_plans",
        sa.Column("excluded_foods", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "foods",
        sa.Column("allergens", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column(
        "foods",
        sa.Column("excluded_conditions", sa.JSON(), server_default="[]", nullable=False),
    )
    op.add_column("meal_items", sa.Column("scheduled_on", sa.Date(), nullable=True))
    op.add_column(
        "meal_items",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("goals", sa.Column("goal_kind", sa.String(length=32), nullable=True))
    op.create_table(
        "goal_logs",
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("logged_on", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=12, scale=4), nullable=False),
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
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("goal_id", "logged_on", name="uq_goal_logs_goal_id_logged_on"),
    )


def downgrade() -> None:
    op.drop_table("goal_logs")
    op.drop_column("goals", "goal_kind")
    op.drop_column("meal_items", "sort_order")
    op.drop_column("meal_items", "scheduled_on")
    op.drop_column("foods", "excluded_conditions")
    op.drop_column("foods", "allergens")
    op.drop_column("diet_plans", "excluded_foods")
    op.drop_column("diet_plans", "macro_targets")
    op.drop_column("diet_plans", "target_calories")
