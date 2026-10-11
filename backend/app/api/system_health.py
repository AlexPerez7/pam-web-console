"""
System Health — equivalente cross-tenant de la página "System Health" del
PVWA. Ver docs/specs/done/system-health.md.

Los números de "Managed Accounts"/"Concurrent Sessions"/instancias
conectadas salen de endpoints que PAM Web Console YA usa y confirma en otros lados
(list_accounts, live_sessions, ComponentsMonitoringDetails vía
`analysis.analyze_components`) — no de los campos (no confirmados) de
`ComponentsMonitoringSummary.Components`. El único dato nuevo es el estado
del Vault (`ComponentsMonitoringSummary.Vaults`, campos confirmados vía el
código fuente de psPAS).

El fan-out cross-tenant (`GET /system-health`) sigue el mismo patrón de
aislamiento que `app/api/search.py`: un `CyberArkClient` nuevo por tenant,
creado/cerrado dentro de su propia tarea, tolerante a que un tenant falle
sin romper a los demás. El endpoint por tenant (`GET /tenants/{id}
/system-health`) en cambio usa `tenant_client`, que sí levanta HTTPException
ante un tenant caído — igual que el resto de los endpoints del proyecto.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.config import get_settings
from app.core import audit, cache, rbac
from app.core.deps import current_operator, require_tenant_access
from app.core.secrets import SecretStoreError, get_secret
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, CyberArkError, build_client
from app.cyberark.auth import AuthError
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, Tenant

router = APIRouter(tags=["system-health"])

_viewer = require_tenant_access(OperatorRole.viewer)

_COMPONENT_IDS = ["PVWA", "SessionManagement", "CPM", "AIM"]
_COMPONENT_LABELS = {
    "PVWA": "Web Portal",
    "SessionManagement": "PSM y PSM for SSH",
    "CPM": "CPM",
    "AIM": "AIM / Credential Provider",
}


def _cache_key(tenant_id: str) -> str:
    return f"pam:system-health:{tenant_id}"


def _ok(v):
    """None si el resultado del gather fue una excepción — degrada ese
    campo puntual en vez de tirar todo el snapshot del tenant (mismo
    criterio que dashboards._ok: un endpoint puntual caído no debe tapar
    los demás, que sí puedan responder)."""
    return None if isinstance(v, BaseException) else v


def _instance_row(inst: dict) -> dict:
    """Una instancia de un componente (mismos campos que `GET /components`
    de `app.api.inventory`), para el detalle de System Health."""
    return {
        "ip": inst.get("ComponentIP") or inst.get("IP"),
        "version": inst.get("ComponentVersion"),
        "user": inst.get("ComponentUserName") or inst.get("ComponentUser"),
        "connected": bool(inst.get("IsLoggedOn")),
        "last_logon": inst.get("LastLogonDate") or inst.get("LastConnectionDate"),
    }


async def _compute_snapshot(client: CyberArkClient) -> dict:
    """Calcula el snapshot de un tenant ya autenticado. Cada lectura
    degrada en forma independiente ante una excepción — un endpoint que
    falla (404/403/lo que sea) no debe tapar el resto de los datos del
    tenant."""
    accounts, live, monitoring = await asyncio.gather(
        client.list_accounts(),
        client.live_sessions(),
        client.components_monitoring_summary(),
        return_exceptions=True,
    )
    component_results = await asyncio.gather(
        *(client.component_details(c) for c in _COMPONENT_IDS), return_exceptions=True
    )
    accounts = _ok(accounts)
    live = _ok(live)
    monitoring = _ok(monitoring)
    component_lists = [_ok(c) for c in component_results]

    comp_summary = analysis.analyze_components(
        dict(zip(_COMPONENT_IDS, component_lists, strict=True))
    )
    vaults = (monitoring or {}).get("Vaults") if monitoring else None
    if isinstance(vaults, dict):
        vaults = [vaults]
    primary = next(
        (v for v in (vaults or []) if isinstance(v, dict) and v.get("Role") != "DR"), None
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "cached": False,
        "vault": (
            {
                "available": True,
                "connected": bool(primary.get("IsLoggedOn")),
                "ip": primary.get("IP"),
            }
            if primary
            else {"available": False}
        ),
        "components": [
            {
                "id": cid,
                "name": _COMPONENT_LABELS.get(cid, cid),
                "available": comp_summary[cid]["available"],
                "connected": comp_summary[cid].get("connected", 0),
                "total": comp_summary[cid].get("total_instances", 0),
                "instances": [_instance_row(i) for i in (instances or []) if isinstance(i, dict)],
            }
            for cid, instances in zip(_COMPONENT_IDS, component_lists, strict=True)
        ],
        "accounts_total": len(accounts) if accounts is not None else None,
        "sessions_active": len(live) if live is not None else None,
    }


async def _cached_snapshot_via_tenant_client(
    tenant_id: str, session: AsyncSession, *, refresh: bool
) -> dict:
    """Para el endpoint por tenant: usa tenant_client (levanta HTTPException
    ante un tenant caído, igual que el resto del proyecto)."""
    key = _cache_key(tenant_id)
    if not refresh:
        cached = await cache.get_json(key)
        if cached is not None:
            return {**cached, "cached": True}
    async with tenant_client(tenant_id, session) as (_, client):
        data = await _compute_snapshot(client)
    await cache.set_json(key, data, ttl=get_settings().system_health_cache_ttl)
    return data


def _friendly_error(e: Exception) -> str:
    """Traduce las excepciones típicas de un tenant caído a un mensaje que
    un operador pueda accionar sin leer un stacktrace — esta página la
    mira el equipo para saber si algo anda mal, no para debuggear PAM Web Console."""
    if isinstance(e, AuthError):
        msg = str(e)
        if "access_denied" in msg or "invalid client" in msg.lower():
            return "credenciales inválidas o rechazadas por Identity"
        return "no se pudo autenticar contra CyberArk"
    if isinstance(e, SecretStoreError):
        return "no se encontraron credenciales para este tenant en el secret store"
    if isinstance(e, CyberArkError):
        return str(e)
    return f"error inesperado consultando el tenant ({type(e).__name__})"


async def _snapshot_for_fanout(tenant: Tenant, *, refresh: bool) -> dict:
    """Para el fan-out cross-tenant: nunca lanza — devuelve el snapshot
    ({'generated_at', ...}) o {'error': '...'}. Mismo criterio de
    aislamiento que search._search_one (client nuevo por tenant, cerrado
    en el finally)."""
    key = _cache_key(tenant.id)
    if not refresh:
        cached = await cache.get_json(key)
        if cached is not None:
            return {**cached, "cached": True}

    max_conc = get_settings().cyberark_max_concurrency
    try:
        secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
    except SecretStoreError as e:
        return {"error": _friendly_error(e)}

    client = build_client(
        tenant,
        secret,
        max_concurrency=max_conc,
        token_cache_seconds=get_settings().cyberark_token_cache_seconds,
    )
    try:
        await client.authenticate()
        data = await _compute_snapshot(client)
    except (AuthError, CyberArkError) as e:
        return {"error": _friendly_error(e)}
    except Exception as e:  # un tenant no debe romper el fan-out
        return {"error": _friendly_error(e)}
    finally:
        await client.close()

    await cache.set_json(key, data, ttl=get_settings().system_health_cache_ttl)
    return data


@router.get("/tenants/{tenant_id}/system-health")
async def tenant_system_health(
    tenant_id: str,
    refresh: bool = Query(False),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await _cached_snapshot_via_tenant_client(tenant_id, session, refresh=refresh)
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="system_health.view",
        result="ok",
        tenant_id=tenant_id,
    )
    await session.commit()
    return {"tenant": tenant_id, **data}


@router.get("/system-health")
async def system_health_all(
    refresh: bool = Query(False),
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    allowed = await rbac.accessible_tenant_ids(op, session)
    tenants = [
        t
        for t in (
            await session.execute(
                select(Tenant)
                .where(
                    Tenant.enabled.is_(True),
                    Tenant.deployment == Deployment.privilege_cloud,
                )
                .order_by(Tenant.id)
            )
        )
        .scalars()
        .all()
        if allowed is None or t.id in allowed
    ]

    outcomes = await asyncio.gather(*(_snapshot_for_fanout(t, refresh=refresh) for t in tenants))

    rows: list[dict] = []
    errors: list[dict] = []
    for tenant, outcome in zip(tenants, outcomes, strict=True):
        if "error" not in outcome:
            rows.append({"tenant": tenant.id, "tenant_name": tenant.name, **outcome})
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="system_health.view",
                result="ok",
                tenant_id=tenant.id,
            )
        else:
            errors.append(
                {"tenant": tenant.id, "tenant_name": tenant.name, "error": outcome["error"]}
            )
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="system_health.view",
                result="error",
                tenant_id=tenant.id,
                detail={"error": outcome["error"]},
            )

    await session.commit()
    return {"generated_at": datetime.now(UTC).isoformat(), "tenants": rows, "errors": errors}
