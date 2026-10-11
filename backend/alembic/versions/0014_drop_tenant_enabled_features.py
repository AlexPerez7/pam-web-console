"""Elimina tenants.enabled_features -- se saco el gating por feature-flag por
tenant (decision del jefe de PAM, 2026-09-22): el unico freno de una
escritura pasa a ser el rol RBAC minimo del endpoint (`require_tenant_access`
/ `require_role`), ya definido por operacion. Ver CLAUDE.md.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-22

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("tenants", "enabled_features")


def downgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("enabled_features", sa.JSON, nullable=False, server_default="{}"),
    )
