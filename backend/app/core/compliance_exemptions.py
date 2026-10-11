"""Excepciones manuales de compliance por cuenta (ver
docs/specs/compliance-exemptions.md): cuentas con la gestión automática
deshabilitada a pedido del cliente, para siempre, que no deben restar del
% de Compliance. Viven en la base de PAM Web Console (`compliance_exemptions`), no en
CyberArk — helpers de acceso a datos compartidos por
`app.api.compliance`/`app.api.dashboards`/`app.api.itr`, para no duplicar
la query en cada uno.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComplianceExemption


async def exempt_ids(session: AsyncSession, tenant_id: str) -> set[str]:
    """`account_id` de todas las excepciones activas del tenant."""
    rows = (
        await session.execute(
            select(ComplianceExemption.account_id).where(ComplianceExemption.tenant_id == tenant_id)
        )
    ).scalars()
    return set(rows)


async def list_exemptions(session: AsyncSession, tenant_id: str) -> list[ComplianceExemption]:
    rows = (
        await session.execute(
            select(ComplianceExemption)
            .where(ComplianceExemption.tenant_id == tenant_id)
            .order_by(ComplianceExemption.created_at.desc())
        )
    ).scalars()
    return list(rows)


async def create_exemption(
    session: AsyncSession, *, tenant_id: str, account_id: str, reason: str, created_by: str
) -> ComplianceExemption:
    row = ComplianceExemption(
        id=secrets.token_hex(16),
        tenant_id=tenant_id,
        account_id=account_id,
        reason=reason,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="La cuenta ya tiene una excepción de compliance activa."
        ) from exc
    return row


async def delete_exemption(session: AsyncSession, *, tenant_id: str, account_id: str) -> None:
    result = await session.execute(
        delete(ComplianceExemption).where(
            ComplianceExemption.tenant_id == tenant_id,
            ComplianceExemption.account_id == account_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="No hay una excepción activa para esa cuenta.")
