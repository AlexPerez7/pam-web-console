"""tenants.compliance_stale_days: umbral (dias) ajustable por tenant para
Compliance -- cuentas con gestion automatica deshabilitada y sin rotar hace
mas de este umbral cuentan como no-compliant (ver app/analysis.py).

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-17

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("compliance_stale_days", sa.Integer, nullable=False, server_default="30"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "compliance_stale_days")
