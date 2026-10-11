from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ComplianceExemption(Base):
    """
    Excepción manual de compliance para una cuenta puntual (ver
    docs/specs/compliance-exemptions.md): cuentas con la gestión automática
    deshabilitada a pedido del cliente, para siempre (ej. cuentas root sólo
    custodiadas, sin uso), que no deben restar del % de Compliance aunque
    estén "vencidas" contra la política de su plataforma. CyberArk no
    registra el motivo de esa desactivación, así que la excepción vive acá,
    con motivo obligatorio y auditada (`compliance.exemption.create`/
    `.delete`) — nunca se excluye una cuenta en silencio.

    Sólo excepciones ACTIVAS: al sacarla se borra la fila de verdad, el
    historial de quién la creó/sacó y cuándo queda en `audit_log`
    (append-only, hash encadenado), no hace falta duplicarlo acá.
    """

    __tablename__ = "compliance_exemptions"
    __table_args__ = (UniqueConstraint("tenant_id", "account_id", name="uq_compliance_exemption"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
