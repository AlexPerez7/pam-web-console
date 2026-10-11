from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AlertState(Base):
    """
    Estado de una alerta de Fase 5 (docs/specs/done/alert-rules.md) — una fila
    por (regla, tenant, target) actualmente abierta o ya resuelta. La
    evalúa `app/jobs/alerts.py::check_alert_rules` (cron ARQ): un problema
    nuevo abre una fila y notifica una vez; mientras siga activo sólo
    recuerda cada `PAM_ALERTS_RENOTIFY_AFTER_HOURS` (no en cada corrida);
    cuando el problema desaparece se marca `resolved_at` y notifica la
    resolución.

    Si la misma condición se dispara de nuevo después de resuelta, se crea
    una fila NUEVA (no se reabre la vieja) — el historial de aperturas
    pasadas vive en `audit_log` (`alert.rule.opened`/`.renotified`/
    `.resolved`), no hace falta duplicarlo acá. Que no haya dos filas
    ABIERTAS a la vez para la misma (rule, tenant_id, target) lo garantiza
    `check_alert_rules` (única corrida, nunca concurrente) al buscar por
    `resolved_at IS NULL` antes de crear una fila nueva — no una
    `UniqueConstraint` de SQL: Postgres trata cada NULL como distinto, así
    que una constraint sobre una columna nullable no serviría acá.
    """

    __tablename__ = "alert_states"
    __table_args__ = (
        Index("ix_alert_states_open_lookup", "rule", "tenant_id", "target", "resolved_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule: Mapped[str] = mapped_column(String(60), index=True)
    # nullable: alguna regla futura podría ser global (no de un tenant puntual)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target: Mapped[str] = mapped_column(String(200))

    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_count: Mapped[int] = mapped_column(Integer, default=0)
    # Aviso que falta entregar ("nueva"/"sigue activa"/"empeoró"/"resuelta")
    # porque ningún canal lo recibió; `app/jobs/alerts.py` lo reintenta en
    # cada corrida hasta que se entregue (docs/specs/alert-delivery.md).
    # NULL = nada pendiente.
    pending_notify: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Debounce (docs/specs/alert-delivery.md, req. 3). `confirmed_at` NULL =
    # candidato visto en menos de `PAM_ALERTS_CONFIRM_RUNS` corridas
    # seguidas: todavía no se avisó ni se auditó. `consecutive_misses` cuenta
    # las corridas limpias seguidas de una alerta confirmada, hasta
    # `PAM_ALERTS_RESOLVE_RUNS`.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_hits: Mapped[int] = mapped_column(Integer, default=1)
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)
