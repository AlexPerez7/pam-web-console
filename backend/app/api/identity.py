"""Usuarios y roles de **Identity/SSO** (distintos de los usuarios/grupos
del Vault, ver `app.api.inventory` `/users` y `/groups` — no viven acá).
Sólo lectura — bloquear/desbloquear un usuario (`identity_user.lock`) vive
en `app.api.writes`, como el resto de las escrituras. Ver
`docs/specs/identity-users.md`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.inventory import _run
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, CyberArkError
from app.db import get_session
from app.domain.member_search import sanitize_search_term
from app.models import Operator, OperatorRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["identity"])

_viewer = require_tenant_access(OperatorRole.viewer)

_USERS_SQL = (
    "SELECT ID, Username, DisplayName, Email, Status FROM User ORDER BY Username COLLATE NOCASE"
)

# `RoleType`/`Organization` no estaban confirmados contra un tenant real al
# sumarlos (2026-09-25, a pedido de Alex para acercar la grilla a la de
# PVWA/Identity Administration) -- `RoleType` sí está confirmado como campo
# real del objeto Role vía el SDK público de CyberArk (`idsec-sdk-golang`,
# `IdsecIdentityRole.RoleType`), `Organization` es sólo una suposición a
# partir de la columna que muestra la UI de PVWA (que además aparece vacía
# en todas las filas de la captura de referencia). Degrada en cascada: si
# `RoleType`/`Organization` no son columnas válidas de la tabla `Role` en
# este tenant, Redrock devuelve `success: false` para TODA la consulta (no
# hay degradación parcial por columna en SQL), así que se reintenta con
# menos columnas en vez de romper la página entera.
_ROLES_SQL_FULL = (
    "SELECT ID, Name, Description, RoleType, Organization FROM Role ORDER BY Name COLLATE NOCASE"
)
_ROLES_SQL_WITH_TYPE = (
    "SELECT ID, Name, Description, RoleType FROM Role ORDER BY Name COLLATE NOCASE"
)
_ROLES_SQL_BASE = "SELECT ID, Name, Description FROM Role ORDER BY Name COLLATE NOCASE"


def _user_row(u: dict) -> dict:
    return {
        "id": u.get("ID"),
        "username": u.get("Username"),
        "displayName": u.get("DisplayName"),
        "email": u.get("Email"),
        "status": u.get("Status"),
    }


def _role_row(r: dict) -> dict:
    return {
        "id": r.get("ID"),
        "name": r.get("Name"),
        "description": r.get("Description"),
        "roleType": r.get("RoleType"),
        "organization": r.get("Organization"),
    }


def _member_row(m: dict) -> dict:
    return {
        "id": m.get("Guid") or m.get("ID"),
        "name": m.get("Name"),
        "type": m.get("Type"),
    }


async def _fetch_roles(client: CyberArkClient) -> list[dict]:
    for script in (_ROLES_SQL_FULL, _ROLES_SQL_WITH_TYPE, _ROLES_SQL_BASE):
        try:
            return await client.redrock_query(script)
        except CyberArkError as e:
            if script is _ROLES_SQL_BASE:
                raise
            logger.warning(
                "identity_role.list: %r falló (%s), reintentando con menos columnas", script, e
            )
    return []  # inalcanzable (la última iteración levanta o retorna)


@router.get("/identity-users")
async def identity_users(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client: CyberArkClient) -> list[dict]:
        rows = await client.redrock_query(_USERS_SQL)
        return [_user_row(u) for u in rows]

    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(session, op, tenant_id, "identity_user.list", fetch(client))
    return {"tenant": tenant_id, "count": len(rows), "users": rows}


@router.get("/identity-users/{user_id}")
async def identity_user_detail(
    tenant_id: str,
    user_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client: CyberArkClient) -> dict:
        info, locked = await asyncio.gather(
            client.identity_get_user_info(user_id),
            client.identity_is_user_locked(user_id),
        )
        return {**info, "Locked": locked}

    async with tenant_client(tenant_id, session) as (_, client):
        user = await _run(
            session, op, tenant_id, "identity_user.get", fetch(client), target=user_id
        )
    return {"tenant": tenant_id, "user": user}


@router.get("/identity-roles")
async def identity_roles(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Lista de roles de Identity — sólo lectura. Los miembros de un rol se
    piden aparte, al abrirlo (`/identity-roles/{role_id}/members`)."""

    async def fetch(client: CyberArkClient) -> list[dict]:
        rows = await _fetch_roles(client)
        return [_role_row(r) for r in rows]

    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(session, op, tenant_id, "identity_role.list", fetch(client))
    return {"tenant": tenant_id, "count": len(rows), "roles": rows}


@router.get("/identity-roles/{role_id}/members")
async def identity_role_members(
    tenant_id: str,
    role_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Miembros de un rol de Identity (usuarios, grupos y roles), como la
    pestaña "Members" del rol en Identity Administration. Sólo lectura."""

    async def fetch(client: CyberArkClient) -> list[dict]:
        rows = await client.identity_role_members(role_id)
        members = [_member_row(m) for m in rows]
        return sorted(members, key=lambda m: (str(m["type"] or ""), str(m["name"] or "").lower()))

    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session, op, tenant_id, "identity_role.members", fetch(client), target=role_id
        )
    return {"tenant": tenant_id, "role_id": role_id, "count": len(rows), "members": rows}


@router.get("/identity-roles/{role_id}/candidates")
async def identity_role_candidates(
    tenant_id: str,
    role_id: str,
    q: str = Query(min_length=1),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Usuarios y roles de Identity que coinciden con `q`, para "Agregar
    miembros" a un rol. Marca los que ya son miembros. Si una de las dos
    búsquedas falla se devuelve la otra."""
    term = sanitize_search_term(q)
    if not term:
        return {"tenant": tenant_id, "results": []}
    like = f"%{term}%"
    users_sql = (
        "SELECT ID, Username, DisplayName, Email FROM User "  # noqa: S608
        f"WHERE Username LIKE '{like}' OR DisplayName LIKE '{like}' OR Email LIKE '{like}' "
        "ORDER BY Username COLLATE NOCASE LIMIT 30"
    )
    roles_sql = (
        "SELECT ID, Name, Description FROM Role "  # noqa: S608
        f"WHERE Name LIKE '{like}' ORDER BY Name COLLATE NOCASE LIMIT 15"
    )

    async def _try(coro) -> list[dict] | None:
        try:
            return await coro
        except CyberArkError as e:
            logger.warning("identity_principal.search: una fuente falló (%s)", e)
            return None

    async def fetch(client: CyberArkClient) -> list[dict]:
        users, roles, members = await asyncio.gather(
            _try(client.redrock_query(users_sql)),
            _try(client.redrock_query(roles_sql)),
            _try(client.identity_role_members(role_id)),
        )
        if users is None and roles is None:
            raise CyberArkError("no se pudo buscar usuarios ni roles en Identity")
        existing: set[str] = set()
        for m in members or []:
            existing.add(str(m.get("Guid") or "").lower())
            existing.add(str(m.get("Name") or "").lower())
        out: list[dict] = []
        for u in users or []:
            out.append(
                {
                    "id": u.get("ID"),
                    "name": u.get("Username"),
                    "displayName": u.get("DisplayName"),
                    "email": u.get("Email"),
                    "type": "User",
                }
            )
        for r in roles or []:
            if str(r.get("ID") or "") == role_id:
                continue  # un rol no puede ser miembro de sí mismo
            out.append(
                {
                    "id": r.get("ID"),
                    "name": r.get("Name"),
                    "displayName": r.get("Description"),
                    "email": None,
                    "type": "Role",
                }
            )
        for c in out:
            c["alreadyMember"] = (
                str(c["id"] or "").lower() in existing or str(c["name"] or "").lower() in existing
            )
        return [c for c in out if c["id"] and c["name"]]

    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "identity_principal.search",
            fetch(client),
            target=role_id,
            extra={"q": term},
        )
    return {"tenant": tenant_id, "results": rows}
