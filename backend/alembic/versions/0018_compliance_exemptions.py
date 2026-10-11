"""Tabla compliance_exemptions: excepción manual por cuenta al % de
Compliance (cuentas con gestión automática deshabilitada a pedido del
cliente, para siempre). Ver docs/specs/compliance-exemptions.md.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-29

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compliance_exemptions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "account_id", name="uq_compliance_exemption"),
    )
    op.create_index("ix_compliance_exemptions_tenant_id", "compliance_exemptions", ["tenant_id"])
    op.create_index("ix_compliance_exemptions_account_id", "compliance_exemptions", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_compliance_exemptions_account_id", table_name="compliance_exemptions")
    op.drop_index("ix_compliance_exemptions_tenant_id", table_name="compliance_exemptions")
    op.drop_table("compliance_exemptions")
