"""tenants.client_logo / client_logo_content_type: logo del cliente para el
header del reporte ITR (junto al logo del equipo) -- bytes en la DB, sin
archivo en disco. Ver app/itr/report_builder.py, client_logo_path.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("client_logo", sa.LargeBinary, nullable=True))
    op.add_column("tenants", sa.Column("client_logo_content_type", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "client_logo_content_type")
    op.drop_column("tenants", "client_logo")
