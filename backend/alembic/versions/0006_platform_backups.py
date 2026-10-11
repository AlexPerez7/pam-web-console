"""platform_backups: paquete de exportación de plataformas borradas (Fase 3)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_backups",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform_id", sa.String(100), nullable=False),
        sa.Column("numeric_id", sa.String(32), nullable=False),
        sa.Column("package", sa.LargeBinary, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("deleted_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_platform_backups_tenant_id", "platform_backups", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("platform_backups")
