"""add profile record and attachment encryption

Revision ID: c4ca6cc979f2
Revises: e6db49a35695
Create Date: 2026-10-03 17:45:12.269547
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4ca6cc979f2"
down_revision: str | None = "e6db49a35695"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    with op.batch_alter_table(
        "attachments",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.add_column(sa.Column("nonce", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("encryption_key_owner_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_attachments_encryption_key_owner_id_users",
            "users",
            ["encryption_key_owner_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table(
        "health_records",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.add_column(sa.Column("encrypted_payload", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("payload_nonce", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("payload_key_owner_id", sa.Uuid(), nullable=True))
        batch_op.alter_column(
            "value",
            existing_type=sa.Numeric(precision=12, scale=4),
            nullable=True,
        )
        batch_op.alter_column(
            "unit",
            existing_type=sa.String(length=32),
            nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_health_records_payload_key_owner_id_users",
            "users",
            ["payload_key_owner_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table(
        "member_profiles",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=120), nullable=True))
        batch_op.alter_column(
            "user_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "member_profiles",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.alter_column(
            "user_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch_op.drop_column("display_name")

    with op.batch_alter_table(
        "health_records",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_health_records_payload_key_owner_id_users",
            type_="foreignkey",
        )
        batch_op.alter_column(
            "unit",
            existing_type=sa.String(length=32),
            nullable=False,
        )
        batch_op.alter_column(
            "value",
            existing_type=sa.Numeric(precision=12, scale=4),
            nullable=False,
        )
        batch_op.drop_column("payload_key_owner_id")
        batch_op.drop_column("payload_nonce")
        batch_op.drop_column("encrypted_payload")

    with op.batch_alter_table(
        "attachments",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_attachments_encryption_key_owner_id_users",
            type_="foreignkey",
        )
        batch_op.drop_column("encryption_key_owner_id")
        batch_op.drop_column("nonce")
