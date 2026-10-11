from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ComponentInstanceSeen(Base):
    """Última vez que PAM Web Console vio CONECTADA una instancia de un componente
    (CPM, PSM, PVWA...) de un tenant. La usa la regla `component_down`
    (`app/jobs/alerts.py`) para distinguir una caída real de una instancia
    fantasma (dada de baja pero todavía registrada en CyberArk):
    docs/specs/alert-delivery.md, req. 7.

    No alcanza con el `last_logon` que devuelve CyberArk: es la hora del
    último inicio de sesión, no del último contacto, y una instancia que
    lleva días conectada lo tiene igual de viejo que una dada de baja.
    La clave es el usuario de la instancia (`ComponentUserName`), único
    dentro de un componente."""

    __tablename__ = "component_instances_seen"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    component_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    instance_user: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
