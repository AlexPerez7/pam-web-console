"""Cola de aprobaciones de un segundo operador (principio 4). Las
solicitudes las crea `writeop.run_write`; aprobar = re-enviar el request
original con el header `X-PAM-Approval-Id` (ver `app.core.approvals`).
Acá sólo se listan, rechazan o cancelan. Ver docs/specs/dual-approval.md."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import approvals, audit, rbac
from app.core.deps import current_operator
from app.core.writeop import MIN_REASON_LEN
from app.db import get_session
from app.models import ApprovalRequest, Operator, OperatorRole

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecisionIn(BaseModel):
    reason: str | None = None


async def _can_decide(session: AsyncSession, op: Operator, tenant_id: str, cache: dict) -> bool:
    if tenant_id not in cache:
        role = await rbac.effective_role(op, tenant_id, session)
        cache[tenant_id] = role is not None and rbac.role_meets(role, OperatorRole.approver)
    return cache[tenant_id]


async def _visible(
    session: AsyncSession, op: Operator, row: ApprovalRequest, cache: dict
) -> tuple[bool, bool]:
    """(la ve, la puede aprobar/rechazar). La ve quien la pidió o quien es
    approver+ en ese tenant; aprobar nunca el propio solicitante."""
    own = row.requested_by_id == op.id
    eligible = await _can_decide(session, op, row.tenant_id, cache)
    return own or eligible, eligible and not own and row.status == "pending"


def _row_out(row: ApprovalRequest, can_approve: bool, own: bool) -> dict:
    return approvals.to_dict(row) | {"can_approve": can_approve, "is_mine": own}


@router.get("")
async def list_approvals(
    status: Literal["pending", "all"] = "pending",
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(200)
    if status == "pending":
        q = q.where(ApprovalRequest.status == "pending")
    rows = (await session.execute(q)).scalars().all()
    cache: dict = {}
    out = []
    changed = False
    for row in rows:
        before = row.status
        approvals.expire_if_needed(row)
        changed |= before != row.status
        if status == "pending" and row.status != "pending":
            continue
        visible, can_approve = await _visible(session, op, row, cache)
        if visible:
            out.append(_row_out(row, can_approve, row.requested_by_id == op.id))
    if changed:
        await session.commit()
    return {"count": len(out), "approvals": out}


@router.get("/count")
async def count_pending(
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Pendientes que ESTE operador puede aprobar -- badge del sidebar."""
    rows = (
        (await session.execute(select(ApprovalRequest).where(ApprovalRequest.status == "pending")))
        .scalars()
        .all()
    )
    cache: dict = {}
    n = 0
    for row in rows:
        approvals.expire_if_needed(row)
        _, can_approve = await _visible(session, op, row, cache)
        n += int(can_approve)
    return {"pending": n}


async def _get_visible(
    session: AsyncSession, op: Operator, approval_id: str
) -> tuple[ApprovalRequest, bool]:
    row = await session.get(ApprovalRequest, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="solicitud de aprobación inexistente")
    approvals.expire_if_needed(row)
    visible, can_approve = await _visible(session, op, row, {})
    if not visible:
        raise HTTPException(status_code=404, detail="solicitud de aprobación inexistente")
    return row, can_approve


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row, can_approve = await _get_visible(session, op, approval_id)
    return _row_out(row, can_approve, row.requested_by_id == op.id)


async def _record(
    session: AsyncSession, op: Operator, request: Request, row: ApprovalRequest, action: str
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action=action,
        result="ok",
        tenant_id=row.tenant_id,
        target=row.target,
        source_ip=request.client.host if request.client else None,
        detail={
            "approval_id": row.id,
            "feature": row.feature,
            "requested_by": row.requested_by,
            "reason": row.decision_reason,
        },
    )
    await session.commit()


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: str,
    body: DecisionIn,
    request: Request,
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row, can_approve = await _get_visible(session, op, approval_id)
    if row.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"la solicitud ya no está pendiente ({row.status})"
        )
    if not can_approve:
        raise HTTPException(status_code=403, detail="no puede rechazar esta solicitud")
    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"indique el motivo del rechazo (mínimo {MIN_REASON_LEN} caracteres)",
        )
    approvals.decide(row, "rejected", op, reason)
    await _record(session, op, request, row, "approval.reject")
    return approvals.to_dict(row)


@router.post("/{approval_id}/cancel")
async def cancel(
    approval_id: str,
    request: Request,
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row, _ = await _get_visible(session, op, approval_id)
    if row.requested_by_id != op.id:
        raise HTTPException(status_code=403, detail="sólo quien la solicitó puede cancelarla")
    if row.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"la solicitud ya no está pendiente ({row.status})"
        )
    approvals.decide(row, "cancelled", op, None)
    await _record(session, op, request, row, "approval.cancel")
    return approvals.to_dict(row)
