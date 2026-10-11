"""Job de sync nocturno de `account_snapshots` para todos los tenants
Privilege Cloud -- ver docs/specs/jobs-programados.md. Reusa
`inventory_sync.sync_accounts` (la misma función del sync on-demand con
TTL, `app/api/inventory.py`), sólo agrega el fan-out por tenant y la
ejecución fuera de un request de FastAPI.

No usa `app.core.tenants.tenant_client`: ese helper levanta
`HTTPException` ante cualquier falla (pensado para un request real), y
acá un tenant que falla no debe abortar el resto -- mismo criterio (y
mismo patrón de construir el cliente a mano) que `app/api/search.py`
`_search_one` y `app/api/system_health.py` `_snapshot_for_fanout`.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import audit, inventory_sync
from app.core.secrets import SecretStoreError, get_secret
from app.cyberark import CyberArkClient, CyberArkError, build_client
from app.cyberark.auth import AuthError
from app.db import SessionLocal
from app.models import Deployment, Tenant

logger = logging.getLogger(__name__)


async def _audit(session: AsyncSession, tenant_id: str, *, result: str, detail: dict) -> None:
    # Primer job de PAM Web Console sin un operador humano real detrás -- convención
    # nueva para `audit.record()` (operator/role son strings libres, sin
    # FK, ver app/models/audit.py).
    await audit.record(
        session,
        operator="system",
        role="system",
        action="account.sync",
        result=result,
        tenant_id=tenant_id,
        detail=detail,
    )
    await session.commit()


async def _sync_one_tenant(tenant: Tenant) -> None:
    """Nunca lanza -- un tenant que falla se audita como error y no
    aborta el resto. Una sola sesión/transacción por tenant: si
    `sync_accounts` falla a mitad de la paginación, lo ya upserteado
    hasta ahí se commitea junto con el audit de error -- mismo criterio
    que el sync on-demand (`app/api/inventory.py`, catch de
    `CyberArkError` alrededor de `sync_accounts`).

    El último `except Exception` (2026-09-23, incidente real en el
    server: un bug de `accounts_page` -- ya arreglado, ver
    `app/cyberark/client.py` -- escapó de la jerarquía de `CyberArkError`
    como `AttributeError` y tumbó el proceso entero, dejando sin
    sincronizar a los tenants que quedaban después en el loop) es
    deliberado pese a ser ancho: acá el costo de un bug no previsto en UN
    tenant es mucho más caro que en un endpoint (aborta la corrida
    completa del cron, no sólo un request) -- mismo criterio que
    `app/api/search.py` `_search_one`."""
    max_conc = get_settings().cyberark_max_concurrency
    async with SessionLocal() as session:
        try:
            secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
        except SecretStoreError as e:
            logger.warning("sync nocturno: %s secret store falló: %s", tenant.id, e)
            await _audit(session, tenant.id, result="error", detail={"error": f"secret store: {e}"})
            return

        client: CyberArkClient = build_client(
            tenant,
            secret,
            max_concurrency=max_conc,
            token_cache_seconds=get_settings().cyberark_token_cache_seconds,
        )
        try:
            await client.authenticate()
            count = await inventory_sync.sync_accounts(tenant.id, client, session)
        except AuthError as e:
            logger.warning("sync nocturno: %s auth falló: %s", tenant.id, e)
            await _audit(session, tenant.id, result="error", detail={"error": f"auth: {e}"})
            return
        except CyberArkError as e:
            logger.warning("sync nocturno: %s CyberArk falló: %s", tenant.id, e)
            await _audit(session, tenant.id, result="error", detail={"error": str(e)})
            return
        except Exception as e:  # ancho a propósito -- ver docstring
            logger.exception("sync nocturno: %s falló con un error no previsto", tenant.id)
            await _audit(
                session,
                tenant.id,
                result="error",
                detail={"error": f"{type(e).__name__}: {e}"},
            )
            return
        finally:
            await client.close()

        await _audit(session, tenant.id, result="ok", detail={"count": count})
    logger.info("sync nocturno: %s -> %d cuentas", tenant.id, count)


async def sync_all_tenants_accounts(ctx: dict) -> dict:
    """Job ARQ (cron nocturno, ver `app.jobs.worker.WorkerSettings`).
    `ctx` es el contexto estándar de ARQ (redis, job_id, ...), sin uso
    acá."""
    async with SessionLocal() as session:
        tenants = (
            (
                await session.execute(
                    select(Tenant).where(
                        Tenant.deployment == Deployment.privilege_cloud,
                        Tenant.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

    logger.info("sync nocturno: arrancando para %d tenants", len(tenants))
    for tenant in tenants:
        await _sync_one_tenant(tenant)
    return {"tenants": len(tenants)}
