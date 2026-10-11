"""Fase 3: consulta y restauración de backups de cuentas borradas
(`account.delete`, ver `app.api.writes.account_delete`).

`reveal` no pasa por `writeop.run_write` (no escribe en CyberArk) pero sí
exige `reason` y audita `account.backup.reveal` — nunca el secreto.
`restore` recrea la cuenta reusando la maquinaria de `account.create`
(Fase 2), con una confirmación reforzada propia chequeada acá (no vía
`Plan.confirm_phrase`, para no tocar `account.create`). Ver
`docs/specs/account-safe-delete.md`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.backups import (
    AccountBackup,
    PlatformBackup,
    decrypt_secret,
    get_backup,
    get_platform_backup,
    list_backups,
    list_platform_backups,
)
from app.core.deps import require_tenant_access
from app.core.writeop import MIN_REASON_LEN, Plan, run_write
from app.cyberark import CyberArkClient
from app.db import get_session
from app.domain.accounts import (
    account_restore_changes,
    build_account_create_body,
    plan_account_create,
)
from app.models import Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}/account-backups", tags=["backups"])
platform_router = APIRouter(prefix="/tenants/{tenant_id}/platform-backups", tags=["backups"])

_tenant_admin = require_tenant_access(OperatorRole.tenant_admin)
_approver = require_tenant_access(OperatorRole.approver)


def _to_out(b: AccountBackup) -> dict:
    return {
        "id": b.id,
        "account_name": b.account_name,
        "safe_name": b.safe_name,
        "user_name": b.user_name,
        "address": b.address,
        "platform_id": b.platform_id,
        "deleted_by": b.deleted_by,
        "deleted_at": b.created_at.isoformat(),
    }


@router.get("")
async def account_backups_list(
    tenant_id: str,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await list_backups(session, tenant_id)
    return {"backups": [_to_out(b) for b in rows]}


class RevealIn(BaseModel):
    reason: str | None = None


@router.post("/{backup_id}/reveal")
async def account_backup_reveal(
    tenant_id: str,
    backup_id: int,
    body: RevealIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)",
        )
    backup = await get_backup(session, tenant_id, backup_id)
    if backup is None:
        raise HTTPException(status_code=404, detail=f"backup '{backup_id}' no existe")

    password = await decrypt_secret(backup.secret_ciphertext)

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.backup.reveal",
        result="ok",
        tenant_id=tenant_id,
        target=str(backup_id),
        source_ip=request.client.host if request.client else None,
        detail={"backup_id": backup_id, "reason": reason},
    )
    await session.commit()
    return {"password": password}


class RestoreIn(BaseModel):
    dry_run: bool = True
    reason: str | None = None
    confirm: str | None = None


@router.post("/{backup_id}/restore")
async def account_backup_restore(
    tenant_id: str,
    backup_id: int,
    body: RestoreIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    backup = await get_backup(session, tenant_id, backup_id)
    if backup is None:
        raise HTTPException(status_code=404, detail=f"backup '{backup_id}' no existe")

    if not body.dry_run and (body.confirm or "") != backup.account_name:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="account.backup.restore",
            result="denied",
            tenant_id=tenant_id,
            target=backup.account_name,
            dry_run=False,
            source_ip=request.client.host if request.client else None,
            detail={"reason": "confirm_mismatch", "backup_id": backup_id},
        )
        await session.commit()
        raise HTTPException(status_code=422, detail="la confirmación no coincide")

    secret = await decrypt_secret(backup.secret_ciphertext)
    changes = account_restore_changes(backup_id, backup.metadata_json, secret)

    async def fetch(client: CyberArkClient) -> dict:
        # mismas validaciones que account.create: la safe y la plataforma
        # deben existir hoy (pueden haber cambiado desde que se borró).
        safe = await client.get_safe(changes["safe_name"])
        details = await client.get_platform_details(changes["platform_id"])
        return {"safe": safe, "platform_details": details}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.create_account(build_account_create_body(changes))

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.create",
        target=f"{changes['safe_name']}/{changes['user_name']}",
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_account_create,
        apply=apply,
    )


def _platform_backup_to_out(b: PlatformBackup) -> dict:
    return {
        "id": b.id,
        "platform_id": b.platform_id,
        "numeric_id": b.numeric_id,
        "size_bytes": b.size_bytes,
        "deleted_by": b.deleted_by,
        "deleted_at": b.created_at.isoformat(),
    }


@platform_router.get("")
async def platform_backups_list(
    tenant_id: str,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await list_platform_backups(session, tenant_id)
    return {"backups": [_platform_backup_to_out(b) for b in rows]}


@platform_router.get("/{backup_id}/download")
async def platform_backup_download(
    tenant_id: str,
    backup_id: int,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Sin `reason`/auditoría de reveal: a diferencia de un backup de
    cuenta, el paquete no tiene un secreto adentro — es política/config del
    CPM. Igual se audita la descarga (`platform.backup.download`)."""
    backup = await get_platform_backup(session, tenant_id, backup_id)
    if backup is None:
        raise HTTPException(status_code=404, detail=f"backup '{backup_id}' no existe")

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="platform.backup.download",
        result="ok",
        tenant_id=tenant_id,
        target=backup.platform_id,
        source_ip=request.client.host if request.client else None,
        detail={"backup_id": backup_id},
    )
    await session.commit()
    return Response(
        content=backup.package,
        media_type="application/octet-stream",
        headers={
            "content-disposition": f'attachment; filename="{backup.platform_id}.zip"',
        },
    )
