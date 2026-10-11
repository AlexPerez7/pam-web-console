"""account_backups: copias de seguridad de cuentas borradas (Fase 3)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_backups",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("account_name", sa.String(300), nullable=False),
        sa.Column("safe_name", sa.String(200), nullable=False),
        sa.Column("user_name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("platform_id", sa.String(100), nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary, nullable=False),
        sa.Column("deleted_by", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_account_backups_tenant_id", "account_backups", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("account_backups")
