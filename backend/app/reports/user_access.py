"""Accesos de un usuario: qué safes tiene asignados (directo, por un rol de
Identity o por un grupo del Vault) y qué cuentas -- targets -- hay en cada
uno. Ver `docs/specs/done/user-access-report.md`.

Pedido de un cliente (Globex, 2026-09-28): "qué safes tiene asignado el
usuario X y todos los targets que estaría viendo (user - target)". Filtrar
"Auditoría de permisos de safes" por el correo sólo muestra la membresía
DIRECTA; en Privilege Cloud lo habitual es que el usuario llegue al safe a
través de un rol de Identity o de un grupo que es miembro, y eso quedaba
afuera.

`heavy=True`: un `safe_members` por safe del tenant (mismo fan-out que
`safe-permissions`) + el listado completo de cuentas.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.cyberark import CyberArkClient, CyberArkError
from app.reports.base import Column, Param, Report, ReportResult

# permisos que responden "¿qué puede hacer con los targets de ese safe?"
_ACCESS_PERMS = ("listAccounts", "useAccounts", "retrieveAccounts", "manageSafe")

_COLUMNS = [
    Column("usuarioConsultado", "Usuario consultado"),
    Column("safeName", "Safe"),
    Column("via", "Acceso por"),
    Column("listAccounts", "Listar cuentas", "bool"),
    Column("useAccounts", "Conectarse (Use)", "bool"),
    Column("retrieveAccounts", "Ver contraseña", "bool"),
    Column("manageSafe", "Administrar safe", "bool"),
    Column("accountName", "Cuenta"),
    Column("userName", "Usuario (target)"),
    Column("address", "Address (target)"),
    Column("platformId", "Plataforma"),
]


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def match_memberships(
    username: str,
    roles: list[str],
    groups: list[str],
    members_by_safe: dict[str, list[dict]],
) -> dict[str, dict[str, Any]]:
    """`safe -> {"via": [...], perms...}` para los safes donde el usuario es
    miembro directo o lo es alguno de sus roles/grupos. Si llega por varias
    vías, los permisos se suman (OR): es lo que efectivamente puede hacer."""
    user = _lower(username)
    role_by_lower = {_lower(r): r for r in roles if r}
    group_by_lower = {_lower(g): g for g in groups if g}

    result: dict[str, dict[str, Any]] = {}
    for safe_name, members in members_by_safe.items():
        for m in members:
            name = _lower(m.get("memberName"))
            if not name:
                continue
            if name == user:
                via = "Directo"
            elif name in role_by_lower:
                via = f"Rol de Identity: {role_by_lower[name]}"
            elif name in group_by_lower:
                via = f"Grupo: {group_by_lower[name]}"
            else:
                continue
            entry = result.setdefault(safe_name, {"via": [], **dict.fromkeys(_ACCESS_PERMS, False)})
            if via not in entry["via"]:
                entry["via"].append(via)
            perms = m.get("permissions") or {}
            for p in _ACCESS_PERMS:
                entry[p] = entry[p] or bool(perms.get(p))
    return result


def compute_user_access(
    memberships: dict[str, dict[str, Any]],
    accounts: list[dict],
    username: str = "",
) -> list[dict[str, Any]]:
    """Una fila por cuenta de cada safe asignado; un safe sin cuentas sale
    igual, con las columnas de la cuenta vacías. Cada fila repite el usuario
    consultado, para que el CSV exportado diga de quién son los accesos."""
    by_safe: dict[str, list[dict]] = {}
    for a in accounts:
        by_safe.setdefault(_lower(a.get("safeName")), []).append(a)

    rows: list[dict[str, Any]] = []
    for safe_name in sorted(memberships, key=str.lower):
        entry = memberships[safe_name]
        base = {
            "usuarioConsultado": username,
            "safeName": safe_name,
            "via": "; ".join(entry["via"]),
            **{p: entry[p] for p in _ACCESS_PERMS},
        }
        safe_accounts = sorted(
            by_safe.get(_lower(safe_name), []),
            key=lambda a: (_lower(a.get("address")), _lower(a.get("userName"))),
        )
        if not safe_accounts:
            rows.append(
                {**base, "accountName": None, "userName": None, "address": None, "platformId": None}
            )
            continue
        for a in safe_accounts:
            rows.append(
                {
                    **base,
                    "accountName": a.get("name"),
                    "userName": a.get("userName"),
                    "address": a.get("address"),
                    "platformId": a.get("platformId"),
                }
            )
    return rows


def vault_groups_of(username: str, vault_users: list[dict]) -> list[str]:
    """Grupos del Vault del usuario (`groupsMembership` de `/API/Users`)."""
    user = _lower(username)
    for vu in vault_users:
        if _lower(vu.get("username") or vu.get("userName")) == user:
            return [
                g["groupName"]
                for g in vu.get("groupsMembership") or []
                if isinstance(g, dict) and g.get("groupName")
            ]
    return []


async def _identity_roles(client: CyberArkClient, username: str) -> tuple[list[str], str | None]:
    """Roles de Identity del usuario + un aviso si no se pudieron resolver
    (el reporte sigue con la membresía directa y los grupos)."""
    try:
        user = await client.identity_find_user(username)
        if not user or not user.get("ID"):
            return [], "el usuario no se encontró en Identity"
        return await client.identity_user_roles(str(user["ID"])), None
    except CyberArkError as e:
        return [], f"no se pudieron obtener los roles de Identity ({e})"


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    username = str(params.get("usuario") or "").strip()

    async def _users() -> list[dict]:
        try:
            return await client.list_users()
        except CyberArkError:
            return []

    (roles, roles_warning), vault_users, safes, accounts = await asyncio.gather(
        _identity_roles(client, username),
        _users(),
        client.list_safes(),
        client.list_accounts(),
    )
    groups = vault_groups_of(username, vault_users)

    async def _members(safe: dict) -> tuple[str, list[dict]]:
        sid = safe.get("safeUrlId") or safe.get("safeName") or ""
        name = safe.get("safeName") or sid
        try:
            return name, await client.safe_members(sid)
        except CyberArkError:
            return name, []

    pairs = await asyncio.gather(*(_members(s) for s in safes))
    members_by_safe: dict[str, list[dict]] = {}
    for name, members in pairs:
        members_by_safe.setdefault(name, []).extend(members)

    memberships = match_memberships(username, roles, groups, members_by_safe)
    rows = compute_user_access(memberships, accounts, username)

    meta: dict[str, Any] = {
        "usuario": username,
        "roles_de_identity": ", ".join(roles) or "ninguno",
        "grupos_del_vault": ", ".join(groups) or "ninguno",
        "safes_asignados": len(memberships),
        "cuentas": sum(1 for r in rows if r["accountName"] is not None),
    }
    if roles_warning:
        meta["aviso"] = roles_warning
    return ReportResult(columns=_COLUMNS, rows=rows, meta=meta)


report = Report(
    name="user-access",
    category="Safes",
    label="Accesos de un usuario",
    description=(
        "Safes que tiene asignados un usuario (directo, por rol de Identity o por grupo) "
        "y las cuentas (targets) de cada uno, con sus permisos."
    ),
    run=_run,
    heavy=True,
    params=[
        Param(
            "usuario",
            "Usuario",
            "user",
            required=True,
            help="Buscar por nombre o correo",
        ),
    ],
)
