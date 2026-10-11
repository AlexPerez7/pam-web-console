"""Job ARQ on-demand para reportes "heavy" (Batch 4 de
`docs/specs/done/reportes.md`: `target-inactivity`, `account-activity` --
necesitan actividad por cuenta, no entran en un request HTTP). A
diferencia de `app.jobs.inventory.sync_all_tenants_accounts` (cron
nocturno para TODOS los tenants), este se encola bajo demanda para UN
tenant puntual desde `POST /tenants/{id}/reports/{name}/run-job`.

Reusa `Report.run` tal cual (mismo que corren los reportes síncronos en
`app.api.reports._compute`) -- lo único que cambia es quién lo llama y
cuándo. Al terminar, escribe el resultado en la MISMA cache key que usaría
un request síncrono (`app.api.reports._cache_key`), así `GET
/reports/{name}` y `.../export` sirven el resultado ya calculado sin
tener que recorrer las cuentas de nuevo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.api.reports import _cache_key
from app.config import get_settings
from app.core import audit, cache
from app.core.secrets import SecretStoreError, get_secret
from app.cyberark import build_client
from app.db import SessionLocal
from app.models import Tenant
from app.reports import REGISTRY
from app.reports.base import result_to_dict

logger = logging.getLogger(__name__)


async def _audit(
    session: Any,
    tenant_id: str,
    name: str,
    operator_username: str,
    operator_role: str,
    *,
    result: str,
    detail: dict,
) -> None:
    await audit.record(
        session,
        operator=operator_username,
        role=operator_role,
        action=f"report.{name}",
        result=result,
        tenant_id=tenant_id,
        target=name,
        detail=detail,
    )
    await session.commit()


async def run_heavy_report(
    ctx: dict,
    tenant_id: str,
    name: str,
    params: dict[str, Any],
    operator_username: str,
    operator_role: str,
) -> dict:
    """`ctx` es el contexto estándar de ARQ (redis, job_id, ...), sin uso
    acá. Atribuido al operador real que lo pidió (no "system" como el cron
    de inventario) -- se pasa explícito porque el job corre en el worker,
    sin el `Depends(current_operator)` de un request HTTP."""
    report = REGISTRY[name]
    async with SessionLocal() as session:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            raise ValueError(f"tenant '{tenant_id}' no existe")

        try:
            secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
        except SecretStoreError as e:
            await _audit(
                session,
                tenant_id,
                name,
                operator_username,
                operator_role,
                result="error",
                detail={"error": f"secret store: {e}"},
            )
            raise

        max_conc = get_settings().cyberark_max_concurrency
        client = build_client(
            tenant,
            secret,
            max_concurrency=max_conc,
            token_cache_seconds=get_settings().cyberark_token_cache_seconds,
        )
        try:
            await client.authenticate()
            result = await report.run(client, report.run_params(tenant, params))
        except Exception as e:
            logger.exception("reporte %s (%s): falló", name, tenant_id)
            await _audit(
                session,
                tenant_id,
                name,
                operator_username,
                operator_role,
                result="error",
                detail={"error": f"{type(e).__name__}: {e}"},
            )
            raise
        finally:
            await client.close()

        data = result_to_dict(result)
        data["cached"] = False
        data["generated_at"] = datetime.now(UTC).isoformat()
        await cache.set_json(
            _cache_key(tenant_id, name, params), data, get_settings().report_cache_ttl
        )

        await _audit(
            session,
            tenant_id,
            name,
            operator_username,
            operator_role,
            result="ok",
            detail={"params": params, "row_count": len(data["rows"])},
        )
    return data
