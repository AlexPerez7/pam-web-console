"""kb_articles: base de conocimiento de errores (causa/solución) — global,
sin tenant_id

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-15

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_articles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("error_pattern", sa.String(300), nullable=False),
        sa.Column("cause", sa.Text, nullable=False),
        sa.Column("solution", sa.Text, nullable=False),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("updated_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("kb_articles")
