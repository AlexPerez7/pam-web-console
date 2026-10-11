"""alert_states: columnas del debounce (confirmed_at, consecutive_hits,
consecutive_misses). Ver docs/specs/alert-delivery.md.

Las filas existentes ya se notificaron, así que se migran como confirmadas
(`confirmed_at = opened_at`).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-06

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alert_states", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "alert_states",
        sa.Column("consecutive_hits", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "alert_states",
        sa.Column("consecutive_misses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE alert_states SET confirmed_at = opened_at")


def downgrade() -> None:
    op.drop_column("alert_states", "consecutive_misses")
    op.drop_column("alert_states", "consecutive_hits")
    op.drop_column("alert_states", "confirmed_at")
