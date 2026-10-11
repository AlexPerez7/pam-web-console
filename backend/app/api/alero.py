"""Vendors, Users, Roles y Activities de CyberArk Remote Access (Alero) --
acceso remoto de terceros/proveedores. Recursos DISTINTOS de la misma API:
Vendors son las cuentas de terceros que se autentican vía Remote Access;
Users son las personas que usan la app móvil (equipo propio del cliente,
con rol Administrator/User/Vendor Manager); Roles ("Identity roles" en el
portal) son roles de CyberArk Identity usados para asignar acceso a
vendors; Activities es el feed de auditoría de Alero (login, altas/bajas,
cambios de config) -- ver el portal de Alero, Identities > Vendors / Users
/ Identity roles, y Activities aparte en el nav. Espacio de nav propio,
sólo lo que sale de la API de Alero (nada del Vault ni de Identity) --
mismo criterio que Identity, ver `app.api.identity`. Sólo lectura. Ver
docs/specs/done/alero-vendors.md.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.alero import AleroClient, AleroError, AleroNotFound, as_list
from app.core import audit
from app.core.deps import require_tenant_access
from app.core.tenants import alero_client
from app.db import get_session
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["alero"])

_viewer = require_tenant_access(OperatorRole.viewer)


def _vendor_row(v: dict) -> dict:
    return {
        "id": v.get("id"),
        "username": v.get("username"),
        "fullName": v.get("fullName"),
        "email": v.get(
            "emailAddress"
        ),  # NO "email" -- confirmado contra Alero real (Globex, 2026-09-21)
        "companyName": v.get("companyName"),
        "status": v.get("status"),
        "expirationStatus": v.get("expirationStatus"),
        "roles": as_list(v.get("idaptiveRoles")),
        "groups": as_list(v.get("groups")),
    }


async def _run_alero(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    action: str,
    coro,
    *,
    target: str | None = None,
):
    """Igual que `app.api.inventory._run`, pero para errores de Alero (API
    separada de CyberArk, con su propia jerarquía de excepciones)."""
    try:
        result = await coro
    except AleroNotFound as e:
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
        raise HTTPException(status_code=404, detail=str(e))
    except AleroError as e:
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
        raise HTTPException(status_code=502, detail=f"Alero: {e}")

    detail = {"count": len(result)} if isinstance(result, list) else {}
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


@router.get("/alero-vendors")
async def alero_vendors(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with alero_client(tenant_id, session) as client:
        rows = await _run_alero(
            session,
            op,
            tenant_id,
            "alero_vendor.list",
            _fetch_vendors(client),
        )
    return {"tenant": tenant_id, "count": len(rows), "vendors": rows}


async def _fetch_vendors(client) -> list[dict]:
    rows = await client.list_vendors()
    return [_vendor_row(v) for v in rows]


async def _resolve_applications(client: AleroClient, applications: list[dict]) -> list[dict]:
    """Resuelve `{siteId, applicationId}` crudos a `{site, application}` con
    nombres legibles (`displayName` del site + `name` de la app). Se
    resuelve con `list_sites()` (una vez) + `list_site_applications()` (una
    por site distinto entre las apps del vendor, normalmente 1)."""
    if not applications:
        return []
    sites: dict[str, str] = {
        str(s.get("id")): str(s.get("displayName") or s.get("id"))
        for s in await client.list_sites()
        if s.get("id")
    }
    site_ids: set[str] = {str(a["siteId"]) for a in applications if a.get("siteId")}
    apps_by_site: dict[str, dict[str, str]] = {}
    for sid in site_ids:
        apps = await client.list_site_applications(sid)
        apps_by_site[sid] = {
            str(a.get("id")): str(a.get("name") or a.get("id")) for a in apps if a.get("id")
        }
    resolved = []
    for a in applications:
        raw_site_id = a.get("siteId")
        site_id = str(raw_site_id) if raw_site_id else None
        raw_app_id = a.get("applicationId")
        app_id = str(raw_app_id) if raw_app_id else None
        resolved.append(
            {
                "site": sites.get(site_id, site_id) if site_id else None,
                "application": (
                    apps_by_site.get(site_id, {}).get(app_id, app_id)
                    if site_id and app_id
                    else None
                ),
            }
        )
    return resolved


def _vendor_detail_row(v: dict, applications: list[dict], last_login_real: int | None) -> dict:
    return {
        "id": v.get("id"),
        "username": v.get("username"),
        "firstName": v.get("firstName"),
        "lastName": v.get("lastName"),
        "email": v.get("emailAddress"),
        "phoneNumber": v.get("phoneNumber"),
        "phoneModel": v.get("phoneModel"),
        "phoneOs": v.get("phoneOs"),
        "companyName": v.get("companyName"),
        "status": v.get("status"),
        "appVersion": v.get("appVersion"),
        "canInvite": v.get("canInvite"),
        "comments": v.get("comments") or None,
        # último login REAL, calculado desde activities/ApplicationVendorLogin
        # (ver AleroClient.get_vendor_last_login) -- gana sobre el campo
        # "lastLogin" crudo de VendorResponse (nunca confirmado como
        # confiable) si se encontró alguno; si no hubo ningún login en la
        # ventana de lookback, cae al campo crudo por si acaso.
        "lastLogin": last_login_real if last_login_real is not None else v.get("lastLogin"),
        "accessStartDate": v.get("accessStartDate"),
        "accessEndDate": v.get("accessEndDate"),
        "invitedBy": v.get("invitedBy"),
        "provisioningType": v.get("provisioningType"),
        "roles": [g.get("name") for g in v.get("groups") or [] if g.get("name")],
        "applications": applications,
        # campos de VendorResponse que faltaban -- confirmados contra
        # Globex real (2026-09-21), coinciden 1 a 1 con lo que muestra
        # el portal: emailConfirmedAt -> "Email verified at",
        # phoneAndEmailAuth -> "Allowed to join without app" (mismo booleano,
        # sin invertir), invitedVendorsInitialStatus/maxNumOfInvitedVendors
        # -> configuración de auto-invitación de sub-vendors por este vendor.
        # "Built-in role", "Last offline sync", "Vendor time zone",
        # "Allowed working days/hours" del portal NO están en esta
        # respuesta (se probó GET /v2-edge/vendors/{id} real, no vienen) --
        # no son un descuido, la API pública no los expone acá.
        "emailConfirmedAt": v.get("emailConfirmedAt"),
        "phoneAndEmailAuth": v.get("phoneAndEmailAuth"),
        "invitedVendorsInitialStatus": v.get("invitedVendorsInitialStatus"),
        "maxNumOfInvitedVendors": v.get("maxNumOfInvitedVendors"),
    }


@router.get("/alero-vendors/{vendor_id}")
async def alero_vendor_detail(
    tenant_id: str,
    vendor_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client) -> dict:
        v = await client.get_vendor_detail(vendor_id)
        applications = await _resolve_applications(client, v.get("applications") or [])
        last_login_real = None
        if v.get("username"):
            last_login_real = await client.get_vendor_last_login(v["username"])
        return _vendor_detail_row(v, applications, last_login_real)

    async with alero_client(tenant_id, session) as client:
        vendor = await _run_alero(
            session, op, tenant_id, "alero_vendor.get", fetch(client), target=vendor_id
        )
    return vendor


def _user_row(u: dict) -> dict:
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "fullName": u.get("fullname"),
        "role": u.get("role"),  # TenantAdmin | User | VendorManager (label en el frontend)
        "status": u.get("status"),  # Activated | Deactivated | PendingActivation
        "lastLogin": u.get("lastLogin"),  # epoch ms
        "appVersion": u.get("appVersion"),
    }


@router.get("/alero-users")
async def alero_users(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client) -> list[dict]:
        rows = await client.list_users()
        return [_user_row(u) for u in rows]

    async with alero_client(tenant_id, session) as client:
        rows = await _run_alero(session, op, tenant_id, "alero_user.list", fetch(client))
    return {"tenant": tenant_id, "count": len(rows), "users": rows}


def _user_detail_row(u: dict, invited_by_name: str | None) -> dict:
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "firstName": u.get("firstName"),
        "lastName": u.get("lastName"),
        "role": u.get("role"),
        "status": u.get("status"),
        "phoneNumber": u.get("phoneNumber"),
        "phoneModel": u.get("phoneModel"),
        "phoneOS": u.get("phoneOS"),
        "appVersion": u.get("appVersion"),
        "lastLogin": u.get("lastLogin"),
        "invitedBy": invited_by_name,
    }


@router.get("/alero-users/{user_id}")
async def alero_user_detail(
    tenant_id: str,
    user_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client) -> dict:
        u = await client.get_user_detail(user_id)
        # invitedBy en UserResponse es un ID, no un nombre (a diferencia de
        # VendorResponse, que ya trae el nombre completo) -- se resuelve
        # contra list_users(); si no aparece (usuario borrado/inactivo,
        # visto contra Globex real), se omite en vez de mostrar un ID
        # crudo sin sentido para el operador.
        invited_by_name = None
        raw_invited_by = u.get("invitedBy")
        if raw_invited_by:
            for candidate in await client.list_users():
                if candidate.get("id") == raw_invited_by:
                    invited_by_name = candidate.get("fullname")
                    break
        return _user_detail_row(u, invited_by_name)

    async with alero_client(tenant_id, session) as client:
        user = await _run_alero(
            session, op, tenant_id, "alero_user.get", fetch(client), target=user_id
        )
    return user


def _role_row(g: dict) -> dict:
    return {
        "id": g.get("id"),
        "name": g.get("name"),
        "description": g.get("description"),
        "vendors": g.get("numOfVendors"),
        "invitations": g.get("numOfInvitations"),
    }


@router.get("/alero-roles")
async def alero_roles(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """ "Identity roles" del portal de Alero -- roles de CyberArk Identity
    usados para asignar acceso a vendors (`type == "IdaptiveRole"`, ver
    `AleroClient.list_groups`). Los grupos nativos de Alero
    (`ProvisionedByAlero`) quedan afuera, igual que en esa pantalla del
    portal."""

    async def fetch(client) -> list[dict]:
        rows = await client.list_groups()
        return [_role_row(g) for g in rows if g.get("type") == "IdaptiveRole"]

    async with alero_client(tenant_id, session) as client:
        rows = await _run_alero(session, op, tenant_id, "alero_role.list", fetch(client))
    return {"tenant": tenant_id, "count": len(rows), "roles": rows}


# Traducción del enum `activityType` (BaseActivity, ver AleroClient) a un
# verbo/acción legible. Cubre las 50 variantes del swagger -- la mayoría
# son eventos de config/administración de Alero mismo (conectores, sites,
# certificados), no sólo login; se muestran igual, con label genérico si
# el texto exacto no aporta demasiado por sí solo.
_ACTIVITY_LABELS: dict[str, str] = {
    "TenantLogin": "inició sesión",
    "ApplicationVendorLogin": "inició sesión en la aplicación",
    "ApplicationUserLogin": "inició sesión en la aplicación",
    "UserActivated": "activó a un usuario",
    "UserDeactivated": "desactivó a un usuario",
    "VendorActivated": "activó a un vendor",
    "VendorDeactivated": "desactivó a un vendor",
    "VendorUpdated": "actualizó a un vendor",
    "UserUpdated": "actualizó a un usuario",
    "UserRoleChanged": "cambió el rol de un usuario",
    "UserJoinTenant": "se unió al tenant",
    "VendorJoinTenant": "se unió al tenant",
    "UserDeleteFromTenant": "eliminó a un usuario del tenant",
    "VendorDeleteFromTenant": "eliminó a un vendor del tenant",
    "UserCreated": "creó un usuario",
    "CompanyUserInvitationCreate": "invitó a un usuario",
    "VendorInvitationCreate": "invitó a un vendor",
    "SiteCreated": "creó un site",
    "SiteUpdated": "actualizó un site",
    "SiteDeleted": "eliminó un site",
    "ApplicationCreated": "creó una aplicación",
    "ApplicationUpdated": "actualizó una aplicación",
    "ApplicationDeleted": "eliminó una aplicación",
    "ApplicationEnabled": "habilitó una aplicación",
    "ApplicationDisabled": "deshabilitó una aplicación",
    "ConnectorCreated": "creó un conector",
    "ConnectorDeleted": "eliminó un conector",
    "ConnectorUpdated": "actualizó un conector",
    "ConnectorInitialized": "inicializó un conector",
    "ConnectorInitializationExtended": "extendió la inicialización de un conector",
    "ConnectorLdapUpdated": "actualizó la configuración LDAP de un conector",
    "ConnectorLdapInitialized": "inicializó LDAP en un conector",
    "ConnectorLdapStopped": "detuvo LDAP en un conector",
    "GroupsCreated": "creó un rol/grupo",
    "GroupsDeleted": "eliminó un rol/grupo",
    "GroupsUpdated": "actualizó un rol/grupo",
    "SettingsUpdated": "actualizó la configuración del tenant",
    "TenantAliasUpdated": "actualizó el alias del tenant",
    "TenantCreated": "creó el tenant",
    "AppCertificateCreated": "creó un certificado de aplicación",
    "AppCertificateDeleted": "eliminó un certificado de aplicación",
    "AppCertificateUpdated": "actualizó un certificado de aplicación",
    "ServiceAccountCreated": "creó un service account",
    "ServiceAccountDeleted": "eliminó un service account",
    "ServiceAccountActivated": "activó un service account",
    "ServiceAccountDeactivated": "desactivó un service account",
    "ApplicationLoginBlocked": "login bloqueado en una aplicación",
    "DirectAccessUserResponse": "respondió una solicitud de Direct Access",
    "DirectAccessConnectionDenied": "conexión de Direct Access denegada",
    "OfflineAccessUserViewedPassword": "vio la contraseña de una cuenta",
    "IdaptiveVendorSync": "sincronizó vendors con Identity",
    "IdaptiveRoleSync": "sincronizó roles con Identity",
    "CompanyInviterUpdated": "actualizó quién puede invitar",
}

_DETAIL_FIELDS = (
    "applicationName",
    "accountName",
    "appName",
    "siteName",
    "userLoginName",
    "reason",
)


def _activity_row(a: dict) -> dict:
    activity_type = a.get("activityType")
    data = a.get("activityData") or {}

    if activity_type == "OfflineAccessUserViewedPassword":
        # el par cuenta+safe es lo que de verdad importa acá -- es el
        # evento más sensible del feed para un tenant PAM.
        parts = [p for p in (data.get("accountName"), data.get("safeName")) if p]
        detail = " / ".join(parts) if parts else None
    else:
        detail = next((data[f] for f in _DETAIL_FIELDS if data.get(f)), None)

    return {
        "id": data.get("id"),
        "time": data.get("time"),
        "activityType": activity_type,
        "label": _ACTIVITY_LABELS.get(str(activity_type), activity_type) if activity_type else None,
        "actor": data.get("initiatedByName"),
        "actorRole": data.get("initiatedByRole"),
        "detail": detail,
        "sourceIp": data.get("sourceIp"),
    }


@router.get("/alero-activities")
async def alero_activities(
    tenant_id: str,
    hours: int = Query(24, ge=1, le=24 * 90, description="ventana hacia atrás, en horas"),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Feed "Activities" del portal de Alero -- login, altas/bajas de
    usuarios/vendors, cambios de config, etc. Sin filtro de tipo por ahora
    (trae todo el enum, ver `AleroClient.ALL_ACTIVITY_TYPES`); ordenado
    del más reciente al más viejo."""
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - hours * 3600_000

    async def fetch(client) -> list[dict]:
        rows = await client.list_activities(from_time_ms=from_ms, to_time_ms=now_ms)
        rows.sort(key=lambda a: (a.get("activityData") or {}).get("time", 0), reverse=True)
        return [_activity_row(a) for a in rows]

    async with alero_client(tenant_id, session) as client:
        rows = await _run_alero(session, op, tenant_id, "alero_activity.list", fetch(client))
    return {"tenant": tenant_id, "count": len(rows), "activities": rows}
