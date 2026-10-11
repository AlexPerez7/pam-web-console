"""alert_states.pending_notify: aviso que falta entregar (reintento).
Ver docs/specs/alert-delivery.md.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-01

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("alert_states", sa.Column("pending_notify", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_states", "pending_notify")
