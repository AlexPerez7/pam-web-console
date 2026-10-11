"""
Dashboard por tenant: KPIs + series para gráficos.

En un cache miss se calcula a partir de lecturas reales contra CyberArk
(Accounts, Safes, Platforms, savedFilters de compliance, salud de
componentes, sesiones PSM en vivo), en paralelo. El resultado se cachea
en Redis por `PAM_DASHBOARD_CACHE_TTL` segundos. `?refresh=1` fuerza el
recálculo. `dashboard.view` se audita en TODOS los requests (también en
cache hit). La lógica de métricas vive en `app.analysis` (portada de ITR).

Sesiones activas: sólo el conteo EN VIVO al momento del cálculo (mismo
patrón que el resto de las métricas, cacheado igual). Sin tendencia
histórica — eso necesitaría muestreo periódico (jobs en background), fuera
de alcance por ahora (ver docs/PLAN.md §7 Fase 1, "dashboard v3").
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.config import get_settings
from app.core import audit, cache, compliance_exemptions
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["dashboards"])

_viewer = require_tenant_access(OperatorRole.viewer)

_TOP_PLATFORMS = 12
_COMPLIANCE_FILTERS = [
    "PolicyFailures",
    "DisabledPasswordByUser",
    "DisabledPasswordByCPM",
    "Locked",
]
_COMPONENT_IDS = ["PVWA", "SessionManagement", "CPM", "AIM"]


def _series(counter, *, order: list | None = None, top: int | None = None) -> list[dict]:
    """Counter/dict -> [{"label", "value"}]. `order` fija el orden; `top` recorta."""
    if order is not None:
        items = [(str(k), int(counter.get(k, 0))) for k in order]
    else:
        items = [(str(k), int(v)) for k, v in counter.most_common()]
    if top is not None:
        items = items[:top]
    return [{"label": label, "value": value} for label, value in items]


def _ok(v):
    """None si el resultado del gather fue una excepción (degradación parcial)."""
    return None if isinstance(v, BaseException) else v


async def _compute(tenant_id: str, session: AsyncSession) -> dict:
    """Cache miss: pega a CyberArk y arma la respuesta. Las 3 lecturas núcleo
    (accounts/safes/platforms) que fallan abortan; compliance y componentes
    degradan a vacío."""
    async with tenant_client(tenant_id, session) as (tenant, client):
        results = await asyncio.gather(
            client.list_accounts(),
            client.list_safes(),
            client.list_platforms(),
            client.live_sessions(),
            *(client.saved_filter_accounts(f) for f in _COMPLIANCE_FILTERS),
            *(client.component_details(c) for c in _COMPONENT_IDS),
            return_exceptions=True,
        )
        stale_days = tenant.compliance_stale_days
    accounts, safes, platforms = results[0], results[1], results[2]
    for core in (accounts, safes, platforms):
        if isinstance(core, BaseException):
            raise core
    live_sessions = _ok(results[3])
    n_filters = len(_COMPLIANCE_FILTERS)
    filter_lists = results[4 : 4 + n_filters]
    component_lists = results[4 + n_filters :]

    exempt = await compliance_exemptions.exempt_ids(session, tenant_id)
    platform_days = analysis.platform_policy_days(platforms)
    acc = analysis.analyze_accounts(accounts, stale_days, platform_days, exempt)
    saf = analysis.analyze_safes(safes)
    plat = analysis.analyze_platforms(platforms, acc["by_platform"])
    by_month = analysis.accounts_by_month(accounts)

    # PolicyFailures se necesita como SET de ids (no sólo el conteo) para
    # que compliance_breakdown pueda deduplicar contra disabled_stale/
    # active_overdue cuenta por cuenta, en vez de sumar conteos que pueden
    # solaparse (ver docstring de compliance_breakdown).
    policy_failure_rows = _ok(filter_lists[0])
    policy_failure_ids = (
        {a.get("id") for a in policy_failure_rows} if policy_failure_rows else set()
    )
    filter_counts = {
        name: len(_ok(rows) or [])
        for name, rows in zip(_COMPLIANCE_FILTERS, filter_lists, strict=True)
    }
    compliance_partial = any(_ok(r) is None for r in filter_lists)
    comp_summary = analysis.analyze_components(
        {cid: _ok(rows) for cid, rows in zip(_COMPONENT_IDS, component_lists, strict=True)}
    )
    cpm = analysis.analyze_cpm_errors(accounts, acc["failed_ids"])
    compliance = analysis.compliance_breakdown(
        accounts,
        policy_failure_ids=policy_failure_ids,
        filter_counts=filter_counts,
        platform_days=platform_days,
        default_stale_days=stale_days,
        exempt_ids=exempt,
    )
    sessions = analysis.analyze_sessions(live_sessions)

    components_disconnected = sum(
        c.get("disconnected", 0) for c in comp_summary.values() if c.get("available")
    )
    auto_pct = round(acc["auto_managed"] / acc["total"] * 100, 1) if acc["total"] else 0.0

    return {
        "tenant": tenant_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "cached": False,
        "compliance_partial": compliance_partial,
        "kpis": {
            "accounts_total": acc["total"],
            "accounts_failed": acc["failed"],
            "accounts_never_changed": acc["never_changed"],
            "accounts_no_platform": acc["no_platform"],
            "auto_managed_pct": auto_pct,
            "compliance_pct": compliance["compliant_pct"],
            "safes_total": saf["total"],
            "safes_no_cpm": saf["no_cpm_count"],
            "safes_olac": saf["olac_count"],
            "platforms_total": plat["total"],
            "platforms_active": plat["active"],
            "platforms_no_periodic_rotation": plat["no_periodic_rotation"],
            "components_disconnected": components_disconnected,
            "sessions_active": sessions["total"],
            "sessions_available": sessions["available"],
        },
        "charts": {
            "accounts_by_platform": _series(acc["by_platform"], top=_TOP_PLATFORMS),
            "password_age": _series(acc["age_change"], order=analysis.AGE_BUCKET_LABELS),
            "management_status": _series(acc["status_counts"]),
            "safe_retention": _series(saf["retention_days_counts"]),
            "accounts_by_month": [{"label": label, "value": value} for label, value in by_month],
            "compliance_breakdown": [
                {"label": k, "value": v} for k, v in compliance["categories"].items()
            ],
            "cpm_errors": [
                {"label": "En reintento", "value": cpm["active_retry"]},
                *_series(cpm["by_reason"]),
            ],
            "sessions_by_protocol": _series(sessions["by_protocol"]),
            "components": [
                {
                    "label": analysis.COMPONENT_NAMES.get(cid, cid),
                    "connected": comp_summary[cid].get("connected", 0),
                    "total": comp_summary[cid].get("total_instances", 0),
                    "available": comp_summary[cid].get("available", False),
                }
                for cid in _COMPONENT_IDS
            ],
        },
    }


@router.get("/dashboard")
async def dashboard(
    tenant_id: str,
    refresh: bool = Query(False),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    key = f"pam:dashboard:{tenant_id}"
    data: dict | None = None
    if not refresh:
        data = await cache.get_json(key)
        if data is not None:
            data["cached"] = True

    if data is None:
        try:
            data = await _compute(tenant_id, session)
        except (NotFound, Forbidden) as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="dashboard.view",
                result="error",
                tenant_id=tenant_id,
                detail={"error": str(e)},
            )
            await session.commit()
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
        except CyberArkError as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="dashboard.view",
                result="error",
                tenant_id=tenant_id,
                detail={"error": str(e)},
            )
            await session.commit()
            raise HTTPException(status_code=502, detail=str(e))
        await cache.set_json(key, data, get_settings().dashboard_cache_ttl)

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="dashboard.view",
        result="ok",
        tenant_id=tenant_id,
        detail={
            "cached": data["cached"],
            "accounts": data["kpis"]["accounts_total"],
            "compliance_pct": data["kpis"]["compliance_pct"],
        },
    )
    await session.commit()
    return data
