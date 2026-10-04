"""add demographic reference ranges

Revision ID: 9b7a3e1c5d20
Revises: 8a2f9c1d4e70
Create Date: 2026-10-03 18:56:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9b7a3e1c5d20"
down_revision: str | None = "8a2f9c1d4e70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reference_ranges") as batch_op:
        batch_op.add_column(sa.Column("sex", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("age_min", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("age_max", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_reference_age_min",
            "age_min IS NULL OR age_min BETWEEN 0 AND 120",
        )
        batch_op.create_check_constraint(
            "ck_reference_age_max",
            "age_max IS NULL OR age_max BETWEEN 0 AND 120",
        )
        batch_op.create_check_constraint(
            "ck_reference_age_order",
            "age_min IS NULL OR age_max IS NULL OR age_min <= age_max",
        )
        batch_op.create_check_constraint(
            "ck_reference_bound_order",
            "lower_bound IS NULL OR upper_bound IS NULL OR lower_bound < upper_bound",
        )
        batch_op.create_index(
            "ix_reference_ranges_demographic_lookup",
            ["record_type_id", "unit", "sex", "age_min", "age_max"],
        )


def downgrade() -> None:
    with op.batch_alter_table("reference_ranges") as batch_op:
        batch_op.drop_index("ix_reference_ranges_demographic_lookup")
        batch_op.drop_constraint("ck_reference_bound_order", type_="check")
        batch_op.drop_constraint("ck_reference_age_order", type_="check")
        batch_op.drop_constraint("ck_reference_age_max", type_="check")
        batch_op.drop_constraint("ck_reference_age_min", type_="check")
        batch_op.drop_column("age_max")
        batch_op.drop_column("age_min")
        batch_op.drop_column("sex")
