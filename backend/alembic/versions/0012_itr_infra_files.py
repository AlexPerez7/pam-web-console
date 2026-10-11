"""itr_infra_files: HealthCheck_<host>_<timestamp>.json subidos a mano por
tenant, para la pestana Infraestructura del reporte ITR (ver
app/itr/infra_data.py, portado de Telemetry-Reporter).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "itr_infra_files",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("uploaded_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_itr_infra_files_tenant_id", "itr_infra_files", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("itr_infra_files")
