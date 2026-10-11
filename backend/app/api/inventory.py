"""
Inventario en vivo de un tenant. Lecturas contra la API de CyberArk, cada
una auditada con la identidad real del operador. Fase 1 expande esto
(filtros, paginación, más recursos, el frontend). Fase 2 agrega escritura.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import audit, cache, inventory_sync
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.domain.accounts import (
    ACCOUNT_STATUS_FLAGS,
    account_matches_remote_machines,
    account_matches_terms,
    account_status_flags,
    parse_search_terms,
)
from app.domain.member_search import (
    merge_member_candidates,
    redrock_user_search_sql,
    sanitize_search_term,
)
from app.domain.platform_config import parse_platform_package
from app.domain.platforms import find_target_platform
from app.domain.safes import list_cpm_names, suggest_safe_defaults
from app.models import AccountSnapshot, Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["inventory"])

_viewer = require_tenant_access(OperatorRole.viewer)
_tenant_admin = require_tenant_access(OperatorRole.tenant_admin)

_COMPONENT_IDS = ["PVWA", "SessionManagement", "CPM", "AIM"]
_COMPONENT_NAMES = {
    "PVWA": "PVWA (Portal)",
    "SessionManagement": "PSM / Session Management",
    "CPM": "CPM",
    "AIM": "AIM / Credential Provider",
}


async def _run(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    action: str,
    coro,
    *,
    target: str | None = None,
    extra: dict | None = None,
):
    """Ejecuta una lectura contra CyberArk y la audita (ok o error)."""
    try:
        result = await coro
    except (NotFound, Forbidden) as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action=action,
            result="error",
            tenant_id=tenant_id,
            target=target,
            detail={"error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
    except CyberArkError as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action=action,
            result="error",
            tenant_id=tenant_id,
            target=target,
            detail={"error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    if isinstance(result, tuple) and len(result) == 2:
        rows, total = result
        detail: dict = {"count": total, "returned": len(rows)}
    elif isinstance(result, list):
        detail = {"count": len(result)}
    else:
        detail = {}
    if extra:
        detail |= extra
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action=action,
        result="ok",
        tenant_id=tenant_id,
        target=target,
        detail=detail,
    )
    await session.commit()
    return result


async def _cached_rows(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    action: str,
    cache_key: str,
    ttl: int,
    fetch_fn: Callable[[], Awaitable[list]],
) -> list:
    """Lista completa cacheada en Redis (TTL corto): evita repetir una
    lectura pesada de CyberArk (paginada o de payload grande) en cada
    apertura de un wizard o tab que sólo necesita el listado completo."""
    cached = await cache.get_json(cache_key)
    if cached is not None:
        rows = cached["rows"]
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action=action,
            result="ok",
            tenant_id=tenant_id,
            detail={"count": len(rows), "cached": True},
        )
        await session.commit()
        return rows
    rows = await _run(session, op, tenant_id, action, fetch_fn())
    await cache.set_json(cache_key, {"rows": rows}, ttl)
    return rows


async def safes_rows_cached(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    client: CyberArkClient,
    action: str,
) -> list:
    """Todas las Safes del tenant. Compartido por `/safe-names` y
    `/safes/defaults`: sin esto, cada carga del filtro de Cuentas o del
    wizard de alta repagina `/API/Safes` contra CyberArk. `safe.create`
    invalida la key al escribir."""
    return await _cached_rows(
        session,
        op,
        tenant_id,
        action,
        f"pam:safes:{tenant_id}",
        get_settings().safes_cache_ttl,
        client.list_safes,
    )


async def platforms_rows_cached(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    client: CyberArkClient,
    action: str,
) -> list:
    """Todas las plataformas del tenant. `/API/Platforms` devuelve el detalle
    completo de política del CPM de cada una (payload pesado) y `GET
    /platforms` lo pedía en vivo sin caché — el paso "Tipo de sistema" del
    wizard de `account.create` (y el tab Plataformas) lo golpeaban en cada
    apertura. De paso, le suma a cada fila `psmServerId` (desde `list_target_
    platforms()` — "Get target platforms", la misma que resuelve el ID
    numérico para `platform.psm.update`) para pintarlo directo en la grilla
    sin un fetch por plataforma; si esa llamada falla (permiso faltante,
    etc.) se degrada sin `psmServerId`, no rompe el listado."""

    async def _fetch() -> list:
        rows = await client.list_platforms()
        try:
            targets = await client.list_target_platforms()
        except CyberArkError:
            targets = []
        psm_by_platform_id = {
            str(t.get("PlatformID")): (t.get("PrivilegedSessionManagement") or {}).get(
                "PSMServerId"
            )
            for t in targets
            if isinstance(t, dict)
        }
        for r in rows:
            if isinstance(r, dict):
                gid = str((r.get("general") or {}).get("id") or "")
                r["psmServerId"] = psm_by_platform_id.get(gid)
        return rows

    return await _cached_rows(
        session,
        op,
        tenant_id,
        action,
        f"pam:platforms:{tenant_id}",
        get_settings().platforms_cache_ttl,
        _fetch,
    )


@router.get("/server")
async def server(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        info = await _run(session, op, tenant_id, "tenant.server_info", client.server_info())
    return {"tenant": tenant_id, "server": info}


_SNAPSHOT_SORT_COLUMNS = {
    "userName": AccountSnapshot.user_name,
    "address": AccountSnapshot.address,
    "safeName": AccountSnapshot.safe_name,
    "platformId": AccountSnapshot.platform_id,
    "modificationTime": AccountSnapshot.modification_time,
}


async def _ensure_snapshot(
    session: AsyncSession, op: Operator, tenant_id: str, client: CyberArkClient
) -> datetime | None:
    """Sincroniza `account_snapshots` si la copia venció el TTL y devuelve
    su `synced_at`. Si la sync falla con una copia vieja, sigue con esa; sin
    ninguna copia, 502."""
    synced = await inventory_sync.last_synced_at(tenant_id, session)
    stale = synced is None or (datetime.now(UTC) - synced) > timedelta(
        seconds=get_settings().accounts_sync_ttl
    )
    if stale:
        try:
            count = await inventory_sync.sync_accounts(tenant_id, client, session)
        except CyberArkError as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="account.sync",
                result="error",
                tenant_id=tenant_id,
                detail={"error": str(e)},
            )
            await session.commit()
            if synced is None:
                raise HTTPException(
                    status_code=502, detail=f"no se pudo sincronizar el inventario: {e}"
                )
            # degradar: seguir sirviendo la copia vieja en vez de romper la grilla
        else:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="account.sync",
                result="ok",
                tenant_id=tenant_id,
                detail={"count": count},
            )
            await session.commit()
            synced = datetime.now(UTC)
    return synced


async def _accounts_from_snapshot(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    client: CyberArkClient,
    *,
    search: str | None,
    sort: str | None,
    offset: int,
    limit: int,
    safe_name: str | None,
    platform_id: str | None,
    user_name: str | None,
    address: str | None,
    status: str | None = None,
) -> tuple[list, int, datetime | None]:
    """Sirve la grilla de Cuentas desde `account_snapshots` en vez de en
    vivo contra CyberArk — el único camino que puede filtrar por
    `platform_id`/`user_name`/`address`, que la API Gen2 no filtra
    server-side. Sincroniza primero si la copia venció el TTL. Ver
    docs/specs/inventory-sync-postgres.md."""
    synced = await _ensure_snapshot(session, op, tenant_id, client)

    stmt = select(AccountSnapshot).where(AccountSnapshot.tenant_id == tenant_id)
    if platform_id:
        # parcial, como user_name/address -- probado contra Globex real:
        # un match exacto confunde en un input de texto libre ("filtrar…"),
        # el operador espera que tipear un pedazo alcance (mismo criterio
        # que el resto de los filtros por columna de la app).
        stmt = stmt.where(AccountSnapshot.platform_id.ilike(f"%{platform_id}%"))
    if user_name:
        stmt = stmt.where(AccountSnapshot.user_name.ilike(f"%{user_name}%"))
    if address:
        stmt = stmt.where(AccountSnapshot.address.ilike(f"%{address}%"))
    if safe_name:
        stmt = stmt.where(AccountSnapshot.safe_name == safe_name)
    terms = parse_search_terms(search)
    sort_key = (sort or "").lstrip("-")
    sort_col = _SNAPSHOT_SORT_COLUMNS.get(sort_key)
    if sort_col is not None:
        stmt = stmt.order_by(sort_col.desc() if (sort or "").startswith("-") else sort_col.asc())

    if status or terms:
        # el estado y las remote machines viven dentro de `raw` (JSON) -- se
        # filtra en Python, no en SQL, para no depender del dialecto (Postgres
        # en el server, SQLite en los tests). Son unas miles de filas por
        # tenant como mucho. Con varios términos (lista pegada de un ticket)
        # la coincidencia es exacta, ver `account_matches_terms`.
        all_rows = (await session.execute(stmt)).scalars().all()
        matching = [
            r.raw
            for r in all_rows
            if (not status or status in account_status_flags(r.raw))
            and (not terms or account_matches_terms(r.raw, terms, exact=len(terms) > 1))
        ]
        return matching[offset : offset + limit], len(matching), synced

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.offset(offset).limit(limit)
    snapshot_rows = (await session.execute(stmt)).scalars().all()
    return [r.raw for r in snapshot_rows], total, synced


async def _remote_machine_matches(session: AsyncSession, tenant_id: str, terms: list[str]) -> list:
    """Cuentas de la copia local que coinciden SÓLO por `remoteMachines` (las
    que coinciden por nombre/usuario/address/safe/plataforma ya las trae la
    búsqueda en vivo de CyberArk). No sincroniza: si el tenant todavía no
    tiene copia, devuelve vacío."""
    snapshot_rows = (
        (
            await session.execute(
                select(AccountSnapshot).where(AccountSnapshot.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        r.raw
        for r in snapshot_rows
        if account_matches_remote_machines(r.raw, terms, exact=False)
        and not account_matches_terms(r.raw, terms, exact=False, include_remote=False)
    ]


@router.get("/accounts")
async def accounts(
    tenant_id: str,
    search: str | None = None,
    sort: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    safe_name: str | None = None,
    modified_after: int | None = Query(None, ge=0),
    platform_id: str | None = None,
    user_name: str | None = None,
    address: str | None = None,
    status: str | None = Query(None, pattern="^(failure|manual|cpm_disabled)$"),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    filters = {
        k: v
        for k, v in {
            "safe_name": safe_name,
            "modified_after": modified_after,
            "platform_id": platform_id,
            "user_name": user_name,
            "address": address,
            "status": status,
        }.items()
        if v
    }
    async with tenant_client(tenant_id, session) as (_, client):
        # varios términos (lista de address pegada de un ticket) sólo se pueden
        # resolver contra la copia local: la búsqueda de CyberArk es de un
        # único término y no mira las remote machines.
        if platform_id or user_name or address or status or len(parse_search_terms(search)) > 1:
            rows, total, synced = await _accounts_from_snapshot(
                session,
                op,
                tenant_id,
                client,
                search=search,
                sort=sort,
                offset=offset,
                limit=limit,
                safe_name=safe_name,
                platform_id=platform_id,
                user_name=user_name,
                address=address,
                status=status,
            )
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="account.list",
                result="ok",
                tenant_id=tenant_id,
                detail={
                    "count": total,
                    "returned": len(rows),
                    "filters": filters,
                    "source": "snapshot",
                },
            )
            await session.commit()
            return {
                "tenant": tenant_id,
                "count": total,
                "offset": offset,
                "limit": limit,
                "accounts": rows,
                "synced_at": synced.isoformat() if synced else None,
            }
        rows, total = await _run(
            session,
            op,
            tenant_id,
            "account.list",
            client.accounts_page(
                search=search,
                sort=sort,
                offset=offset,
                limit=limit,
                safe_name=safe_name,
                modified_after=modified_after,
            ),
            extra={"filters": filters} if filters else None,
        )
        # CyberArk no busca en las remote machines: las cuentas de dominio que
        # sólo coinciden por ahí se suman desde la copia local, arriba de la
        # primera página (nunca duplicando lo que ya trajo la búsqueda en vivo).
        terms = parse_search_terms(search)
        if terms:
            extras = await _remote_machine_matches(session, tenant_id, terms)
            total += len(extras)  # igual en todas las páginas
            if offset == 0:
                on_page = {str(r.get("id")) for r in rows}
                rows = [e for e in extras if str(e.get("id")) not in on_page] + rows
    return {
        "tenant": tenant_id,
        "count": total,
        "offset": offset,
        "limit": limit,
        "accounts": rows,
    }


@router.get("/accounts/summary")
async def accounts_summary(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Conteos por estado para los chips de la grilla de Cuentas (total, con
    falla del CPM, deshabilitada por un usuario, deshabilitada por el CPM), desde `account_snapshots`
    (sincroniza si venció). Cada chip filtra con `GET /accounts?status=`."""
    async with tenant_client(tenant_id, session) as (_, client):
        synced = await _ensure_snapshot(session, op, tenant_id, client)
    raws = (
        (
            await session.execute(
                select(AccountSnapshot.raw).where(AccountSnapshot.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    counts = dict.fromkeys(ACCOUNT_STATUS_FLAGS, 0)
    for raw in raws:
        for flag in account_status_flags(raw):
            counts[flag] += 1
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.summary",
        result="ok",
        tenant_id=tenant_id,
        detail={"total": len(raws), **counts},
    )
    await session.commit()
    return {
        "tenant": tenant_id,
        "total": len(raws),
        **counts,
        "synced_at": synced.isoformat() if synced else None,
    }


@router.get("/accounts/{account_id}")
async def account_detail(
    tenant_id: str,
    account_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        acc = await _run(
            session,
            op,
            tenant_id,
            "account.get",
            client.get_account(account_id),
            target=account_id,
        )
    return {"tenant": tenant_id, "account": acc}


@router.get("/accounts/{account_id}/activities")
async def account_activities(
    tenant_id: str,
    account_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "account.activities",
            client.account_activities(account_id),
            target=account_id,
        )
    return {"tenant": tenant_id, "account_id": account_id, "activities": rows}


@router.get("/accounts/{account_id}/password-versions")
async def account_password_versions(
    tenant_id: str,
    account_id: str,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Metadata de versiones históricas de la contraseña (fecha/hora/
    quién la modificó) — SIN el valor. RBAC `tenant_admin+`, sin
    feature-flag propia: es metadata, mismo criterio que listar backups
    de cuentas borradas (`GET .../account-backups`). Revelar el VALOR de
    una versión puntual es `POST .../reveal-version` en `writes.py`,
    gateado además por la feature `account.reveal.history`. Ver
    docs/specs/done/account-password-versions.md."""
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "account.password_versions.list",
            client.list_password_versions(account_id),
            target=account_id,
        )
    return {"tenant": tenant_id, "account_id": account_id, "versions": rows}


@router.get("/safes")
async def safes(
    tenant_id: str,
    search: str | None = None,
    sort: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows, total = await _run(
            session,
            op,
            tenant_id,
            "safe.list",
            client.safes_page(search=search, sort=sort, offset=offset, limit=limit),
        )
    return {
        "tenant": tenant_id,
        "count": total,
        "offset": offset,
        "limit": limit,
        "safes": rows,
    }


@router.get("/safe-names")
async def safe_names(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Nombres de todos los Safes del tenant — para poblar el filtro de Cuentas."""
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await safes_rows_cached(session, op, tenant_id, client, "safe.list")
    names = sorted({s["safeName"] for s in rows if isinstance(s, dict) and s.get("safeName")})
    return {"tenant": tenant_id, "names": names}


@router.get("/cpm-names")
async def cpm_names(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """CPMs distintos entre las safes existentes del tenant — para poblar el
    selector de `safe.create`/`safe.update` (no hay API de CyberArk que
    liste los CPMs disponibles). Reusa el mismo caché que `/safe-names`."""
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await safes_rows_cached(session, op, tenant_id, client, "safe.list")
    return {"tenant": tenant_id, "names": list_cpm_names(rows)}


@router.get("/safes/{safe_id}/members")
async def safe_members(
    tenant_id: str,
    safe_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "safe.members",
            client.safe_members(safe_id),
            target=safe_id,
        )
    return {"tenant": tenant_id, "safe_id": safe_id, "members": rows}


@router.get("/member-search")
async def member_search(
    tenant_id: str,
    q: str = Query(min_length=1),
    kind: str = Query("any", pattern="^(any|user|group)$"),
    safe_id: str | None = None,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Busca usuarios/grupos para agregar como miembro de una safe — usuarios
    del Vault + grupos del Vault + usuarios de Identity (Redrock). Tolerante a
    que una fuente falle. Ver `docs/specs/safe-member-add.md`."""
    term = sanitize_search_term(q)
    if not term:
        return {"tenant": tenant_id, "results": []}

    async def _gather() -> list[dict]:
        async with tenant_client(tenant_id, session) as (_, client):
            tasks = [
                client.list_users(search=term) if kind in ("any", "user") else _empty(),
                client.list_user_groups() if kind in ("any", "group") else _empty(),
                client.redrock_query(redrock_user_search_sql(term))
                if kind in ("any", "user")
                else _empty(),
                client.safe_members(safe_id) if safe_id else _empty(),
            ]
            vusers, vgroups, idusers, members = await asyncio.gather(*tasks, return_exceptions=True)

        def _ok(v: object) -> list[dict]:
            return v if isinstance(v, list) else []

        vgroups_l = [
            g
            for g in _ok(vgroups)
            if term.lower() in str(g.get("groupName") or g.get("name") or "").lower()
        ]
        existing = {
            str(m.get("memberName") or "").lower() for m in _ok(members) if isinstance(m, dict)
        }
        return merge_member_candidates(
            _ok(vusers), vgroups_l, _ok(idusers), existing_members=existing, kind=kind
        )

    results = await _run(
        session,
        op,
        tenant_id,
        "member.search",
        _gather(),
        target=safe_id,
        extra={"q": term, "kind": kind},
    )
    return {"tenant": tenant_id, "results": results}


async def _empty() -> list:
    return []


@router.get("/sessions")
async def sessions(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(session, op, tenant_id, "session.list", client.live_sessions())
    return {"tenant": tenant_id, "count": len(rows), "sessions": rows}


@router.get("/components")
async def components(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        per_component = await _run(
            session,
            op,
            tenant_id,
            "component.list",
            asyncio.gather(*(client.component_details(c) for c in _COMPONENT_IDS)),
        )
    rows: list[dict] = []
    for cid, instances in zip(_COMPONENT_IDS, per_component, strict=True):
        for inst in instances or []:
            rows.append(
                {
                    "component": _COMPONENT_NAMES.get(cid, cid),
                    "ip": inst.get("ComponentIP") or inst.get("IP"),
                    "version": inst.get("ComponentVersion"),
                    "connected": bool(inst.get("IsLoggedOn")),
                    "last_connection": inst.get("LastLogonDate") or inst.get("LastConnectionDate"),
                    "os": inst.get("OS"),
                }
            )
    return {"tenant": tenant_id, "count": len(rows), "components": rows}


@router.get("/platforms")
async def platforms(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await platforms_rows_cached(session, op, tenant_id, client, "platform.list")
    return {"tenant": tenant_id, "count": len(rows), "platforms": rows}


@router.get("/platforms/{platform_id}/psm")
async def platform_psm(
    tenant_id: str,
    platform_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """PSM asignado a una plataforma puntual — `GET /platforms` (genérico)
    no lo trae, hace falta "Get target platforms". Ver
    `docs/specs/platform-psm-migration.md`."""

    async def _fetch() -> dict:
        targets = await client.list_target_platforms()
        match = find_target_platform(targets, platform_id)
        if match is None:
            raise NotFound(f"plataforma '{platform_id}' no existe")
        return await client.get_platform_psm(match["ID"])

    async with tenant_client(tenant_id, session) as (_, client):
        psm = await _run(session, op, tenant_id, "platform.psm.get", _fetch(), target=platform_id)
    return {"tenant": tenant_id, "platform_id": platform_id, "psm": psm}


@router.get("/platforms/{platform_id}/export")
async def platform_export(
    tenant_id: str,
    platform_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Descarga el paquete de "Export Platform" de CyberArk — política/
    config del CPM, sin secretos. A diferencia del backup que guarda
    `platform.delete` (sólo antes de borrar), esto se puede pedir en
    cualquier momento. Usa el `PlatformID` string directo, no el ID
    numérico (ver `client.export_platform`)."""
    async with tenant_client(tenant_id, session) as (_, client):
        package = await _run(
            session,
            op,
            tenant_id,
            "platform.export",
            client.export_platform(platform_id),
            target=platform_id,
        )
    return Response(
        content=package,
        media_type="application/octet-stream",
        headers={"content-disposition": f'attachment; filename="{platform_id}.zip"'},
    )


@router.get("/platforms/{platform_id}/config")
async def platform_config(
    tenant_id: str,
    platform_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Configuración COMPLETA de una plataforma (el árbol de `editor.aspx`
    del PVWA: "UI & Workflows" + "Automatic Password Management"), leída
    del paquete de "Export Platform" — `editor.aspx` se autentica con la
    sesión del navegador del operador, PAM Web Console no puede llamarlo. Sólo
    lectura, `viewer+`, mismo criterio que `.../export`. Ver
    `docs/specs/platform-config-viewer.md`."""
    async with tenant_client(tenant_id, session) as (_, client):
        package = await _run(
            session,
            op,
            tenant_id,
            "platform.config.view",
            client.export_platform(platform_id),
            target=platform_id,
        )
    try:
        parsed = parse_platform_package(package)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"tenant": tenant_id, "platform_id": platform_id, **parsed}


@router.get("/psm-servers")
async def psm_servers(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Nombres de los PSM configurados en el tenant, para el selector de
    `platform.psm.update` (`PsmServerField.tsx`). El campo real de "Get
    PSM Servers" es `ID` (a veces `id`) — **no** `PSMServerID`, que nunca
    existió en la respuesta real; verificado contra `psm_balance.py`
    (Reportes, ya probado en el server), que usa el mismo campo para el
    mismo propósito. El bug anterior dejaba `names` siempre vacío y el
    selector caía siempre al modo "ingresar a mano"."""
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(session, op, tenant_id, "psm.servers.list", client.list_psm_servers())
    names = sorted(
        {
            str(r.get("ID") or r.get("id"))
            for r in rows
            if isinstance(r, dict) and (r.get("ID") or r.get("id"))
        }
    )
    return {"tenant": tenant_id, "names": names}


@router.get("/safes/defaults")
async def safe_defaults(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """CPM y retención más frecuentes entre las safes existentes — precarga
    el form de `safe.create`. Ver `docs/specs/plantillas-alta-cuenta-safe.md`."""
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await safes_rows_cached(session, op, tenant_id, client, "safe.defaults")
    return {"tenant": tenant_id, "defaults": suggest_safe_defaults(rows)}


@router.get("/users")
async def users(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(session, op, tenant_id, "user.list", client.list_users())
    return {"tenant": tenant_id, "count": len(rows), "users": rows}


@router.get("/users/{user_id}")
async def user_detail(
    tenant_id: str,
    user_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        user = await _run(
            session,
            op,
            tenant_id,
            "user.get",
            client.get_user(user_id),
            target=user_id,
        )
    return {"tenant": tenant_id, "user": user}


@router.get("/groups")
async def groups(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(session, op, tenant_id, "group.list", client.list_user_groups())
    return {"tenant": tenant_id, "count": len(rows), "groups": rows}


@router.get("/groups/{group_id}/members")
async def group_members(
    tenant_id: str,
    group_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "group.members",
            client.user_group_members(group_id),
            target=group_id,
        )
    return {"tenant": tenant_id, "group_id": group_id, "members": rows}
