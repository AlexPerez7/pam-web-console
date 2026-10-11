"""Campañas de rotación/remediación (Fase 4) — un lote nombrado de
cuentas, típicamente armado desde Troubleshooting, con seguimiento de
avance calculado en vivo (sin historial persistido). Sin feature-flag:
crear/borrar una campaña no toca CyberArk, es bookkeeping propio de PAM Web Console.
Ver docs/specs/done/rotation-campaigns.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.core import audit
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.core.writeop import MIN_REASON_LEN
from app.cyberark import CyberArkError
from app.db import get_session
from app.domain.campaigns import campaign_progress, campaign_summary
from app.models import Campaign, CampaignAccount, Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}/campaigns", tags=["campaigns"])

_viewer = require_tenant_access(OperatorRole.viewer)
_operator = require_tenant_access(OperatorRole.operator)


async def _live_accounts(tenant_id: str, session: AsyncSession) -> tuple[dict[str, dict], set]:
    """Una sola `list_accounts()` por request, reusada para todas las
    campañas que aparezcan en la respuesta — evita N requests a
    CyberArk por N campañas."""
    async with tenant_client(tenant_id, session) as (_, client):
        accounts = await client.list_accounts()
    by_id = {a.get("id"): a for a in accounts if a.get("id")}
    failed_ids = analysis.analyze_accounts(accounts)["failed_ids"]
    return by_id, failed_ids


def _member_dict(m: CampaignAccount) -> dict[str, Any]:
    return {
        "account_id": m.account_id,
        "account_name": m.account_name,
        "safe_name": m.safe_name,
        "platform_id": m.platform_id,
    }


def _campaign_header(c: Campaign) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "reason": c.reason,
        "created_by": c.created_by,
        "created_at": c.created_at,
    }


@router.get("")
async def list_campaigns(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        select(Campaign).where(Campaign.tenant_id == tenant_id).order_by(Campaign.created_at.desc())
    )
    campaigns = result.scalars().all()

    by_id: dict[str, dict] = {}
    failed_ids: set = set()
    if campaigns:
        try:
            by_id, failed_ids = await _live_accounts(tenant_id, session)
        except CyberArkError as e:
            raise HTTPException(status_code=502, detail=str(e))

    rows = []
    for c in campaigns:
        progress = campaign_progress([_member_dict(m) for m in c.accounts], by_id, failed_ids)
        rows.append({**_campaign_header(c), **campaign_summary(progress)})

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="campaign.list",
        result="ok",
        tenant_id=tenant_id,
        detail={"count": len(rows)},
    )
    await session.commit()
    return {"tenant": tenant_id, "campaigns": rows}


class CampaignCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    reason: str = Field(min_length=MIN_REASON_LEN)
    account_ids: list[str] = Field(min_length=1)


@router.post("")
async def create_campaign(
    tenant_id: str,
    body: CampaignCreateIn,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        by_id, _failed_ids = await _live_accounts(tenant_id, session)
    except CyberArkError as e:
        raise HTTPException(status_code=502, detail=str(e))

    members: list[CampaignAccount] = []
    missing: list[str] = []
    for account_id in body.account_ids:
        acc = by_id.get(account_id)
        if acc is None:
            missing.append(account_id)
            continue
        members.append(
            CampaignAccount(
                account_id=account_id,
                account_name=str(acc.get("name") or account_id),
                safe_name=str(acc.get("safeName") or ""),
                platform_id=str(acc.get("platformId") or ""),
            )
        )
    if not members:
        raise HTTPException(
            status_code=422, detail="ninguna de las cuentas indicadas existe en el tenant"
        )

    campaign = Campaign(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        reason=body.reason,
        created_by=op.username,
        accounts=members,
    )
    session.add(campaign)
    await session.flush()
    campaign_id = campaign.id

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="campaign.create",
        result="ok",
        tenant_id=tenant_id,
        target=str(campaign_id),
        detail={
            "name": body.name,
            "reason": body.reason,
            "members": len(members),
            "missing": missing,
        },
    )
    await session.commit()
    return {"id": campaign_id, "members": len(members), "missing": missing}


@router.get("/{campaign_id}")
async def get_campaign(
    tenant_id: str,
    campaign_id: int,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="campaña no encontrada")

    try:
        by_id, failed_ids = await _live_accounts(tenant_id, session)
    except CyberArkError as e:
        raise HTTPException(status_code=502, detail=str(e))

    rows = campaign_progress([_member_dict(m) for m in campaign.accounts], by_id, failed_ids)
    summary = campaign_summary(rows)

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="campaign.get",
        result="ok",
        tenant_id=tenant_id,
        target=str(campaign_id),
        detail=summary,
    )
    await session.commit()
    return {**_campaign_header(campaign), "accounts": rows, **summary}


@router.delete("/{campaign_id}")
async def delete_campaign(
    tenant_id: str,
    campaign_id: int,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="campaña no encontrada")

    name = campaign.name
    await session.delete(campaign)
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="campaign.delete",
        result="ok",
        tenant_id=tenant_id,
        target=str(campaign_id),
        detail={"name": name},
    )
    await session.commit()
    return {"result": "ok"}
