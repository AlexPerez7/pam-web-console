from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApprovalRequest(Base):
    """
    Solicitud de aprobación de un segundo operador (principio 4) para una
    operación de Fase 3 (las que tienen `Plan.confirm_phrase`). La crea
    `writeop.run_write` en vez de ejecutar, cuando quien ejecuta no es
    `platform_admin`. Se aprueba RE-ENVIANDO el mismo request con el header
    `X-PAM-Approval-Id` (otro operador, `approver+`): la operación corre de
    nuevo contra el estado actual y sólo procede si el plan coincide con el
    solicitado (`plan_digest`). Ver docs/specs/dual-approval.md.

    Estados: pending | approved | rejected | cancelled | expired | stale.
    """

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    feature: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(300))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    # `changes` tal como lo recibió run_write -- se compara con el del
    # aprobador (debe ser idéntico). Las ops de Fase 3 no llevan secretos.
    changes_json: Mapped[dict] = mapped_column(JSON)
    # body original sin dry_run/reason/confirm, para que la UI lo re-envíe
    body_json: Mapped[dict] = mapped_column(JSON)
    plan_digest: Mapped[str] = mapped_column(String(64))
    diff_json: Mapped[list] = mapped_column(JSON)
    effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[list] = mapped_column(JSON)
    confirm_phrase: Mapped[str | None] = mapped_column(String(300), nullable=True)

    requested_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("operators.id", ondelete="CASCADE")
    )
    requested_by: Mapped[str] = mapped_column(String(120))
    request_reason: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
