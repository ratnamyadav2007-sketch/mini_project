"""link attachments to health records

Revision ID: 21ad84b3c550
Revises: 10c4d8a2f631
Create Date: 2026-10-03 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "21ad84b3c550"
down_revision: str | None = "10c4d8a2f631"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("attachments") as batch_op:
        batch_op.add_column(sa.Column("record_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_attachments_record_id_health_records",
            "health_records",
            ["record_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_attachments_record_id", ["record_id"])


def downgrade() -> None:
    with op.batch_alter_table("attachments") as batch_op:
        batch_op.drop_index("ix_attachments_record_id")
        batch_op.drop_constraint("fk_attachments_record_id_health_records", type_="foreignkey")
        batch_op.drop_column("record_id")
