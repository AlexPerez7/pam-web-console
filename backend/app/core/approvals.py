"""Aprobación de un segundo operador (principio 4 de CLAUDE.md) para las
operaciones de Fase 3 -- las que tienen `Plan.confirm_phrase`.

Regla: quien ejecuta siendo `platform_admin` (rol de plataforma) ejecuta
directo; cualquier otro rol deja una `ApprovalRequest` pendiente y NO toca
CyberArk. Otro operador (`approver+` efectivo en el tenant, o
`platform_admin`, nunca el mismo solicitante) aprueba re-enviando el mismo
request con el header `X-PAM-Approval-Id`: `run_write` recalcula el plan
contra el estado actual y sólo ejecuta si coincide con el solicitado.

Ver docs/specs/dual-approval.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import rbac
from app.models import ApprovalRequest, Operator, OperatorRole

if TYPE_CHECKING:
    from app.core.writeop import Plan

APPROVAL_HEADER = "x-pam-approval-id"
# campos del body que NO forman parte de la operación en sí: cada operador
# pone los suyos (razón, confirmación) y `dry_run` cambia entre preview y
# ejecución.
_PER_OPERATOR_FIELDS = {"dry_run", "reason", "confirm"}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def aware(value: dt.datetime) -> dt.datetime:
    """SQLite (tests) devuelve datetimes naive; Postgres, aware. Todo es UTC."""
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def plan_digest(plan: Plan) -> str:
    """Huella de lo que la operación VA a hacer (diff + patch + efecto). Si
    el objeto cambió entre la solicitud y la aprobación, cambia el diff y la
    huella deja de coincidir."""
    payload = {"diff": plan.diff, "patch": plan.patch, "effect": plan.effect}
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def requires_approval(op: Operator, plan: Plan) -> bool:
    if not get_settings().dual_approval_enabled:
        return False
    if plan.confirm_phrase is None:  # sólo Fase 3
        return False
    return op.role is not OperatorRole.platform_admin


def approval_id_from(request: Request) -> str | None:
    value = (request.headers.get(APPROVAL_HEADER) or "").strip()
    return value or None


async def _request_body(request: Request) -> dict:
    try:
        raw = await request.body()
        data = json.loads(raw) if raw else {}
    except (ValueError, RuntimeError):
        data = {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k not in _PER_OPERATOR_FIELDS}


async def create_request(
    session: AsyncSession,
    *,
    op: Operator,
    request: Request,
    tenant_id: str,
    feature: str,
    target: str,
    changes: dict,
    plan: Plan,
    reason: str,
) -> ApprovalRequest:
    created = now()
    row = ApprovalRequest(
        id=secrets.token_hex(16),
        tenant_id=tenant_id,
        feature=feature,
        target=target,
        method=request.method,
        path=request.url.path,
        changes_json=json.loads(canonical(changes)),
        body_json=await _request_body(request),
        plan_digest=plan_digest(plan),
        diff_json=json.loads(canonical(plan.diff)),
        effect=plan.effect,
        warnings_json=list(plan.warnings),
        confirm_phrase=plan.confirm_phrase,
        requested_by_id=op.id,
        requested_by=op.username,
        request_reason=reason,
        status="pending",
        created_at=created,
        expires_at=created + dt.timedelta(hours=get_settings().approval_ttl_hours),
    )
    session.add(row)
    await session.flush()
    return row


def expire_if_needed(row: ApprovalRequest) -> None:
    if row.status == "pending" and aware(row.expires_at) <= now():
        row.status = "expired"


async def load_for_execution(
    session: AsyncSession,
    *,
    approval_id: str,
    op: Operator,
    request: Request,
    tenant_id: str,
    feature: str,
    target: str,
    changes: dict,
) -> ApprovalRequest:
    """Valida que `approval_id` se pueda aprobar con ESTE request. No mira el
    plan (eso lo hace `check_digest`, después de recalcularlo). Levanta
    HTTPException si algo no cuadra."""
    row = await session.get(ApprovalRequest, approval_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="solicitud de aprobación inexistente")
    expire_if_needed(row)
    if row.status != "pending":
        await session.commit()
        raise HTTPException(
            status_code=409, detail=f"la solicitud ya no está pendiente (estado: {row.status})"
        )
    if row.requested_by_id == op.id:
        raise HTTPException(
            status_code=403,
            detail="no puede aprobar su propia solicitud; debe hacerlo otro operador",
        )
    role = await rbac.effective_role(op, tenant_id, session)
    if role is None or not rbac.role_meets(role, OperatorRole.approver):
        raise HTTPException(status_code=403, detail="aprobar requiere rol 'approver' o superior")
    same_op = (
        row.feature == feature
        and row.target == target
        and row.method == request.method
        and row.path == request.url.path
        and canonical(row.changes_json) == canonical(changes)
    )
    if not same_op:
        raise HTTPException(
            status_code=409, detail="el request no coincide con la operación solicitada"
        )
    return row


def check_digest(row: ApprovalRequest, plan: Plan) -> bool:
    return row.plan_digest == plan_digest(plan)


def decide(row: ApprovalRequest, status: str, op: Operator, reason: str | None) -> None:
    row.status = status
    row.decided_by = op.username
    row.decision_reason = reason
    row.decided_at = now()


def to_dict(row: ApprovalRequest, *, include_body: bool = True) -> dict:
    d: dict[str, Any] = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "feature": row.feature,
        "target": row.target,
        "method": row.method,
        "path": row.path,
        "diff": row.diff_json,
        "effect": row.effect,
        "warnings": row.warnings_json,
        "confirm_phrase": row.confirm_phrase,
        "requested_by": row.requested_by,
        "request_reason": row.request_reason,
        "status": row.status,
        "decided_by": row.decided_by,
        "decision_reason": row.decision_reason,
        "created_at": aware(row.created_at).isoformat(),
        "expires_at": aware(row.expires_at).isoformat(),
        "decided_at": aware(row.decided_at).isoformat() if row.decided_at else None,
    }
    if include_body:
        d["body"] = row.body_json
        d["changes"] = row.changes_json
    return d
