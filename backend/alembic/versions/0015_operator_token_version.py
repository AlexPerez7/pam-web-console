"""Agrega operators.token_version -- permite invalidar todas las sesiones de
un operador (JWT viejos dejan de servir) sin esperar su expiracion natural,
al cambiar la contrasena o al hacer logout. Ver docs/specs/done/login-hardening.md.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-23

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "operators",
        sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("operators", "token_version")
