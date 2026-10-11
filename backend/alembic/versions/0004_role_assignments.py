"""role_assignments: RBAC scopeado por tenant

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# el tipo enum ya existe (lo creó 0001); create_type=False evita recrearlo
operatorrole = postgresql.ENUM(
    "viewer",
    "operator",
    "approver",
    "tenant_admin",
    "platform_admin",
    name="operatorrole",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "operator_id",
            sa.Integer,
            sa.ForeignKey("operators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", operatorrole, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("operator_id", "tenant_id", name="uq_role_assignment"),
    )
    op.create_index("ix_role_assignments_operator_id", "role_assignments", ["operator_id"])


def downgrade() -> None:
    op.drop_table("role_assignments")
