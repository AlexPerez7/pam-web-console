"""
Estado de la plataforma CyberArk (status.cyberark.com) — ver
`app/vendor_status.py` para el detalle de la API real. Recurso GLOBAL de
PAM Web Console (sin `tenant_id` en el path, mismo patrón que `knowledge_base.py`): no
es información de ningún tenant, es del FABRICANTE. Sólo exige estar
autenticado (`current_operator`, sin RBAC adicional) — es información
pública, sin exposición de datos de PAM de ningún cliente.

Cacheado en Redis (`PAM_VENDOR_STATUS_CACHE_TTL`, default 300s) para no
pegarle a status.cyberark.com en cada apertura de la página. Auditado
SIEMPRE (también en cache hit), mismo criterio que `dashboards.view`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import audit, cache
from app.core.deps import current_operator
from app.db import get_session
from app.models import Operator
from app.vendor_status import VendorStatusError, fetch_summary

router = APIRouter(prefix="/vendor-status", tags=["vendor-status"])

_CACHE_KEY = "pam:vendor-status:cyberark"


@router.get("/cyberark")
async def cyberark_status(
    refresh: bool = Query(False),
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = None if refresh else await cache.get_json(_CACHE_KEY)
    if data is not None:
        data = {**data, "cached": True}
    else:
        try:
            data = await fetch_summary()
        except VendorStatusError as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="vendor_status.view",
                result="error",
                detail={"error": str(e)},
            )
            await session.commit()
            raise HTTPException(status_code=503, detail=str(e)) from e
        data["cached"] = False
        await cache.set_json(_CACHE_KEY, data, ttl=get_settings().vendor_status_cache_ttl)

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="vendor_status.view",
        result="ok",
    )
    await session.commit()
    return data
