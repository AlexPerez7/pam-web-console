"""Espacio SIA (Secure Infrastructure Access): políticas, sesiones, targets,
secretos (sólo metadatos) y configuración del servicio. Sólo lectura (corte
1). Ver docs/specs/done/sia-read-only.md.

Cada lectura se audita con `_run` (mismo patrón que `app/api/setup.py`). Las
partes que vienen de servicios distintos (p. ej. secretos de DB y de VM) se
piden por separado: si una falla, la otra igual se muestra y la falla vuelve
en `warnings`; sólo si fallan todas se responde con error.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.inventory import _run
from app.core import cache
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.domain import sia
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}/sia", tags=["sia"])

_viewer = require_tenant_access(OperatorRole.viewer)

_CACHE_TTL = 30  # s, como el resto de las lecturas en vivo
_ACTIVE_SESSIONS_TTL = 15

NO_SIA_URL = "SIA no está configurado para este tenant."
SIA_FORBIDDEN = (
    "SIA no está licenciado o el service account no tiene el rol SIA Admin en este tenant."
)


def _require_sia(client: CyberArkClient) -> None:
    if not client.sia_connector_url:
        raise HTTPException(status_code=409, detail=NO_SIA_URL)


async def _cached(key: str, ttl: int, fetch: Callable[[], Awaitable[dict]]) -> dict:
    hit = await cache.get_json(key)
    if hit is not None:
        return hit
    data = await fetch()
    await cache.set_json(key, data, ttl)
    return data


def _forbidden(e: Exception) -> Exception:
    return Forbidden(SIA_FORBIDDEN) if isinstance(e, Forbidden) else e


async def _parts(parts: dict[str, Callable[[], Awaitable[Any]]]) -> tuple[dict, list[str]]:
    """Corre varias lecturas independientes. Devuelve (resultados, avisos);
    si fallan TODAS, propaga la primera falla (traducida si es un 403)."""
    names = list(parts)
    results = await asyncio.gather(*(parts[n]() for n in names), return_exceptions=True)
    ok: dict = {}
    errors: list[tuple[str, CyberArkError]] = []
    for name, res in zip(names, results, strict=True):
        if isinstance(res, CyberArkError):
            errors.append((name, res))
        elif isinstance(res, BaseException):
            raise res
        else:
            ok[name] = res
    if errors and not ok:
        raise _forbidden(errors[0][1])
    warnings = [f"{name}: {SIA_FORBIDDEN if isinstance(e, Forbidden) else e}" for name, e in errors]
    return ok, warnings


# ------------------------------------------------------------------ #
# Políticas
# ------------------------------------------------------------------ #
async def _policies_of(client: CyberArkClient, kind: str) -> list[dict]:
    """Políticas UAP si el tenant las tiene; si UAP no existe (404) o no
    devuelve nada, las de las rutas legado de `dpa`.

    Si UAP respondió (aunque vacío), un 403/404 de la ruta legado significa
    "no hay políticas de este tipo", no "SIA no licenciado": en Globex
    (UAP, sin políticas de DB) `api/adb/access-policies` da 403 y se
    mostraba como falta de licencia (2026-10-07)."""
    try:
        uap: list | None = await client.list_uap_policies("VM" if kind == "vm" else "DB")
    except (
        CyberArkError
    ):  # 404 = el tenant no tiene UAP; cualquier otra falla, igual se prueba legado
        uap = None
    if uap:
        return [sia.uap_policy_row(p, kind) for p in uap]
    try:
        legacy = await (
            client.list_sia_vm_policies() if kind == "vm" else client.list_sia_db_policies()
        )
    except (Forbidden, NotFound):
        if uap is None:
            raise
        return []
    return [sia.legacy_policy_row(p, kind) for p in legacy]


@router.get("/policies")
async def list_policies(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    holder: dict = {}
    async with tenant_client(tenant_id, session) as (_, client):
        _require_sia(client)

        async def fetch() -> dict:
            ok, warnings = await _parts(
                {
                    "VM": lambda: _policies_of(client, "vm"),
                    "Bases de datos": lambda: _policies_of(client, "db"),
                }
            )
            rows = [r for part in ok.values() for r in part]
            rows.sort(key=lambda r: (r["name"] or "").lower())
            return {"rows": rows, "warnings": warnings}

        async def rows_fn() -> list:
            data = await _cached(f"sia:policies:{tenant_id}", _CACHE_TTL, fetch)
            holder["warnings"] = data["warnings"]
            return data["rows"]

        rows = await _run(session, op, tenant_id, "sia.policies.list", rows_fn())
    return {
        "tenant": tenant_id,
        "count": len(rows),
        "policies": rows,
        "warnings": holder.get("warnings", []),
    }


@router.get("/policies/{policy_id}")
async def get_policy(
    tenant_id: str,
    policy_id: str,
    source: Literal["uap", "legacy"] = Query(...),
    kind: Literal["vm", "db"] = Query(...),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        _require_sia(client)

        async def fetch() -> dict:
            try:
                if source == "uap":
                    return sia.uap_policy_detail(await client.get_uap_policy(policy_id), kind)
                return sia.legacy_policy_detail(await client.get_sia_policy(kind, policy_id), kind)
            except Forbidden as e:
                raise _forbidden(e) from e

        detail = await _run(
            session,
            op,
            tenant_id,
            "sia.policy.get",
            fetch(),
            target=policy_id,
            extra={"source": source, "kind": kind},
        )
    return {"tenant": tenant_id, "policy": detail}


# ------------------------------------------------------------------ #
# Sesiones
# ------------------------------------------------------------------ #
def _sessions_search(status: str, now: datetime | None = None) -> str:
    if status == "active":
        return "sessionStatus IN Active"
    since = (now or datetime.now(UTC)) - timedelta(hours=24)
    return f"startTime GE {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"


@router.get("/sessions")
async def list_sessions(
    tenant_id: str,
    status: Literal["active", "recent"] = Query("active"),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        _require_sia(client)

        async def fetch() -> dict:
            try:
                raw, _total = await client.list_sm_sessions(_sessions_search(status))
            except Forbidden as e:
                raise _forbidden(e) from e
            rows = [sia.session_row(s) for s in raw if sia.is_sia_session(s)]
            rows.sort(key=lambda r: r["startTime"] or "", reverse=True)
            return {"rows": rows}

        async def rows_fn() -> list:
            ttl = _ACTIVE_SESSIONS_TTL if status == "active" else _CACHE_TTL
            data = await _cached(f"sia:sessions:{status}:{tenant_id}", ttl, fetch)
            return data["rows"]

        rows = await _run(
            session, op, tenant_id, "sia.sessions.list", rows_fn(), extra={"status": status}
        )
    return {"tenant": tenant_id, "status": status, "count": len(rows), "sessions": rows}


@router.get("/sessions/{session_id}")
async def get_session_detail(
    tenant_id: str,
    session_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    holder: dict = {}
    async with tenant_client(tenant_id, session) as (_, client):
        _require_sia(client)

        async def fetch() -> dict:
            try:
                raw = await client.get_sm_session(session_id)
            except Forbidden as e:
                raise _forbidden(e) from e
            try:
                acts = await client.list_sm_session_activities(session_id)
                holder["activities"] = [sia.activity_row(a) for a in acts]
            except CyberArkError as e:
                holder["activities"] = []
                holder["warning"] = f"No se pudieron leer las actividades: {e}"
            return sia.session_row(raw)

        row = await _run(session, op, tenant_id, "sia.session.get", fetch(), target=session_id)
    return {
        "tenant": tenant_id,
        "session": row,
        "activities": holder.get("activities", []),
        "warnings": [holder["warning"]] if "warning" in holder else [],
    }


# ------------------------------------------------------------------ #
# Targets, secretos y configuración
# ------------------------------------------------------------------ #
@router.get("/strong-accounts")
async def list_strong_accounts(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """La página "Strong accounts" del portal (Setup > Deployment): cuentas
    fuertes de VM con sus target sets y de DB con sus bases. Sólo
    metadatos, armados por lista blanca (`app.domain.sia`). Sin caché: los
    secretos no pasan por Redis, ni siquiera sus metadatos."""
    holder: dict = {}
    async with tenant_client(tenant_id, session) as (_, client):
        _require_sia(client)

        async def rows_fn() -> list:
            ok, warnings = await _parts(
                {
                    "Strong accounts de VM": client.list_sia_vm_secrets,
                    "Target sets de VM": client.list_sia_target_sets,
                    "Strong accounts de bases de datos": client.list_sia_db_secrets,
                    "Bases de datos": client.list_sia_db_resources,
                }
            )
            holder["warnings"] = warnings
            vm_secrets = ok.get("Strong accounts de VM", [])
            # Safe/cuenta de las PCloudAccount: la lista no los trae, se
            # resuelven una por una como hace el portal (2026-10-07).
            pcloud_ids = [
                sia.secret_id(x)
                for x in vm_secrets
                if sia.secret_type(x) == "PCloudAccount" and sia.secret_id(x)
            ]
            found = await asyncio.gather(*(client.get_sia_pcloud_account(i) for i in pcloud_ids))
            data = sia.strong_accounts(
                vm_secrets,
                ok.get("Target sets de VM", []),
                ok.get("Strong accounts de bases de datos", []),
                ok.get("Bases de datos", []),
                dict(zip(pcloud_ids, found, strict=True)),
            )
            holder["data"] = data
            return data["vm"] + data["db"]

        rows = await _run(session, op, tenant_id, "sia.strong_accounts.list", rows_fn())
    data = holder.get("data") or {}
    return {
        "tenant": tenant_id,
        "count": len(rows),
        "vm": data.get("vm", []),
        "db": data.get("db", []),
        "databases": data.get("databases", []),
        "targetSetsWithoutAccount": data.get("targetSetsWithoutAccount", []),
        "databasesWithoutAccount": data.get("databasesWithoutAccount", []),
        "warnings": holder.get("warnings", []),
    }


@router.get("/settings")
async def get_settings(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        _require_sia(client)

        async def fetch() -> dict:
            try:
                return {"rows": sia.settings_groups(await client.get_sia_settings())}
            except Forbidden as e:
                raise _forbidden(e) from e

        async def rows_fn() -> list:
            data = await _cached(f"sia:settings:{tenant_id}", _CACHE_TTL, fetch)
            return data["rows"]

        groups = await _run(session, op, tenant_id, "sia.settings.get", rows_fn())
    return {"tenant": tenant_id, "count": len(groups), "groups": groups}
