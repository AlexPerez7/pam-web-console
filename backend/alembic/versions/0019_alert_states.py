"""Tabla alert_states: estado de las alertas de Fase 5 (componente/tenant
caído, aprobación pendiente sin resolver). Ver docs/specs/done/alert-rules.md.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-30

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_states",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rule", sa.String(60), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_count", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_alert_states_rule", "alert_states", ["rule"])
    op.create_index("ix_alert_states_tenant_id", "alert_states", ["tenant_id"])
    op.create_index(
        "ix_alert_states_open_lookup",
        "alert_states",
        ["rule", "tenant_id", "target", "resolved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_states_open_lookup", table_name="alert_states")
    op.drop_index("ix_alert_states_tenant_id", table_name="alert_states")
    op.drop_index("ix_alert_states_rule", table_name="alert_states")
    op.drop_table("alert_states")
