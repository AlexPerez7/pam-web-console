"""account_snapshots: copia local de cuentas para filtro por columna server-side

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-16

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_snapshots",
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("platform_id", sa.String(100), nullable=True),
        sa.Column("user_name", sa.String(300), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("safe_name", sa.String(200), nullable=True),
        sa.Column("modification_time", sa.Integer, nullable=True),
        sa.Column("raw", sa.JSON, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_account_snapshots_tenant_platform", "account_snapshots", ["tenant_id", "platform_id"]
    )
    op.create_index(
        "ix_account_snapshots_tenant_user", "account_snapshots", ["tenant_id", "user_name"]
    )
    op.create_index(
        "ix_account_snapshots_tenant_address", "account_snapshots", ["tenant_id", "address"]
    )


def downgrade() -> None:
    op.drop_table("account_snapshots")
