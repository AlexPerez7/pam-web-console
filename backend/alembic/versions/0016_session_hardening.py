"""Endurecimiento de sesión: tabla refresh_tokens (refresh de un solo uso,
rotación por familia, detección de reutilización) + operators.totp_last_step
(un código TOTP no se puede usar dos veces). Ver
docs/specs/done/session-hardening.md.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("operators", sa.Column("totp_last_step", sa.Integer, nullable=True))
    op.create_table(
        "refresh_tokens",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column(
            "operator_id",
            sa.Integer,
            sa.ForeignKey("operators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("family_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_tokens_operator_id", "refresh_tokens", ["operator_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_operator_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_column("operators", "totp_last_step")
