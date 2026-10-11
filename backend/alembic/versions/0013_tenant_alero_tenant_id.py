"""tenants.alero_tenant_id: id de tenant de CyberArk Remote Access (Alero)
-- gatea el espacio de nav "Alero" (nulo = el cliente no usa Remote
Access). Las credenciales del service account (privado) viven en el
secret store, no acá -- ver secrets/README.md y app/alero/client.py.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-21

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("alero_tenant_id", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "alero_tenant_id")
