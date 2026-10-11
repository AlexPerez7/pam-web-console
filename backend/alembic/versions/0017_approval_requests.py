"""Tabla approval_requests: aprobación de un segundo operador para las
operaciones de Fase 3 (principio 4). Ver docs/specs/dual-approval.md.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("feature", sa.String(80), nullable=False),
        sa.Column("target", sa.String(300), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("changes_json", sa.JSON, nullable=False),
        sa.Column("body_json", sa.JSON, nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("diff_json", sa.JSON, nullable=False),
        sa.Column("effect", sa.Text, nullable=True),
        sa.Column("warnings_json", sa.JSON, nullable=False),
        sa.Column("confirm_phrase", sa.String(300), nullable=True),
        sa.Column(
            "requested_by_id",
            sa.Integer,
            sa.ForeignKey("operators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("request_reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(120), nullable=True),
        sa.Column("decision_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approval_requests_tenant_id", "approval_requests", ["tenant_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_tenant_id", table_name="approval_requests")
    op.drop_table("approval_requests")
