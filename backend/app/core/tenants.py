"""
Helper: obtener un CyberArkClient autenticado para un tenant.

Junta las 3 piezas de Fase 0: registro de tenants (DB) + secret store
(app.core.secrets) + cliente async (app.cyberark). Un cliente NUEVO por
request (Fase 0-1); en Fase 1 se agrega pooling / caché de token.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.alero import AleroClient
from app.config import get_settings
from app.core.secrets import SecretStoreError, get_secret
from app.cyberark import CyberArkClient, build_client
from app.cyberark.auth import AuthError
from app.demo import demo_transport, is_demo
from app.models import Deployment, Tenant


@asynccontextmanager
async def tenant_client(
    tenant_id: str, session: AsyncSession
) -> AsyncIterator[tuple[Tenant, CyberArkClient]]:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not tenant.enabled:
        raise HTTPException(
            status_code=404, detail=f"tenant '{tenant_id}' no existe o está deshabilitado"
        )
    if tenant.deployment is not Deployment.privilege_cloud:
        raise HTTPException(status_code=400, detail="v1 solo opera tenants Privilege Cloud")

    try:
        secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
    except SecretStoreError as e:
        raise HTTPException(status_code=503, detail=f"secret store: {e}")

    s = get_settings()
    client = build_client(
        tenant,
        secret,
        max_concurrency=s.cyberark_max_concurrency,
        token_cache_seconds=s.cyberark_token_cache_seconds,
    )
    try:
        await client.authenticate()
    except AuthError as e:
        await client.close()
        raise HTTPException(status_code=502, detail=f"auth contra CyberArk falló: {e}")

    try:
        yield tenant, client
    finally:
        await client.close()


@asynccontextmanager
async def alero_client(tenant_id: str, session: AsyncSession) -> AsyncIterator[AleroClient]:
    """Igual que `tenant_client`, pero para CyberArk Remote Access (Alero):
    API separada, credenciales propias (no comparte token con Privilege
    Cloud). `tenant.alero_tenant_id` nulo = el cliente no tiene Remote
    Access -- 400, no 404 (el tenant existe, sólo no tiene esta feature)."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not tenant.enabled:
        raise HTTPException(
            status_code=404, detail=f"tenant '{tenant_id}' no existe o está deshabilitado"
        )
    if not tenant.alero_tenant_id:
        raise HTTPException(
            status_code=400,
            detail=f"tenant '{tenant_id}' no tiene Remote Access (Alero) configurado",
        )

    try:
        secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
    except SecretStoreError as e:
        raise HTTPException(status_code=503, detail=f"secret store: {e}")

    alero_secret = secret.get("alero")
    if not alero_secret:
        raise HTTPException(
            status_code=503,
            detail=f"faltan credenciales de Alero en el secret store para '{tenant.secret_ref}'",
        )

    client = AleroClient(
        tenant_id=tenant.alero_tenant_id,
        service_account_id=alero_secret["service_account_id"],
        private_key_pem=alero_secret["private_key"],
        discovery_uri=alero_secret["discovery_uri"],
        verify_ssl=tenant.verify_ssl,
        transport=demo_transport(tenant.id) if is_demo() else None,
    )
    try:
        yield client
    finally:
        await client.close()
