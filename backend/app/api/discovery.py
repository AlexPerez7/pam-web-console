"""Fase 4: cuentas pendientes descubiertas por CyberArk Discovery (Gen2,
Privilege Cloud/ISPSS -- alcance v1 de PAM Web Console), aún sin onboardear al Vault.

`GET .../api/discovered-accounts/` y el resto de esta API viven en el
dominio base del tenant SIN el prefix `/PasswordVault` -- confirmado contra
la documentación oficial de CyberArk (`docs.cyberark.com/manage/latest/en/
content/discovery/discovery-apis/discovery-account-discovery.htm`), no
contra un endpoint Gen1 adivinado (el primer corte de esta feature probaba
`/API/DiscoveredAccounts` con `/PasswordVault` primero -- nunca confirmado
contra la doc, sacado en esta revisión). Ver `app/cyberark/client.py`
(`CyberArkClient._discovery_url`) y `docs/specs/done/discovery.md`.

Onboardear/ignorar/restaurar/borrar son operaciones de escritura reales
contra CyberArk -> pasan por `writeop.run_write` (reason + dry-run +
rate-limit + audit before/after), mismo criterio que el resto de PAM Web Console.
Ninguna necesita confirmación reforzada (`approver+`): onboardear es
riesgo equivalente a `account.create` (alta nueva, no toca nada
existente); ignorar/restaurar/borrar son reversibles y no afectan cuentas
ya onboardeadas -- todas `operator+`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.inventory import _run
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.core.writeop import Plan, run_write
from app.cyberark import CyberArkClient
from app.db import get_session
from app.domain.discovery import (
    plan_discovered_account_delete,
    plan_discovered_account_ignore,
    plan_discovered_account_onboard,
    plan_discovered_account_restore,
)
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["discovery"])

_viewer = require_tenant_access(OperatorRole.viewer)
_operator = require_tenant_access(OperatorRole.operator)


@router.get("/discovered-accounts")
async def list_discovered_accounts(
    tenant_id: str,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        result = await _run(
            session,
            op,
            tenant_id,
            "discovered_account.list",
            client.list_discovered_accounts(search=search, offset=offset, limit=limit),
        )
    return {
        "tenant": tenant_id,
        "accounts": result.get("items", []),
        "total": result.get("totalCount", 0),
    }


@router.get("/discovered-accounts/{discovered_account_id}/activities")
async def get_discovered_account_activities(
    tenant_id: str,
    discovered_account_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        activities = await _run(
            session,
            op,
            tenant_id,
            "discovered_account.activities",
            client.get_discovered_account_activities(discovered_account_id),
            target=discovered_account_id,
        )
    return {"tenant": tenant_id, "activities": activities}


class DiscoveredAccountOnboardChanges(BaseModel):
    safe_name: str = Field(min_length=1)
    platform_id: str = Field(min_length=1)
    secret: str | None = None
    reset_secret: bool = True
    tags: list[str] | None = None
    additional_properties: dict | None = None


class DiscoveredAccountOnboardIn(BaseModel):
    changes: DiscoveredAccountOnboardChanges
    dry_run: bool = True
    reason: str | None = None


@router.post("/discovered-accounts/{discovered_account_id}/onboard")
async def onboard_discovered_account(
    tenant_id: str,
    discovered_account_id: str,
    body: DiscoveredAccountOnboardIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Onboardea vía el endpoint REAL de Discovery (`.../onboard`) -- ya no
    reusa `account.create` (workaround del primer corte: mandaba un body
    que no coincidía con lo que espera `POST /accounts` y encima apuntaba
    a una ruta inexistente). `changes` anidado (no campos sueltos en el
    body) para que el frontend pueda manejar esto con el `<DryRunDialog>`
    genérico, igual que `account.create`/`safe.create`. `secret` viaja
    dentro de `changes` pero `plan_discovered_account_onboard` nunca lo
    lee -- no termina en el diff/audit, mismo criterio que
    `plan_account_create`."""
    changes = body.changes

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_discovered_account(discovered_account_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.onboard_discovered_account(
            discovered_account_id,
            safe_name=changes.safe_name,
            platform_id=changes.platform_id,
            additional_properties=changes.additional_properties,
            tags=changes.tags,
            secret=changes.secret,
            reset_secret=changes.reset_secret,
        )

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="discovered_account.onboard",
        target=discovered_account_id,
        changes=changes.model_dump(),
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_discovered_account_onboard,
        apply=apply,
    )


class DiscoveredAccountActionIn(BaseModel):
    dry_run: bool = True
    reason: str | None = None


async def _discovered_account_action(
    *,
    feature: str,
    tenant_id: str,
    discovered_account_id: str,
    body: DiscoveredAccountActionIn,
    request: Request,
    op: Operator,
    session: AsyncSession,
    build_plan: Callable[[dict, dict], Plan],
    do_apply: Callable[[CyberArkClient], Awaitable[None]],
) -> dict:
    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_discovered_account(discovered_account_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await do_apply(client)
        return {}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature=feature,
        target=discovered_account_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_plan,
        apply=apply,
    )


@router.post("/discovered-accounts/{discovered_account_id}/ignore")
async def ignore_discovered_account(
    tenant_id: str,
    discovered_account_id: str,
    body: DiscoveredAccountActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reusa `body.reason` (el mismo que exige el audit de PAM Web Console) como el
    `reason` opcional que CyberArk guarda junto a la cuenta ignorada -- no
    hace falta un segundo campo de texto en el diálogo."""
    return await _discovered_account_action(
        feature="discovered_account.ignore",
        tenant_id=tenant_id,
        discovered_account_id=discovered_account_id,
        body=body,
        request=request,
        op=op,
        session=session,
        build_plan=plan_discovered_account_ignore,
        do_apply=lambda client: client.ignore_discovered_account(
            discovered_account_id, reason=body.reason
        ),
    )


@router.post("/discovered-accounts/{discovered_account_id}/restore")
async def restore_discovered_account(
    tenant_id: str,
    discovered_account_id: str,
    body: DiscoveredAccountActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _discovered_account_action(
        feature="discovered_account.restore",
        tenant_id=tenant_id,
        discovered_account_id=discovered_account_id,
        body=body,
        request=request,
        op=op,
        session=session,
        build_plan=plan_discovered_account_restore,
        do_apply=lambda client: client.restore_discovered_account(discovered_account_id),
    )


@router.delete("/discovered-accounts/{discovered_account_id}")
async def delete_discovered_account(
    tenant_id: str,
    discovered_account_id: str,
    body: DiscoveredAccountActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """DELETE con body `{dry_run, reason}` -- mismo patrón que
    `account.delete`/`safe.delete` en `app/api/writes.py`."""
    return await _discovered_account_action(
        feature="discovered_account.delete",
        tenant_id=tenant_id,
        discovered_account_id=discovered_account_id,
        body=body,
        request=request,
        op=op,
        session=session,
        build_plan=plan_discovered_account_delete,
        do_apply=lambda client: client.delete_discovered_account(discovered_account_id),
    )
