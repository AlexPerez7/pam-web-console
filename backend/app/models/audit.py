from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """
    Log de auditoría append-only con hash encadenado. Toda acción de PAM Web Console
    (incluidas las lecturas) queda acá, atribuida al operador real — CyberArk
    solo ve al service account. Ver docs/PLAN.md §5.4.

    Sin `updated_at`: no se modifica nunca. El bloqueo de UPDATE/DELETE se
    refuerza con grants de Postgres además de la lógica de la app.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    operator: Mapped[str] = mapped_column(String(120), index=True)
    role: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    action: Mapped[str] = mapped_column(
        String(120), index=True
    )  # "account.read", "safe.member.remove"
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # diff before/after, params
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(String(40))  # "ok" | "error" | "denied"
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash: Mapped[str] = mapped_column(String(64))
