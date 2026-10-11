"""initial: tenants, operators, audit_log

Revision ID: 0001
Revises:
Create Date: 2026-09-09

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

deployment = sa.Enum("privilege_cloud", "self_hosted", name="deployment")
operatorrole = sa.Enum(
    "viewer", "operator", "approver", "tenant_admin", "platform_admin", name="operatorrole"
)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("client", sa.String(200), nullable=False),
        sa.Column("deployment", deployment, nullable=False, server_default="privilege_cloud"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("pvwa_url", sa.String(500), nullable=False),
        sa.Column("identity_tenant_url", sa.String(500)),
        sa.Column("connector_management_url", sa.String(500)),
        sa.Column("sia_connector_url", sa.String(500)),
        sa.Column("secret_ref", sa.String(200), nullable=False),
        sa.Column("enabled_features", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("verify_ssl", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "operators",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("email", sa.String(200)),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("totp_secret", sa.String(64)),
        sa.Column("totp_enrolled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("role", operatorrole, nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("username", name="uq_operators_username"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.String(64)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target", sa.String(500)),
        sa.Column("detail", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column("source_ip", sa.String(64)),
        sa.Column("prev_hash", sa.String(64)),
        sa.Column("hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_operator", "audit_log", ["operator"])
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("operators")
    op.drop_table("tenants")
    operatorrole.drop(op.get_bind(), checkfirst=True)
    deployment.drop(op.get_bind(), checkfirst=True)
