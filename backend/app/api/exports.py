"""Export de inventario a CSV.

Un endpoint por recurso (`accounts`/`safes`/`platforms`/`users`/`groups`):
trae el dataset completo del tenant, lo pasa por `app.core.csv_export` con
columnas curadas y lo devuelve como archivo adjunto. Read-only y **auditado**
(`<recurso>.export`) — una lectura masiva de un tenant tiene que quedar
registrada igual que un `list`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.csv_export import Column, dotted, epoch_iso, flag, to_csv
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["export"])

_viewer = require_tenant_access(OperatorRole.viewer)


_FETCH: dict[str, Callable[[CyberArkClient], Awaitable[list]]] = {
    "accounts": lambda c: c.list_accounts(),
    "safes": lambda c: c.list_safes(),
    "platforms": lambda c: c.list_platforms(),
    "users": lambda c: c.list_users(),
    "groups": lambda c: c.list_user_groups(),
}

_COLUMNS: dict[str, list[Column]] = {
    "accounts": [
        ("id", dotted("id")),
        ("name", dotted("name")),
        ("userName", dotted("userName")),
        ("address", dotted("address")),
        ("platformId", dotted("platformId")),
        ("safeName", dotted("safeName")),
        ("status", dotted("secretManagement.status")),
        ("autoManaged", lambda r: flag(dotted("secretManagement.automaticManagementEnabled")(r))),
        ("lastModified", lambda r: epoch_iso(dotted("secretManagement.lastModifiedTime")(r))),
        ("createdTime", lambda r: epoch_iso(r.get("createdTime"))),
    ],
    "safes": [
        ("safeName", dotted("safeName")),
        ("safeUrlId", dotted("safeUrlId")),
        ("description", dotted("description")),
        ("managingCPM", dotted("managingCPM")),
        ("numberOfDaysRetention", dotted("numberOfDaysRetention")),
        ("olacEnabled", lambda r: flag(r.get("olacEnabled") or r.get("OLACEnabled"))),
        ("creationTime", lambda r: epoch_iso(r.get("creationTime"))),
    ],
    "platforms": [
        ("id", dotted("general.id")),
        ("name", dotted("general.name")),
        ("systemType", dotted("general.systemType")),
        ("active", lambda r: flag(dotted("general.active")(r))),
    ],
    "users": [
        ("id", dotted("id")),
        ("username", dotted("username")),
        ("userType", dotted("userType")),
        ("source", dotted("source")),
        ("componentUser", lambda r: flag(r.get("componentUser"))),
        ("firstName", dotted("personalDetails.firstName")),
        ("lastName", dotted("personalDetails.lastName")),
        ("suspended", lambda r: flag(r.get("suspended"))),
        ("enabled", lambda r: flag(r.get("enableUser"))),
        ("lastLogin", lambda r: epoch_iso(r.get("lastSuccessfulLoginDate"))),
    ],
    "groups": [
        ("id", dotted("id")),
        ("groupName", dotted("groupName")),
        ("groupType", dotted("groupType")),
        ("directory", dotted("directory")),
        ("description", dotted("description")),
    ],
}

# acción de audit: singular del recurso + ".export"
_ACTION = {
    "accounts": "account.export",
    "safes": "safe.export",
    "platforms": "platform.export",
    "users": "user.export",
    "groups": "group.export",
}


@router.get("/export/{resource}")
async def export_resource(
    tenant_id: str,
    resource: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if resource not in _FETCH:
        raise HTTPException(status_code=404, detail=f"recurso '{resource}' no exportable")

    action = _ACTION[resource]
    try:
        async with tenant_client(tenant_id, session) as (_, client):
            rows = await _FETCH[resource](client)
    except (NotFound, Forbidden) as e:
        await _audit(session, op, tenant_id, action, "error", {"error": str(e)})
        raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
    except CyberArkError as e:
        await _audit(session, op, tenant_id, action, "error", {"error": str(e)})
        raise HTTPException(status_code=502, detail=str(e))

    await _audit(session, op, tenant_id, action, "ok", {"count": len(rows), "format": "csv"})

    body = to_csv(rows, _COLUMNS[resource])
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"pam-{tenant_id}-{resource}-{stamp}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


async def _audit(
    session: AsyncSession,
    op: Operator,
    tenant_id: str,
    action: str,
    result: str,
    detail: dict,
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action=action,
        result=result,
        tenant_id=tenant_id,
        detail=detail,
    )
    await session.commit()
