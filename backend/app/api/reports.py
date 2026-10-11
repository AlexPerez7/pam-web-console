"""Sección de Reportes — lecturas masivas de un tenant, auditadas.

`GET /reports`                              catálogo
`GET /tenants/{id}/reports/{name}`          corre y devuelve {columns, rows, meta}
`GET /tenants/{id}/reports/{name}/export`   lo mismo como archivo (csv|json|xlsx)
`POST /tenants/{id}/reports/{name}/run-job` reportes "heavy" (Batch 4): encola un job
`GET /tenants/{id}/reports/{name}/jobs/{job_id}`  estado/resultado del job

Ver docs/specs/reportes.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from arq.jobs import Job as ArqJob
from arq.jobs import JobStatus
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.alero import AleroError
from app.config import get_settings
from app.core import audit, cache
from app.core.arq import get_arq_pool
from app.core.csv_export import epoch_iso, flag, to_csv
from app.core.deps import current_operator, require_tenant_access
from app.core.tenants import alero_client, tenant_client
from app.cyberark import CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole, Tenant
from app.reports import REGISTRY
from app.reports import license_candidates as license_candidates_report
from app.reports.base import ReportResult, result_to_dict

catalog_router = APIRouter(prefix="/reports", tags=["reports"])
router = APIRouter(prefix="/tenants/{tenant_id}", tags=["reports"])

_viewer = require_tenant_access(OperatorRole.viewer)


@catalog_router.get("")
async def list_reports(_: Operator = Depends(current_operator)) -> list[dict]:
    return [r.catalog() for r in REGISTRY.values()]


def _params_for(name: str, request: Request) -> dict[str, str]:
    report = REGISTRY[name]
    return {
        p.key: request.query_params[p.key]
        for p in report.params
        if p.key in request.query_params and request.query_params[p.key] != ""
    }


def _cell_extractor(kind: str, key: str) -> Callable[[dict], Any]:
    if kind == "date":
        return lambda row: epoch_iso(row.get(key))
    if kind == "bool":
        return lambda row: flag(row.get(key))
    return lambda row: row.get(key)


def _cache_key(tenant_id: str, name: str, params: dict) -> str:
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    return f"pam:report:{tenant_id}:{name}:{h}"


async def _compute(tenant_id: str, name: str, params: dict, session: AsyncSession) -> ReportResult:
    report = REGISTRY[name]
    async with tenant_client(tenant_id, session) as (tenant, client):
        result = await report.run(client, report.run_params(tenant, params))

    # license-candidates: si el tenant tiene Remote Access (Alero)
    # configurado, reemplazar el status/login/candidato de las filas
    # ExtUser (vendors) con datos reales de Alero -- ver
    # app.reports.license_candidates.enrich_with_alero. report.run() no
    # puede hacerlo directo: sólo recibe un CyberArkClient, Alero es una
    # API separada con credenciales propias. Best-effort: si Alero falla
    # (secret store sin credenciales, tenant caído), el reporte se sigue
    # devolviendo con el login de Identity como respaldo, mismo criterio
    # que el script de PowerShell original.
    if name == "license-candidates" and tenant.alero_tenant_id:
        inactive_days = int(params.get("inactive_days") or 30)
        try:
            async with alero_client(tenant_id, session) as alero:
                await license_candidates_report.enrich_with_alero(
                    result, alero, inactive_days=inactive_days
                )
        except (AleroError, HTTPException):
            result.meta["alero_enriched"] = False

    return result


async def _run_report(
    tenant_id: str,
    name: str,
    request: Request,
    op: Operator,
    session: AsyncSession,
    *,
    refresh: bool,
    audit_extra: dict | None = None,
) -> dict:
    if name not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"reporte '{name}' no existe")
    report = REGISTRY[name]

    params = _params_for(name, request)
    missing = [p.key for p in report.params if p.required and p.key not in params]
    if missing:
        raise HTTPException(status_code=422, detail=f"faltan parámetros: {', '.join(missing)}")

    key = _cache_key(tenant_id, name, params)
    data: dict | None = None
    if not refresh:
        data = await cache.get_json(key)
        if data is not None:
            data["cached"] = True

    # "heavy" (Batch 4): no corre síncrono en un request HTTP -- si no hay
    # un resultado ya cacheado (de un job ya corrido vía POST .../run-job),
    # no hay nada que devolver acá.
    if data is None and report.heavy:
        raise HTTPException(
            status_code=501,
            detail="este reporte corre como job en background -- use POST .../run-job",
        )

    if data is None:
        try:
            result = await _compute(tenant_id, name, params, session)
        except (NotFound, Forbidden) as e:
            await _audit(session, op, request, name, tenant_id, "error", {"error": str(e)})
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
        except CyberArkError as e:
            await _audit(session, op, request, name, tenant_id, "error", {"error": str(e)})
            raise HTTPException(status_code=502, detail=str(e))
        data = result_to_dict(result)
        data["cached"] = False
        data["generated_at"] = datetime.now(UTC).isoformat()
        await cache.set_json(key, data, get_settings().report_cache_ttl)

    await _audit(
        session,
        op,
        request,
        name,
        tenant_id,
        "ok",
        {
            "params": params,
            "row_count": len(data["rows"]),
            "cached": data["cached"],
            **(audit_extra or {}),
        },
    )
    return data


async def _audit(
    session: AsyncSession,
    op: Operator,
    request: Request,
    name: str,
    tenant_id: str,
    result: str,
    detail: dict,
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action=f"report.{name}",
        result=result,
        tenant_id=tenant_id,
        target=name,
        source_ip=request.client.host if request.client else None,
        detail=detail,
    )
    await session.commit()


@router.get("/reports/{name}")
async def run_report(
    tenant_id: str,
    name: str,
    request: Request,
    refresh: bool = Query(False),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _run_report(tenant_id, name, request, op, session, refresh=refresh)


@router.post("/reports/{name}/run-job")
async def run_report_job(
    tenant_id: str,
    name: str,
    request: Request,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Encola un reporte "heavy" (Batch 4: `target-inactivity`,
    `account-activity`) como job ARQ en vez de correrlo en el request --
    devuelve `{job_id}` para pollear con
    `GET .../reports/{name}/jobs/{job_id}`. Ver `app.jobs.reports.
    run_heavy_report`."""
    if name not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"reporte '{name}' no existe")
    report = REGISTRY[name]
    if not report.heavy:
        raise HTTPException(
            status_code=422, detail="este reporte no es 'heavy' -- use GET .../reports/{name}"
        )

    params = _params_for(name, request)
    missing = [p.key for p in report.params if p.required and p.key not in params]
    if missing:
        raise HTTPException(status_code=422, detail=f"faltan parámetros: {', '.join(missing)}")

    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "run_heavy_report", tenant_id, name, params, op.username, op.role.value
    )
    if job is None:
        raise HTTPException(status_code=503, detail="no se pudo encolar el job (¿Redis caído?)")

    await _audit(session, op, request, name, tenant_id, "ok", {"params": params, "queued": True})
    return {"job_id": job.job_id}


@router.get("/reports/{name}/jobs/{job_id}")
async def report_job_status(
    tenant_id: str,
    name: str,
    job_id: str,
    op: Operator = Depends(_viewer),
) -> dict:
    """Consulta el estado de un job encolado por `POST .../run-job`. No
    audita cada poll (sería ruido) -- el job en sí ya se auditó al
    encolarse y vuelve a auditarse al terminar
    (`app.jobs.reports.run_heavy_report`)."""
    pool = await get_arq_pool()
    job = ArqJob(job_id, pool)
    status = await job.status()

    if status == JobStatus.not_found:
        raise HTTPException(status_code=404, detail="job no encontrado (puede haber expirado)")
    if status != JobStatus.complete:
        return {"status": status.value}

    info = await job.result_info()
    if info is None:
        raise HTTPException(status_code=404, detail="resultado del job no encontrado")
    if not info.success:
        raise HTTPException(status_code=502, detail=f"el reporte falló: {info.result}")

    result = info.result
    result["status"] = "complete"
    return result


@router.get("/reports/{name}/export")
async def export_report(
    tenant_id: str,
    name: str,
    request: Request,
    fmt: str = Query("csv", alias="format"),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    report = REGISTRY.get(name)
    if report is None:
        raise HTTPException(status_code=404, detail=f"reporte '{name}' no existe")
    if fmt not in report.formats:
        raise HTTPException(
            status_code=422, detail=f"format debe ser uno de: {', '.join(report.formats)}"
        )
    data = await _run_report(
        tenant_id, name, request, op, session, refresh=False, audit_extra={"format": fmt}
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"pam-{tenant_id}-{name}-{stamp}.{fmt}"

    if fmt == "xlsx" and report.xlsx is not None:
        tenant = await session.get(Tenant, tenant_id)
        tenant_name = (tenant.client or tenant.name) if tenant else tenant_id
        content = await asyncio.to_thread(report.xlsx, data, tenant_name)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"content-disposition": f'attachment; filename="{filename}"'},
        )

    if fmt == "json":
        return Response(
            content=json.dumps(data, default=str, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="{filename}"'},
        )

    cols = [(c["label"], _cell_extractor(c["kind"], c["key"])) for c in data["columns"]]
    return Response(
        content=to_csv(data["rows"], cols),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )
