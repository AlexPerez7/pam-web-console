"""campaigns: campañas de rotación/remediación con seguimiento (Fase 4)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-15

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_campaigns_tenant_id", "campaigns", ["tenant_id"])

    op.create_table(
        "campaign_accounts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer,
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("account_name", sa.String(300), nullable=False),
        sa.Column("safe_name", sa.String(200), nullable=False),
        sa.Column("platform_id", sa.String(100), nullable=False),
    )
    op.create_index("ix_campaign_accounts_campaign_id", "campaign_accounts", ["campaign_id"])


def downgrade() -> None:
    op.drop_table("campaign_accounts")
    op.drop_table("campaigns")
