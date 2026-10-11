"""Motor común de las operaciones de escritura de Fase 2.

`run_write()` envuelve todo lo transversal: rate-limit por (operador,
tenant), circuit breaker por cantidad de objetos, `dry-run` + diff,
ejecución, y auditoría con before/after. El gating es sólo RBAC (rol
efectivo mínimo por operación, ya exigido en el endpoint vía
`require_tenant_access`) — no hay feature-flags por tenant. Cada operación
concreta le pasa 3 callables (traer el estado actual, armar el plan, aplicar).

Ver `docs/specs/fase-2-escritura.md`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import approvals, audit, cache
from app.core.tenants import tenant_client
from app.cyberark import Conflict, CyberArkClient, CyberArkError, Forbidden, NotFound
from app.models import Operator, Tenant

MIN_REASON_LEN = 8


@dataclass
class Plan:
    """Lo que va a hacer una operación de escritura.

    Ops de edición: `patch` + `diff`. Ops de acción (change/verify/…): sin
    diff, con `effect` = descripción legible de lo que va a pasar.
    """

    patch: list[dict]  # JSON Patch (RFC 6902) para CyberArk
    diff: list[dict]  # legible para la UI: [{field, from, to}]
    warnings: list[str] = field(default_factory=list)
    object_count: int = 1
    effect: str | None = None  # ops de acción sin diff
    # Fase 3: si no es None, ejecutar exige que `confirm` (ver run_write) sea
    # exactamente igual a este valor — "confirmación reforzada" para
    # operaciones de alto riesgo. None (default) = sin cambios para Fase 2.
    confirm_phrase: str | None = None
    # True sólo para las 3 ops que de verdad encolan un trabajo asíncrono en
    # el CPM (account.change/verify/reconcile, ver `plan_cpm`) — el frontend
    # lo usa para decidir el mensaje de éxito ("pedido enviado, el resultado
    # llega después" vs. "ya terminó"). Antes se deducía de "es una acción
    # sin diff", pero eso también matchea session.terminate/platform.
    # activate/discovery.onboard/etc., que sí terminan en el momento — bug
    # real encontrado probando session.terminate (2026-09-24).
    is_cpm_action: bool = False


Fetch = Callable[[CyberArkClient], Awaitable[dict]]
BuildPlan = Callable[[dict, dict], Plan]
Apply = Callable[[CyberArkClient, Plan], Awaitable[dict]]


async def _audit(
    session: AsyncSession,
    op: Operator,
    request: Request,
    *,
    feature: str,
    result: str,
    tenant_id: str,
    target: str,
    dry_run: bool,
    detail: dict,
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action=feature,
        result=result,
        tenant_id=tenant_id,
        target=target,
        dry_run=dry_run,
        source_ip=request.client.host if request.client else None,
        detail=detail,
    )
    await session.commit()


def _changed_fields(diff: list[dict]) -> tuple[dict, dict]:
    before = {d["field"]: d.get("from") for d in diff}
    after = {d["field"]: d.get("to") for d in diff}
    return before, after


async def run_write(
    *,
    session: AsyncSession,
    op: Operator,
    request: Request,
    tenant_id: str,
    feature: str,
    target: str,
    changes: dict,
    dry_run: bool,
    reason: str | None,
    fetch: Fetch,
    build_plan: BuildPlan,
    apply: Apply,
    confirm: str | None = None,
) -> dict[str, Any]:
    s = get_settings()

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    # 2. razón obligatoria para ejecutar
    reason = (reason or "").strip()
    if not dry_run and len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"'reason' es obligatoria para ejecutar (mínimo {MIN_REASON_LEN} caracteres)",
        )

    # 3. rate-limit de escritura (los dry-run no cuentan)
    rate_limit_checked = True
    if not dry_run:
        key = f"pam:write:{op.username}:{tenant_id}"
        n = await cache.incr_with_ttl(key, s.write_window_minutes * 60)
        if n is None:
            rate_limit_checked = False
        elif n > s.write_max_per_window:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="denied",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={"reason": "rate_limited", "count": n},
            )
            raise HTTPException(
                status_code=429,
                detail="demasiadas operaciones de escritura; espere unos minutos",
                headers={"Retry-After": str(s.write_window_minutes * 60)},
            )

    # 4. traer estado actual + armar el plan
    async with tenant_client(tenant_id, session) as (_, client):
        try:
            current = await fetch(client)
        except (NotFound, Forbidden) as e:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="error",
                tenant_id=tenant_id,
                target=target,
                dry_run=dry_run,
                detail={"error": str(e), "phase": "fetch"},
            )
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))

        plan = build_plan(current, changes)

        # 5. circuit breaker
        if plan.object_count > s.write_object_cap:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="denied",
                tenant_id=tenant_id,
                target=target,
                dry_run=dry_run,
                detail={"reason": "too_many_objects", "object_count": plan.object_count},
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"la operación afectaría {plan.object_count} objetos "
                    f"(máx {s.write_object_cap}); use un job (Fase 4)"
                ),
            )

        # una op de acción (effect) nunca es no-op aunque no tenga patch
        no_op = not plan.patch and not plan.effect

        # aprobación de un segundo operador (docs/specs/dual-approval.md):
        # con `X-PAM-Approval-Id`, este request ES la aprobación de una
        # solicitud pendiente -- se valida antes de mostrar o ejecutar nada.
        approval_id = approvals.approval_id_from(request)
        approval_row = None
        if approval_id is not None:
            approval_row = await approvals.load_for_execution(
                session,
                approval_id=approval_id,
                op=op,
                request=request,
                tenant_id=tenant_id,
                feature=feature,
                target=target,
                changes=changes,
            )
        needs_approval = approval_row is None and approvals.requires_approval(op, plan)

        # 6. dry-run: se audita y se devuelve el plan, sin tocar CyberArk
        if dry_run:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="ok",
                tenant_id=tenant_id,
                target=target,
                dry_run=True,
                detail={
                    "diff": plan.diff,
                    "patch": plan.patch,
                    "effect": plan.effect,
                    "no_op": no_op,
                    "reason": reason,
                },
            )
            return {
                "dry_run": True,
                "no_op": no_op,
                "diff": plan.diff,
                "patch": plan.patch,
                "effect": plan.effect,
                "warnings": plan.warnings,
                "confirm_phrase": plan.confirm_phrase,
                "rate_limit_checked": rate_limit_checked,
                "is_cpm_action": plan.is_cpm_action,
                # la UI cambia "Confirmar" por "Solicitar aprobación"
                "requires_approval": needs_approval and not no_op,
                # aprobando: ¿el plan recalculado coincide con el solicitado?
                "approval_matches": (
                    approvals.check_digest(approval_row, plan) if approval_row else None
                ),
            }

        # 6b. Fase 3: confirmación reforzada — antes de tocar CyberArk
        if plan.confirm_phrase is not None and (confirm or "") != plan.confirm_phrase:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="denied",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={"reason": "confirm_mismatch"},
            )
            raise HTTPException(status_code=422, detail="la confirmación no coincide")

        # 7. sin cambios reales -> no se llama a CyberArk
        if no_op:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="ok",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={"no_op": True, "reason": reason},
            )
            return {"dry_run": False, "no_op": True, "result": "ok", "warnings": plan.warnings}

        # 7b. aprobación de un segundo operador
        if approval_row is not None and not approvals.check_digest(approval_row, plan):
            # el objeto cambió desde la solicitud: lo aprobado ya no es lo que
            # se ejecutaría. Se invalida la solicitud.
            approval_row.status = "stale"
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="denied",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={"reason": "approval_stale", "approval_id": approval_row.id},
            )
            raise HTTPException(
                status_code=409,
                detail="el objeto cambió desde la solicitud; hay que volver a solicitarla",
            )
        if needs_approval:
            row = await approvals.create_request(
                session,
                op=op,
                request=request,
                tenant_id=tenant_id,
                feature=feature,
                target=target,
                changes=changes,
                plan=plan,
                reason=reason,
            )
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="pending_approval",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={
                    "approval_id": row.id,
                    "reason": reason,
                    "diff": plan.diff,
                    "effect": plan.effect,
                },
            )
            return {
                "dry_run": False,
                "no_op": False,
                "pending_approval": True,
                "approval_id": row.id,
                "result": "pending_approval",
                "warnings": plan.warnings,
            }

        # 8. ejecutar
        try:
            after_obj = await apply(client, plan)
        except (NotFound, Forbidden, Conflict) as e:
            code = {NotFound: 404, Forbidden: 403, Conflict: 409}[type(e)]
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="error",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={"error": str(e), "phase": "apply", "patch": plan.patch},
            )
            raise HTTPException(status_code=code, detail=str(e))
        except CyberArkError as e:
            await _audit(
                session,
                op,
                request,
                feature=feature,
                result="error",
                tenant_id=tenant_id,
                target=target,
                dry_run=False,
                detail={"error": str(e), "phase": "apply", "patch": plan.patch},
            )
            raise HTTPException(status_code=502, detail=str(e))

    detail: dict = {"reason": reason}
    resp: dict[str, Any] = {
        "dry_run": False,
        "no_op": False,
        "result": "ok",
        "warnings": plan.warnings,
        "object": after_obj,
        "rate_limit_checked": rate_limit_checked,
    }
    if plan.diff:
        before, after = _changed_fields(plan.diff)
        detail |= {"diff": plan.diff, "patch": plan.patch, "before": before, "after": after}
        resp |= {"before": before, "after": after}
    if plan.effect:
        detail["effect"] = plan.effect
        resp["effect"] = plan.effect
    # ops en lote con resultado parcial (ej. `account.bulk-cpm`): lo que
    # falló queda también en el audit, no sólo en la respuesta.
    if isinstance(after_obj, dict) and after_obj.get("failed"):
        detail["failed"] = after_obj["failed"]
    if plan.confirm_phrase is not None:
        detail["confirm_ok"] = True
    if approval_row is not None:
        approvals.decide(approval_row, "approved", op, reason)
        detail |= {
            "approval_id": approval_row.id,
            "requested_by": approval_row.requested_by,
            "request_reason": approval_row.request_reason,
            "approved_by": op.username,
        }
        resp["approval_id"] = approval_row.id

    await _audit(
        session,
        op,
        request,
        feature=feature,
        result="ok",
        tenant_id=tenant_id,
        target=target,
        dry_run=False,
        detail=detail,
    )
    return resp
