"""Sección Troubleshooting: errores de rotación/verificación del CPM por
cuenta, navegables (a diferencia del Dashboard, que sólo los agrega). Ver
docs/specs/troubleshooting-errores-cpm.md.

Sin escritura ni feature-flag — es lectura, mismo criterio que Dashboard/
Inventario: cualquier `viewer+` puede verla.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.config import get_settings
from app.core import audit, cache
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}/troubleshooting", tags=["troubleshooting"])

_viewer = require_tenant_access(OperatorRole.viewer)


async def _compute(tenant_id: str, session: AsyncSession) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        accounts = await client.list_accounts()
    acc = analysis.analyze_accounts(accounts)
    cpm = analysis.analyze_cpm_errors(accounts, acc["failed_ids"])
    rows = analysis.failed_accounts_rows(accounts, acc["failed_ids"])
    return {
        "tenant": tenant_id,
        "cached": False,
        "summary": {
            "failed_total": cpm["failed_total"],
            "active_retry": cpm["active_retry"],
            "given_up": cpm["given_up"],
            "by_reason": [{"label": k, "value": v} for k, v in cpm["by_reason"].most_common()],
        },
        "accounts": rows,
    }


@router.get("/account-errors")
async def account_errors(
    tenant_id: str,
    refresh: bool = Query(False),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    key = f"pam:troubleshooting:{tenant_id}"
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
                action="troubleshooting.account_errors.view",
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
                action="troubleshooting.account_errors.view",
                result="error",
                tenant_id=tenant_id,
                detail={"error": str(e)},
            )
            await session.commit()
            raise HTTPException(status_code=502, detail=str(e))
        await cache.set_json(key, data, get_settings().troubleshooting_cache_ttl)

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="troubleshooting.account_errors.view",
        result="ok",
        tenant_id=tenant_id,
        detail={"cached": data["cached"], "failed_total": data["summary"]["failed_total"]},
    )
    await session.commit()
    return data
