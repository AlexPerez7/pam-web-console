"""Candidatos a liberar licencia / dar de baja (todos los usuarios de Identity).

Portado de `License_Candidates_Report.ps1` — que a su vez replica
`analysis.py:analyze_users()` de ITR. Cruza TODOS los usuarios del Vault con
TODOS los de Identity (Redrock) por username; `EPVUser`/`ExtUser` son los
únicos tipos que consumen licencia de CyberArk, el resto se muestra igual
(higiene general de Identity) con `licenseType = "Sin licencia"`.

**Fusión con `identity-user-activity` (2026-09-22):** ese reporte separado
("Actividad de usuarios de Identity", sólo Identity, sin cruzar el Vault)
se dio de baja — el pedido real detrás de los dos era el mismo ("qué
usuarios están inactivos, para poder liberar licencias o darlos de baja"),
así que en vez de mantener dos reportes que compartían el 90% de la lógica
con alcances apenas distintos, este quedó como la única fuente: ya no
filtra por tipo de usuario (antes sólo mostraba EPVUser/ExtUser), suma
`displayName`/`email` (que sólo tenía el reporte dado de baja), y el
`candidato` ahora también considera el status de Identity (disabled/
suspended/locked/deactivated), no sólo los días sin login.

Las filas `ExtUser` (Privileged External User) son los vendors de Remote
Access (Alero) — "días sin loguearse" contra Identity NO es confiable para
estas cuentas, se autentican vía Remote Access, no contra el portal de
Identity directamente (mismo motivo documentado en el `.ps1` original). Si
el tenant tiene Alero configurado (`Tenant.alero_tenant_id`),
`enrich_with_alero()` reemplaza el status/login/candidato de esas filas con
datos reales de Alero — ver `app.api.reports._compute`, que la llama
después de `_run()` porque necesita un `AleroClient` (API separada de
CyberArk, `report.run()` sólo recibe un `CyberArkClient`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.alero import as_list
from app.cyberark import CyberArkClient
from app.reports._identity import INACTIVE_STATUS, days_since, parse_redrock_epoch, source_label
from app.reports.base import Column, Param, Report, ReportResult

if TYPE_CHECKING:
    from app.alero import AleroClient

_LICENSE_TYPES = {"EPVUser": "Privileged Standard User", "ExtUser": "Privileged External User"}
_PAGE = 500

_COLUMNS = [
    Column("username", "Usuario"),
    Column("displayName", "Nombre"),
    Column("email", "Email"),
    Column("licenseType", "Tipo (licencia)"),
    Column("inVault", "En el Vault", "bool"),
    Column("inIdentity", "En Identity", "bool"),
    Column("origen", "Origen"),
    Column("status", "Estado (Identity/Alero)"),
    Column("lastLogin", "Último login", "date"),
    Column("daysSinceLogin", "Días sin login", "number"),
    Column("candidato", "Candidato a revisar", "bool"),
    Column("aleroExpiration", "Vigencia de acceso (Alero)"),
    Column("aleroRoles", "Roles (Alero)"),
    Column("aleroGroups", "Grupos (Alero)"),
    Column("comentarios", "Comentarios"),
    Column("gruposVault", "Grupos (Vault)"),
]


def compute_license_candidates(
    vault_users: list[dict], identity_users: list[dict], inactive_days: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    vault: dict[str, dict] = {}
    for u in vault_users:
        name = (u.get("username") or "").strip()
        if name:
            vault.setdefault(name.lower(), u)

    identity: dict[str, dict] = {}
    for u in identity_users:
        name = (u.get("Username") or u.get("LoginName") or "").strip()
        if not name:
            continue
        prev = identity.get(name.lower())
        # ante duplicados (migración AD->Entra), preferir el de mejor status
        if prev is None or _status_rank(u.get("Status")) > _status_rank(prev.get("Status")):
            identity[name.lower()] = u

    rows: list[dict[str, Any]] = []
    for key in sorted(set(vault) | set(identity)):
        vu = vault.get(key)
        iu = identity.get(key)
        user_type = (vu or {}).get("userType") or (iu or {}).get("UserType")
        license_type = _LICENSE_TYPES.get(user_type or "", "Sin licencia")
        last = parse_redrock_epoch((iu or {}).get("LastLogin"))
        d = days_since(last)
        status = (iu or {}).get("Status")
        disabled = str(status or "").lower() in INACTIVE_STATUS
        candidato = last is None or (d is not None and d >= inactive_days) or disabled
        origen = (source_label(iu) if iu else None) or (vu or {}).get("source") or "Desconocido"
        grupos_vault = (
            ", ".join(
                g.get("groupName")
                for g in (vu or {}).get("groupsMembership") or []
                if g.get("groupName")
            )
            or None
        )
        rows.append(
            {
                "username": (vu or {}).get("username") or (iu or {}).get("Username"),
                "displayName": (iu or {}).get("DisplayName"),
                "email": (iu or {}).get("Email"),
                "licenseType": license_type,
                "inVault": vu is not None,
                "inIdentity": iu is not None,
                "origen": origen,
                "status": status,
                "lastLogin": last,
                "daysSinceLogin": d,
                "candidato": candidato,
                "gruposVault": grupos_vault,
            }
        )
    rows.sort(
        key=lambda r: (
            not r["candidato"],
            -(r["daysSinceLogin"] if r["daysSinceLogin"] is not None else 10**9),
        )
    )
    con_licencia = sum(1 for r in rows if r["licenseType"] in _LICENSE_TYPES.values())
    return rows, {
        "usuarios_totales": len(rows),
        "con_licencia": con_licencia,
        "candidatos": sum(1 for r in rows if r["candidato"]),
        "umbral_dias": inactive_days,
    }


def _status_rank(status: Any) -> int:
    s = str(status or "").lower()
    return {"active": 3, "suspended": 2, "not invited": 1}.get(s, 0)


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    inactive_days = int(params.get("inactive_days") or 30)
    vault_users = await client.list_users()

    identity_users: list[dict] = []
    offset = 0
    _base_sql = (
        "SELECT ID, Username, DisplayName, Email, UserType, Status, LastLogin, "
        "SourceDs, SourceDsLocalized, SourceDsType FROM Users"
    )
    while True:
        # _PAGE y offset son int -> sin riesgo de inyección
        page = await client.redrock_query(f"{_base_sql} LIMIT {_PAGE:d} OFFSET {offset:d}")
        identity_users.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE

    rows, meta = compute_license_candidates(vault_users, identity_users, inactive_days)
    return ReportResult(columns=_COLUMNS, rows=rows, meta=meta)


report = Report(
    name="license-candidates",
    category="Identity y Licencias",
    label="Candidatos a liberar licencia",
    description=(
        "Todos los usuarios de Identity, cruzados con el Vault — último login, candidatos "
        "a dar de baja por inactividad, y cuáles consumen una licencia CyberArk "
        "(Privileged Standard/External) que se podría liberar."
    ),
    run=_run,
    params=[
        Param(
            "inactive_days",
            "Días sin login",
            "number",
            required=False,
            help="umbral (default 30)",
        )
    ],
)


def enrich_ext_users_with_alero(
    rows: list[dict[str, Any]],
    alero_vendors: list[dict],
    alero_last_logins: dict[str, int],
    inactive_days: int,
) -> None:
    """Enriquece IN-PLACE las filas `ExtUser` (Privileged External User, los
    vendors de Remote Access) con datos reales de Alero -- reemplaza
    status/lastLogin/daysSinceLogin/candidato (que venían de Identity, no
    confiable para estas cuentas) y suma vigencia de acceso/roles/grupos.
    Las filas `EPVUser` quedan intactas. Mismo criterio que la pestaña
    "Privileged External User" de `License_Candidates_Report.ps1`.

    `alero_last_logins` está en epoch MILISEGUNDOS (formato nativo de
    `activities`, ver `AleroClient.get_all_vendor_last_logins`) -- se
    convierte a segundos acá para quedar consistente con `lastLogin` de las
    filas `EPVUser` (`parse_redrock_epoch`, siempre en segundos)."""
    vendors_by_username: dict[str, dict] = {}
    for v in alero_vendors:
        uname = (v.get("username") or "").strip().lower()
        if uname:
            vendors_by_username[uname] = v

    for row in rows:
        if row["licenseType"] != _LICENSE_TYPES["ExtUser"]:
            continue

        local_part = (row["username"] or "").strip().lower().split("@")[0]
        vendor = vendors_by_username.get(local_part)
        last_login_ms = alero_last_logins.get(local_part)
        last_login = last_login_ms // 1000 if last_login_ms is not None else None
        days_since_login = days_since(last_login)

        alero_status = vendor.get("status") if vendor else "No encontrado en Alero"
        alero_expiration = vendor.get("expirationStatus") if vendor else None
        candidato = (
            alero_status == "Deactivated"
            or (alero_expiration is not None and alero_expiration != "InTimeframe")
            or last_login is None
            or (days_since_login is not None and days_since_login >= inactive_days)
        )

        comentario = None
        if vendor is None:
            comentario = (
                "Posible cuenta huérfana: no tiene registro en Identity y el vendor ya no "
                "existe en Alero (¿eliminado?). Solo queda el registro en el Vault."
                if not row["inIdentity"]
                else "No se encontró como vendor en Alero (revisar manualmente)."
            )

        alero_roles = ", ".join(as_list(vendor.get("idaptiveRoles")) or []) if vendor else None
        alero_groups = ", ".join(as_list(vendor.get("groups")) or []) if vendor else None
        row.update(
            {
                "status": alero_status,
                "lastLogin": last_login,
                "daysSinceLogin": days_since_login,
                "candidato": candidato,
                "aleroExpiration": alero_expiration,
                "aleroRoles": alero_roles,
                "aleroGroups": alero_groups,
                "comentarios": comentario,
            }
        )

    rows.sort(
        key=lambda r: (
            not r["candidato"],
            -(r["daysSinceLogin"] if r["daysSinceLogin"] is not None else 10**9),
        )
    )


async def enrich_with_alero(
    result: ReportResult, alero: AleroClient, *, inactive_days: int
) -> None:
    """Orquesta el enriquecimiento: trae vendors + el mapa de último login
    real de Alero y llama a `enrich_ext_users_with_alero` (pura). La llama
    `app.api.reports._compute`, sólo si el tenant tiene
    `Tenant.alero_tenant_id` configurado -- `report.run()` no puede hacerlo
    directo porque sólo recibe un `CyberArkClient` (API separada de la de
    Alero, credenciales propias)."""
    vendors = await alero.list_vendors()
    last_logins = await alero.get_all_vendor_last_logins()
    enrich_ext_users_with_alero(result.rows, vendors, last_logins, inactive_days)
    result.meta["alero_enriched"] = True
