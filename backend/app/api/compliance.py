"""Sección Compliance: qué cuentas concretas arrastran el % de Compliance
del Dashboard hacia abajo, y por qué motivo — navegables, a diferencia del
Dashboard que sólo agrega. Ver app.analysis.compliance_breakdown.

Sin escritura ni feature-flag, mismo criterio que Troubleshooting: lectura,
cualquier viewer+ puede verla. Sin caché propia a propósito: es un
drill-down puntual (no una vista que se abre tan seguido como el
Dashboard), y mostrar el estado más fresco posible es justamente el punto
de "por qué está en rojo ESTA cuenta ahora".
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.core import audit, compliance_exemptions
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}/compliance", tags=["compliance"])

_viewer = require_tenant_access(OperatorRole.viewer)
_operator = require_tenant_access(OperatorRole.operator)

Reason = Literal[
    "PolicyFailures", "DisableByUser", "DisableByCPM", "Locked", "DisabledStale", "ActiveOverdue"
]

# reason (categoría del desglose de compliance_breakdown) -> savedFilter real
# de CyberArk. DisabledStale/ActiveOverdue no tienen savedFilter propio — se
# derivan del listado completo, ver analysis.disabled_stale_rows/
# active_overdue_rows.
_SAVED_FILTER: dict[str, str] = {
    "PolicyFailures": "PolicyFailures",
    "DisableByUser": "DisabledPasswordByUser",
    "DisableByCPM": "DisabledPasswordByCPM",
    "Locked": "Locked",
}
_DERIVED_REASONS = {"DisabledStale", "ActiveOverdue"}


@router.get("/accounts")
async def compliance_accounts(
    tenant_id: str,
    reason: Reason = Query(...),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        async with tenant_client(tenant_id, session) as (tenant, client):
            if reason in _DERIVED_REASONS:
                accounts, platforms = await asyncio.gather(
                    client.list_accounts(), client.list_platforms()
                )
                platform_days = analysis.platform_policy_days(platforms)
                exempt = await compliance_exemptions.exempt_ids(session, tenant_id)
                rows_fn = (
                    analysis.disabled_stale_rows
                    if reason == "DisabledStale"
                    else analysis.active_overdue_rows
                )
                rows = rows_fn(accounts, tenant.compliance_stale_days, platform_days, exempt)
            else:
                accounts = await client.saved_filter_accounts(_SAVED_FILTER[reason])
                rows = analysis.compliance_rows(accounts)
    except (NotFound, Forbidden) as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="compliance.accounts.view",
            result="error",
            tenant_id=tenant_id,
            detail={"reason": reason, "error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
    except CyberArkError as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="compliance.accounts.view",
            result="error",
            tenant_id=tenant_id,
            detail={"reason": reason, "error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="compliance.accounts.view",
        result="ok",
        tenant_id=tenant_id,
        detail={"reason": reason, "count": len(rows)},
    )
    await session.commit()
    return {"tenant": tenant_id, "reason": reason, "count": len(rows), "accounts": rows}


@router.get("/search")
async def compliance_search(
    tenant_id: str,
    q: str = Query(min_length=2, description="nombre / usuario / address / safe"),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Busca cuentas por término libre y devuelve, para cada una, TODAS las
    categorías de `compliance_breakdown` en las que cae (una cuenta puede
    estar en más de una a la vez, ej. `PolicyFailures` + `DisableByUser`) —
    a diferencia de `/accounts?reason=X`, que sólo mira una categoría por
    vez. Mismo criterio exacto que esa vista (sin `last_changed` vía
    versiones de password: cae al `lastModifiedTime` de la cuenta, igual
    que `disabled_stale_rows`/`active_overdue_rows`)."""
    try:
        async with tenant_client(tenant_id, session) as (tenant, client):
            (
                accounts,
                platforms,
                policy_failures,
                disabled_by_user,
                disabled_by_cpm,
                locked,
            ) = await asyncio.gather(
                client.list_accounts(search=q),
                client.list_platforms(),
                client.saved_filter_accounts("PolicyFailures"),
                client.saved_filter_accounts("DisabledPasswordByUser"),
                client.saved_filter_accounts("DisabledPasswordByCPM"),
                client.saved_filter_accounts("Locked"),
            )
            default_stale_days = tenant.compliance_stale_days
    except (NotFound, Forbidden) as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="compliance.search",
            result="error",
            tenant_id=tenant_id,
            detail={"q": q, "error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
    except CyberArkError as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="compliance.search",
            result="error",
            tenant_id=tenant_id,
            detail={"q": q, "error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    platform_days = analysis.platform_policy_days(platforms)
    exempt = await compliance_exemptions.exempt_ids(session, tenant_id)
    policy_ids = {a.get("id") for a in policy_failures}
    user_ids = {a.get("id") for a in disabled_by_user}
    cpm_ids = {a.get("id") for a in disabled_by_cpm}
    locked_ids = {a.get("id") for a in locked}

    rows = []
    for acc in accounts:
        acc_id = acc.get("id")
        status = analysis.account_compliance(
            acc,
            is_policy_failure=acc_id in policy_ids,
            default_stale_days=default_stale_days,
            platform_days=platform_days,
            exempt_ids=exempt,
        )
        reasons = list(status["reasons"])
        if acc_id in user_ids:
            reasons.append("DisableByUser")
        if acc_id in cpm_ids:
            reasons.append("DisableByCPM")
        if acc_id in locked_ids:
            reasons.append("Locked")
        rows.append(
            {
                "id": acc_id,
                "name": acc.get("name"),
                "userName": acc.get("userName"),
                "address": acc.get("address"),
                "safeName": acc.get("safeName"),
                "platformId": acc.get("platformId"),
                "compliant": not reasons,
                "reasons": reasons,
                "ageDays": status["age_days"],
                "thresholdDays": status["threshold_days"],
            }
        )

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="compliance.search",
        result="ok",
        tenant_id=tenant_id,
        detail={"q": q, "count": len(rows)},
    )
    await session.commit()
    return {"tenant": tenant_id, "query": q, "count": len(rows), "accounts": rows}


@router.get("/accounts/{account_id}/status")
async def compliance_account_status(
    tenant_id: str,
    account_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Compliance de UNA cuenta puntual — alimenta la `ComplianceCard` del
    detalle de cuenta. Mismo criterio EXACTO que `compliance_breakdown`
    (ver `analysis.account_compliance`): cruza el `PolicyFailures` nativo
    de CyberArk con la política de ROTACIÓN de la plataforma de la cuenta
    (o el default del tenant si la plataforma no define una), gestionada
    o no. Sin caché propia, mismo criterio que `/accounts?reason=...`."""
    try:
        async with tenant_client(tenant_id, session) as (tenant, client):

            async def versions() -> list | None:
                # sólo metadata (fecha/autor, nunca el valor). Best-effort:
                # si falla (permisos del service account, API), se cae a
                # `lastModifiedTime` como antes.
                try:
                    return await client.list_password_versions(account_id)
                except CyberArkError:
                    return None

            account, platforms, policy_failures, pw_versions = await asyncio.gather(
                client.get_account(account_id),
                client.list_platforms(),
                client.saved_filter_accounts("PolicyFailures"),
                versions(),
            )
            default_stale_days = tenant.compliance_stale_days
        platform_days = analysis.platform_policy_days(platforms)
        exempt = await compliance_exemptions.exempt_ids(session, tenant_id)
        is_policy_failure = any(a.get("id") == account_id for a in policy_failures)
        status = analysis.account_compliance(
            account,
            is_policy_failure=is_policy_failure,
            default_stale_days=default_stale_days,
            platform_days=platform_days,
            last_changed=analysis.last_password_change(pw_versions),
            exempt_ids=exempt,
        )
    except (NotFound, Forbidden) as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="compliance.account_status.view",
            result="error",
            tenant_id=tenant_id,
            target=account_id,
            detail={"error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
    except CyberArkError as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="compliance.account_status.view",
            result="error",
            tenant_id=tenant_id,
            target=account_id,
            detail={"error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="compliance.account_status.view",
        result="ok",
        tenant_id=tenant_id,
        target=account_id,
        detail={"compliant": status["compliant"], "reasons": status["reasons"]},
    )
    await session.commit()
    return {"tenant": tenant_id, "account_id": account_id, **status}


class _ExemptionCreate(BaseModel):
    account_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


@router.get("/exemptions")
async def list_compliance_exemptions(
    tenant_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Qué cuentas están excluidas del % de Compliance y por qué (R4):
    nada se excluye en silencio, cualquier viewer+ puede ver la lista."""
    rows = await compliance_exemptions.list_exemptions(session, tenant_id)
    return {
        "tenant": tenant_id,
        "exemptions": [
            {
                "account_id": r.account_id,
                "reason": r.reason,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.post("/exemptions", status_code=201)
async def create_compliance_exemption(
    tenant_id: str,
    body: _ExemptionCreate,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Marca una cuenta como excluida del % de Compliance, con motivo
    obligatorio (R2) — ver docs/specs/compliance-exemptions.md. No toca
    CyberArk: metadata propia de PAM Web Console."""
    row = await compliance_exemptions.create_exemption(
        session,
        tenant_id=tenant_id,
        account_id=body.account_id,
        reason=body.reason,
        created_by=op.username,
    )
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="compliance.exemption.create",
        result="ok",
        tenant_id=tenant_id,
        target=body.account_id,
        detail={"account_id": body.account_id, "reason": body.reason},
    )
    await session.commit()
    return {
        "tenant": tenant_id,
        "account_id": row.account_id,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
    }


@router.delete("/exemptions/{account_id}", status_code=204)
async def delete_compliance_exemption(
    tenant_id: str,
    account_id: str,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Saca una excepción de compliance activa (R3)."""
    await compliance_exemptions.delete_exemption(
        session, tenant_id=tenant_id, account_id=account_id
    )
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="compliance.exemption.delete",
        result="ok",
        tenant_id=tenant_id,
        target=account_id,
    )
    await session.commit()
