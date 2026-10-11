"""
Visor del audit log.

- `GET /audit` (+ `/summary`, `/operators`, `/export`, `/verify`): visor
  global. Requiere `tenant_admin+` (de plataforma o efectivo en algún
  tenant) y se scopea a esos tenants. `GET /audit` oculta las lecturas
  (`kind == "consulta"`) con resultado `ok` por defecto — señal sobre
  ruido, ver docs/specs/done/audit-page.md (Tanda 1). Paginado por cursor
  (`before_id`), no por offset: la tabla es append-only y el `id` es
  monótono, así que una página nunca se corre aunque entren registros
  nuevos mientras se navega. `GET /audit/export` (Tanda 3) exporta lo
  filtrado sin paginar, con tope y auditado.
- `GET /tenants/{id}/audit`: rastro del audit de un tenant puntual (lo usa
  el panel de detalle). Requiere acceso `viewer` a ese tenant. **No
  cambia** — lo sigue consumiendo `AccountDetailPanel.tsx` con la forma
  de lista simple de siempre.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, audit_catalog, rbac
from app.core.audit import verify_chain
from app.core.csv_export import to_csv
from app.core.deps import require_audit_reader, require_tenant_access
from app.db import get_session
from app.models import ApprovalRequest, AuditLog, Operator

_EXPORT_CAP = 50_000

_EXPORT_COLUMNS = [
    ("id", lambda r: r["id"]),
    ("fecha", lambda r: r["ts"]),
    ("operador", lambda r: r["operator"]),
    ("rol", lambda r: r["role"]),
    ("tenant", lambda r: r["tenant_id"]),
    ("accion", lambda r: r["action"]),
    ("etiqueta", lambda r: r["label"]),
    ("categoria", lambda r: r["category"]),
    ("tipo", lambda r: r["kind"]),
    ("target", lambda r: r["target"]),
    ("target_legible", lambda r: r["target_label"]),
    ("resultado", lambda r: r["result"]),
    ("dry_run", lambda r: r["dry_run"]),
    ("request_id", lambda r: r["request_id"]),
    ("detalle", lambda r: json.dumps(r["detail"], ensure_ascii=False, sort_keys=True)),
]

router = APIRouter(prefix="/audit", tags=["audit"])
tenant_router = APIRouter(prefix="/tenants/{tenant_id}", tags=["audit"])


def _target_label(r: AuditLog) -> str | None:
    """R7: si `detail` ya trae un nombre legible del target, se muestra en
    vez del ID crudo de CyberArk — nunca se consulta CyberArk para
    resolverlo (sería una llamada extra por fila, y el objeto puede ya no
    existir). Hoy sólo un puñado de acciones dejan uno de estos campos en
    `detail`; el resto cae al `target` de siempre."""
    d = r.detail or {}
    for key in ("name", "safeName", "platform_id"):
        v = d.get(key)
        if isinstance(v, str) and v:
            return v
    user_name = d.get("userName")
    if isinstance(user_name, str) and user_name:
        address = d.get("address")
        return f"{user_name}@{address}" if isinstance(address, str) and address else user_name
    return r.target


def _row(r: AuditLog) -> dict:
    return {
        "id": r.id,
        "ts": r.ts.isoformat(),
        "operator": r.operator,
        "role": r.role,
        "tenant_id": r.tenant_id,
        "action": r.action,
        "kind": audit_catalog.classify(r.action),
        "category": audit_catalog.category(r.action),
        "label": audit_catalog.label(r.action),
        "target": r.target,
        "target_label": _target_label(r),
        "result": r.result,
        "dry_run": r.dry_run,
        "detail": r.detail,
        "request_id": r.request_id,
    }


async def _check_tenant_scope(
    op: Operator, session: AsyncSession, tenant_id: str | None
) -> set[str] | None:
    scope = await rbac.audit_tenant_ids(op, session)  # None = todos
    if scope is not None and tenant_id and tenant_id not in scope:
        raise HTTPException(status_code=403, detail=f"sin acceso al audit de '{tenant_id}'")
    return scope


async def _actions_by(
    session: AsyncSession, *, kind: str | None = None, cat: str | None = None
) -> set[str]:
    """Acciones DISTINCT ya presentes en la tabla que caen en `kind` y/o
    `cat` — la clasificación vive en Python (`audit_catalog`), así que un
    filtro por tipo/categoría se traduce a un IN/NOT IN sobre esta lista en
    vez de una columna propia (sin migración, ver docs/specs/done/audit-page.md).
    Barato: `SELECT DISTINCT action` usa el índice existente y el universo
    de acciones distintas es chico (le pese a como crezca la tabla)."""
    rows = (await session.execute(select(AuditLog.action).distinct())).scalars().all()
    out = set()
    for a in rows:
        if kind is not None and audit_catalog.classify(a) != kind:
            continue
        if cat is not None and audit_catalog.category(a) != cat:
            continue
        out.add(a)
    return out


async def _filtered_stmt(
    session: AsyncSession,
    *,
    scope: set[str] | None,
    tenant_id: str | None,
    target: str | None,
    action: list[str] | None,
    category: str | None,
    operator: str | None,
    since: datetime | None,
    until: datetime | None,
    result: list[str] | None,
    request_id: str | None,
    include_reads: bool,
    include_dry_run: bool,
):
    """Filtros comunes a `GET /audit` y `GET /audit/export` — todo lo que no
    sea paginación/tope. Devuelve el `select()` ya armado, ordenado por `id`
    descendente."""
    stmt = select(AuditLog).order_by(desc(AuditLog.id))

    if tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    elif scope is not None:
        stmt = stmt.where(AuditLog.tenant_id.in_(scope))

    if target:
        stmt = stmt.where(AuditLog.target == target)
    if action:
        stmt = stmt.where(AuditLog.action.in_(action))
    if operator:
        stmt = stmt.where(AuditLog.operator == operator)
    if since:
        stmt = stmt.where(AuditLog.ts >= since)
    if until:
        stmt = stmt.where(AuditLog.ts <= until)
    if result:
        stmt = stmt.where(AuditLog.result.in_(result))
    if request_id:
        # "ver todo el mismo request" (R8): sin el filtro de ruido de
        # abajo, para que se vea también lo que hubiera quedado oculto
        # (una lectura ok que disparó la escritura, un dry-run previo).
        stmt = stmt.where(AuditLog.request_id == request_id)
        include_reads = True
        include_dry_run = True
    if not include_dry_run:
        stmt = stmt.where(~AuditLog.dry_run)

    if category:
        if category not in audit_catalog.CATEGORIES:
            raise HTTPException(status_code=422, detail=f"categoría desconocida: '{category}'")
        cat_actions = await _actions_by(session, cat=category)
        stmt = stmt.where(AuditLog.action.in_(cat_actions) if cat_actions else false())

    if not include_reads:
        read_actions = await _actions_by(session, kind="consulta")
        if read_actions:
            stmt = stmt.where(or_(~AuditLog.action.in_(read_actions), AuditLog.result != "ok"))

    return stmt


@router.get("")
async def recent(
    limit: int = Query(100, ge=1, le=200),
    before_id: int | None = Query(None, ge=1),
    tenant_id: str | None = Query(None),
    target: str | None = Query(None),
    action: list[str] | None = Query(None),
    category: str | None = Query(None),
    operator: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    result: list[str] | None = Query(None),
    request_id: str | None = Query(None),
    include_reads: bool = Query(False),
    include_dry_run: bool = Query(False),
    op: Operator = Depends(require_audit_reader),
    session: AsyncSession = Depends(get_session),
) -> dict:
    scope = await _check_tenant_scope(op, session, tenant_id)
    stmt = await _filtered_stmt(
        session,
        scope=scope,
        tenant_id=tenant_id,
        target=target,
        action=action,
        category=category,
        operator=operator,
        since=since,
        until=until,
        result=result,
        request_id=request_id,
        include_reads=include_reads,
        include_dry_run=include_dry_run,
    )
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)

    rows = (await session.execute(stmt.limit(limit))).scalars().all()
    next_before_id = rows[-1].id if len(rows) == limit else None
    return {"items": [_row(r) for r in rows], "next_before_id": next_before_id}


def _export_csv_meta(
    chain, id_range: tuple[int, int] | None, count: int, exported_at: datetime
) -> str:
    """R10/R11: el CSV arranca con la verificación de integridad (de toda la
    cadena, no sólo lo exportado — verificarla parcialmente no tiene
    sentido, el hash encadena TODO el historial) y el rango de `id`
    exportado, antes de la tabla real."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    if chain.intact:
        w.writerow(
            [
                f"Cadena de hashes: intacta ({chain.checked} registros verificados, "
                f"id {chain.first_id}-{chain.last_id})"
            ]
        )
    else:
        w.writerow([f"Cadena de hashes: ROTA a partir del registro #{chain.first_broken_id}"])
    range_txt = f"id {id_range[0]}-{id_range[1]}" if id_range else "sin registros"
    w.writerow([f"Exportado: {count} registro(s) ({range_txt}), {exported_at.isoformat()}"])
    w.writerow([])
    return buf.getvalue()


@router.get("/export")
async def export_(
    format: Literal["csv", "json"] = Query("csv"),
    tenant_id: str | None = Query(None),
    target: str | None = Query(None),
    action: list[str] | None = Query(None),
    category: str | None = Query(None),
    operator: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    result: list[str] | None = Query(None),
    request_id: str | None = Query(None),
    include_reads: bool = Query(False),
    include_dry_run: bool = Query(False),
    op: Operator = Depends(require_audit_reader),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """R10: descarga lo filtrado (mismos filtros que `GET /audit`, sin
    paginar), tope `_EXPORT_CAP` filas."""
    scope = await _check_tenant_scope(op, session, tenant_id)
    stmt = await _filtered_stmt(
        session,
        scope=scope,
        tenant_id=tenant_id,
        target=target,
        action=action,
        category=category,
        operator=operator,
        since=since,
        until=until,
        result=result,
        request_id=request_id,
        include_reads=include_reads,
        include_dry_run=include_dry_run,
    )
    rows = (await session.execute(stmt.limit(_EXPORT_CAP))).scalars().all()
    entries = [_row(r) for r in rows]
    chain = await verify_chain(session)
    ids = [r.id for r in rows]
    id_range = (min(ids), max(ids)) if ids else None
    exported_at = datetime.now(UTC)

    filters = {
        k: v
        for k, v in {
            "tenant_id": tenant_id,
            "target": target,
            "action": action,
            "category": category,
            "operator": operator,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "result": result,
            "request_id": request_id,
            "include_reads": include_reads or None,
            "include_dry_run": include_dry_run or None,
        }.items()
        if v
    }
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="audit.export",
        result="ok",
        tenant_id=tenant_id,
        detail={"count": len(entries), "format": format, "filters": filters},
    )
    await session.commit()

    stamp = exported_at.strftime("%Y-%m-%d_%H%M%S")
    if format == "json":
        meta = {
            "chain_intact": chain.intact,
            "chain_first_broken_id": chain.first_broken_id,
            "chain_checked": chain.checked,
            "chain_id_range": [chain.first_id, chain.last_id] if id_range else None,
            "exported_count": len(entries),
            "exported_id_range": list(id_range) if id_range else None,
            "exported_at": exported_at.isoformat(),
        }
        body = json.dumps({"meta": meta, "items": entries}, ensure_ascii=False, indent=2)
        media_type = "application/json; charset=utf-8"
        filename = f"pam-audit-{stamp}.json"
    else:
        body = _export_csv_meta(chain, id_range, len(entries), exported_at) + to_csv(
            entries, _EXPORT_COLUMNS
        )
        media_type = "text/csv; charset=utf-8"
        filename = f"pam-audit-{stamp}.csv"

    return Response(
        content=body,
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/summary")
async def summary(
    tenant_id: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    op: Operator = Depends(require_audit_reader),
    session: AsyncSession = Depends(get_session),
) -> dict:
    scope = await _check_tenant_scope(op, session, tenant_id)
    now = datetime.now(UTC)
    since_ = since or (now - timedelta(hours=24))
    until_ = until or now

    def scoped(stmt):
        stmt = stmt.where(AuditLog.ts >= since_, AuditLog.ts <= until_)
        if tenant_id:
            return stmt.where(AuditLog.tenant_id == tenant_id)
        if scope is not None:
            return stmt.where(AuditLog.tenant_id.in_(scope))
        return stmt

    async def count(*conditions) -> int:
        stmt = scoped(select(func.count()).select_from(AuditLog)).where(*conditions)
        return (await session.execute(stmt)).scalar_one()

    counts = {
        "passwords_revealed": await count(
            AuditLog.action.in_(
                ["account.reveal", "account.reveal.bulk", "account.reveal.history"]
            ),
            AuditLog.result == "ok",
        ),
        "recordings_downloaded": await count(
            AuditLog.action.in_(["recording.play", "recording.play.mp4"]),
            AuditLog.result == "ok",
        ),
        "credential_backups_accessed": await count(
            AuditLog.action.in_(["account.backup.reveal", "account.backup.restore"]),
            AuditLog.result == "ok",
        ),
        "access_denied": await count(AuditLog.result == "denied"),
        "errors": await count(AuditLog.result == "error"),
        "rollbacks_failed": await count(AuditLog.action.like("%.rollback_failed")),
        "failed_logins": await count(AuditLog.action == "auth.login", AuditLog.result != "ok"),
    }

    approvals_stmt = (
        select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == "pending")
    )
    if tenant_id:
        approvals_stmt = approvals_stmt.where(ApprovalRequest.tenant_id == tenant_id)
    elif scope is not None:
        approvals_stmt = approvals_stmt.where(ApprovalRequest.tenant_id.in_(scope))
    counts["pending_approvals"] = (await session.execute(approvals_stmt)).scalar_one()

    return {"since": since_.isoformat(), "until": until_.isoformat(), "counts": counts}


@router.get("/operators")
async def operators_list(
    tenant_id: str | None = Query(None),
    op: Operator = Depends(require_audit_reader),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    scope = await _check_tenant_scope(op, session, tenant_id)
    stmt = select(AuditLog.operator).distinct().order_by(AuditLog.operator)
    if tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    elif scope is not None:
        stmt = stmt.where(AuditLog.tenant_id.in_(scope))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/verify")
async def verify(
    _: Operator = Depends(require_audit_reader),
    session: AsyncSession = Depends(get_session),
) -> dict:
    v = await verify_chain(session)
    return {
        "intact": v.intact,
        "first_broken_id": v.first_broken_id,
        "checked": v.checked,
        "first_id": v.first_id,
        "last_id": v.last_id,
        "verified_at": datetime.now(UTC).isoformat(),
    }


@tenant_router.get("/audit")
async def tenant_audit(
    tenant_id: str,
    target: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    _: Operator = Depends(require_tenant_access()),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(desc(AuditLog.id))
    if target:
        stmt = stmt.where(AuditLog.target == target)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await session.execute(stmt.limit(limit))).scalars().all()
    return [_row(r) for r in rows]
