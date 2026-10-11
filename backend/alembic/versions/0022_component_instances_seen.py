"""component_instances_seen: última vez que PAM Web Console vio conectada cada
instancia de un componente, para que `component_down` no alerte por
instancias fantasma (docs/specs/alert-delivery.md, req. 7).

Además cierra sin aviso las alertas `component_down` abiertas: casi todas
eran instancias fantasma que con la regla nueva dejan de contar, y
resolverlas por el camino normal mandaría una tarjeta "Resuelta" por cada
una. Las caídas reales se vuelven a abrir solas en las próximas corridas
(con su aviso "nueva").

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-07

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "component_instances_seen",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("component_id", sa.String(60), primary_key=True),
        sa.Column("instance_user", sa.String(200), primary_key=True),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "UPDATE alert_states SET resolved_at = now(), pending_notify = NULL "
        "WHERE rule = 'component_down' AND resolved_at IS NULL "
        "AND target NOT IN ('_tenant', 'Vault')"
    )


def downgrade() -> None:
    op.drop_table("component_instances_seen")
