"""Setup > Connector management: conectores del sistema, conectores SIA y
pools de un tenant. Sólo lectura (corte 1). Ver
docs/specs/setup-connector-management.md.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.inventory import _run
from app.core import cache
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, Forbidden
from app.db import get_session
from app.domain.connectors import connector_rows, pool_rows, sia_row
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}/setup", tags=["setup"])

_viewer = require_tenant_access(OperatorRole.viewer)

_CACHE_TTL = 30  # s, como el resto de las lecturas en vivo de PAM Web Console

_NO_CM_URL = "Este tenant no tiene configurada la URL de Connector Management."
_NO_SIA_URL = "Este tenant no tiene configurada la URL de SIA."
_SIA_FORBIDDEN = (
    "SIA no está licenciado o el service account no tiene el rol SIA Admin en este tenant."
)


async def _cached(tenant_id: str, kind: str, fetch) -> dict:
    key = f"setup:{kind}:{tenant_id}"
    hit = await cache.get_json(key)
    if hit is not None:
        return hit
    data = await fetch()
    await cache.set_json(key, data, _CACHE_TTL)
    return data


async def _cm_data(client: CyberArkClient) -> dict:
    connectors, pools = await asyncio.gather(
        client.list_connectors(), client.list_connector_pools()
    )
    return {"connectors": connectors, "pools": pools}


@router.get("/connectors")
async def list_connectors(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        if not client.connector_management_url:
            raise HTTPException(status_code=409, detail=_NO_CM_URL)

        async def rows_fn() -> list:
            data = await _cached(tenant_id, "cm", lambda: _cm_data(client))
            return connector_rows(data["connectors"], data["pools"])

        rows = await _run(session, op, tenant_id, "setup.connectors.list", rows_fn())
    return {"tenant": tenant_id, "count": len(rows), "connectors": rows}


@router.get("/connector-pools")
async def list_connector_pools(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        if not client.connector_management_url:
            raise HTTPException(status_code=409, detail=_NO_CM_URL)

        async def rows_fn() -> list:
            data = await _cached(tenant_id, "cm", lambda: _cm_data(client))
            return pool_rows(data["pools"], data["connectors"])

        rows = await _run(session, op, tenant_id, "setup.connector_pools.list", rows_fn())
    return {"tenant": tenant_id, "count": len(rows), "pools": rows}


@router.get("/sia-connectors")
async def list_sia_connectors(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        if not client.sia_connector_url:
            raise HTTPException(status_code=409, detail=_NO_SIA_URL)

        async def fetch() -> dict:
            return {"items": await client.list_sia_connectors()}

        async def rows_fn() -> list:
            try:
                data = await _cached(tenant_id, "sia", fetch)
            except Forbidden as e:
                raise Forbidden(_SIA_FORBIDDEN) from e
            return [sia_row(c) for c in data["items"]]

        rows = await _run(session, op, tenant_id, "setup.sia_connectors.list", rows_fn())
    return {"tenant": tenant_id, "count": len(rows), "connectors": rows}
