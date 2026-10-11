"""Operaciones de escritura de Fase 2 y Fase 3.

Fase 2: toda escritura pasa por `app.core.writeop.run_write`: gating por
feature-flag del tenant, RBAC `operator+` efectivo, `dry-run` + diff,
`reason` obligatoria, rate-limit, y auditoría con before/after. Ver
`docs/specs/done/fase-2-escritura.md`.

Fase 3 (`safe.member.update` / `safe.member.remove` / `safe.update` /
`account.delete` / `safe.delete` / `platform.psm.update`): la misma
maquinaria, más RBAC `approver+` y confirmación reforzada
(`Plan.confirm_phrase` + `confirm` en el body). Ver
`docs/specs/done/safe-member-edit.md`, `docs/specs/done/safe-update.md`,
`docs/specs/account-safe-delete.md` (`account.delete` guarda un backup
cifrado antes de borrar — ver `app.core.backups` y `app.api.backups` para
listar/revelar/restaurar) y `docs/specs/platform-psm-migration.md`
(`platform.psm.update`: backup + verificación + rollback automático ante
un bug conocido de CyberArk).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import audit, cache
from app.core.backups import BackupCryptoError, save_account_backup, save_platform_backup
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.core.writeop import MIN_REASON_LEN, Plan, run_write
from app.cyberark import Conflict, CyberArkClient, CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.domain.accounts import (
    account_link_body,
    build_account_bulk_plan,
    build_account_create_body,
    build_account_delete_plan,
    build_account_move_plan,
    build_account_plan,
    parse_bulk_csv,
    plan_account_create,
    plan_account_link,
    plan_cpm,
    plan_cpm_bulk,
    plan_password_set_next,
    plan_password_vault_only,
)
from app.domain.groups import (
    build_group_create_body,
    build_group_delete_plan,
    build_group_member_add_plan,
    build_group_member_remove_plan,
    build_group_update_body,
    build_group_update_plan,
    find_group_member,
    group_member_body,
    plan_group_create,
)
from app.domain.identity_roles import (
    build_identity_role_member_add_plan,
    plan_identity_role_create,
    select_new_members,
)
from app.domain.identity_users import plan_identity_user_lock
from app.domain.platforms import (
    build_platform_delete_plan,
    build_platform_duplicate_plan,
    find_target_platform,
    normalize_psm,
    plan_platform_activation,
    plan_platform_import,
    plan_platform_psm,
)
from app.domain.safe_permissions import PRESET_KEYS
from app.domain.safes import (
    build_safe_create_body,
    build_safe_delete_plan,
    build_safe_member_plan,
    build_safe_member_remove_plan,
    build_safe_member_update_plan,
    build_safe_update_plan,
    find_member,
    plan_safe_create,
    safe_member_body,
    safe_member_update_body,
    safe_update_body,
)
from app.domain.sessions import plan_session_terminate
from app.domain.users import (
    build_user_create_body,
    build_user_delete_plan,
    build_user_update_body,
    build_user_update_plan,
    plan_user_activate,
    plan_user_create,
)
from app.models import Operator, OperatorRole, Tenant

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["writes"])

_operator = require_tenant_access(OperatorRole.operator)
_approver = require_tenant_access(OperatorRole.approver)  # Fase 3: alto riesgo
_tenant_admin = require_tenant_access(OperatorRole.tenant_admin)  # account.reveal


class AccountUpdateIn(BaseModel):
    changes: dict = Field(default_factory=dict)
    dry_run: bool = True
    reason: str | None = None


class AccountBulkUpdateIn(BaseModel):
    account_ids: list[str] = Field(min_length=1)
    changes: dict = Field(default_factory=dict)
    dry_run: bool = True
    reason: str | None = None


class AccountBulkCpmIn(BaseModel):
    action: Literal["change", "verify", "reconcile"]
    account_ids: list[str] = Field(min_length=1)
    dry_run: bool = True
    reason: str | None = None


class WriteActionIn(BaseModel):
    dry_run: bool = True
    reason: str | None = None


class AccountRevealIn(BaseModel):
    reason: str | None = None


class AccountRevealVersionIn(BaseModel):
    reason: str | None = None
    version: int


class ManualPasswordChanges(BaseModel):
    new_password: str = Field(min_length=1)


class ManualPasswordIn(BaseModel):
    changes: ManualPasswordChanges
    dry_run: bool = True
    reason: str | None = None


class PlatformPsmChanges(BaseModel):
    psm_server_id: str = Field(min_length=1)
    psm_server_name: str | None = None


class PlatformPsmIn(BaseModel):
    """Fase 3: `confirm` debe ser exactamente el `platform_id` de la URL —
    ver `Plan.confirm_phrase`."""

    changes: PlatformPsmChanges
    dry_run: bool = True
    reason: str | None = None
    confirm: str | None = None


class PlatformBulkPsmPreviewIn(BaseModel):
    platform_ids: list[str] = Field(min_length=1)
    psm_server_id: str = Field(min_length=1)
    psm_server_name: str | None = None


class PlatformBulkPsmIn(PlatformBulkPsmPreviewIn):
    reason: str
    # sin Plan.confirm_phrase (no aplica a N objetos) -- confirmación manual:
    # debe repetir el psm_server_id destino exacto.
    confirm: str | None = None


class SafeMemberChanges(BaseModel):
    member_name: str = Field(min_length=1)
    member_type: Literal["User", "Group", "Role"] = "User"
    search_in: str = "Vault"
    preset: str = "read_only"
    permissions: dict[str, bool] | None = None
    membership_expiration_date: int | None = None

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        if v not in PRESET_KEYS:
            raise ValueError(f"preset inválido; use uno de {', '.join(PRESET_KEYS)}")
        return v


class SafeMemberAddIn(BaseModel):
    changes: SafeMemberChanges
    dry_run: bool = True
    reason: str | None = None


class SafeMemberUpdateChanges(BaseModel):
    """Fase 3: editar permisos/vencimiento de un miembro ya existente — sin
    `member_name`/`member_type`/`search_in` (el miembro lo identifica la URL;
    el tipo/fuente no se pueden cambiar, hay que sacarlo y agregarlo de
    nuevo)."""

    preset: str = "custom"
    permissions: dict[str, bool] | None = None
    membership_expiration_date: int | None = None

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        if v not in PRESET_KEYS:
            raise ValueError(f"preset inválido; use uno de {', '.join(PRESET_KEYS)}")
        return v


class SafeMemberUpdateIn(BaseModel):
    """Fase 3: `confirm` debe ser exactamente el `memberName` de la URL —
    ver `Plan.confirm_phrase`."""

    changes: SafeMemberUpdateChanges = Field(default_factory=SafeMemberUpdateChanges)
    dry_run: bool = True
    reason: str | None = None
    confirm: str | None = None


class ConfirmedActionIn(WriteActionIn):
    """Fase 3: como `WriteActionIn`, más `confirm` (confirmación reforzada,
    ver `Plan.confirm_phrase`). Usado por `safe.member.remove`."""

    confirm: str | None = None


class AccountMoveChanges(BaseModel):
    safe_name: str = Field(min_length=1)


class AccountMoveIn(ConfirmedActionIn):
    changes: AccountMoveChanges


class AccountBulkMovePreviewIn(BaseModel):
    account_ids: list[str] = Field(min_length=1)
    safe_name: str = Field(min_length=1)


class AccountBulkMoveIn(AccountBulkMovePreviewIn):
    reason: str
    # sin Plan.confirm_phrase (no aplica a N objetos) -- confirmación manual:
    # debe repetir el nombre exacto de la Safe destino.
    confirm: str | None = None


class AccountBulkDeletePreviewIn(BaseModel):
    account_ids: list[str] = Field(min_length=1)


class AccountBulkDeleteIn(AccountBulkDeletePreviewIn):
    reason: str
    # sin Plan.confirm_phrase (no aplica a N objetos) -- confirmación manual:
    # debe ser exactamente "ELIMINAR <N>", N = cantidad de cuentas del lote.
    confirm: str | None = None


class AccountLinkChanges(BaseModel):
    link_type: Literal["logon", "reconcile"] = "logon"
    linked_safe: str = Field(min_length=1)
    linked_name: str = Field(min_length=1)
    linked_folder: str = "Root"


class AccountLinkIn(BaseModel):
    changes: AccountLinkChanges
    dry_run: bool = True
    reason: str | None = None


class AccountCreateChanges(BaseModel):
    safe_name: str = Field(min_length=1)
    platform_id: str = Field(min_length=1)
    user_name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    name: str | None = None
    secret: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    automatic_management_enabled: bool = True
    manual_management_reason: str | None = None
    remote_machines: str | None = None
    access_restricted_to_remote_machines: bool = False


class AccountCreateIn(BaseModel):
    changes: AccountCreateChanges
    dry_run: bool = True
    reason: str | None = None


class SafeCreateChanges(BaseModel):
    safe_name: str = Field(min_length=1)
    description: str | None = None
    managing_cpm: str | None = None
    retention_type: Literal["days", "versions"] | None = None
    retention_value: int | None = Field(default=None, gt=0)
    olac_enabled: bool = False


class SafeCreateIn(BaseModel):
    changes: SafeCreateChanges
    dry_run: bool = True
    reason: str | None = None


class SafeUpdateChanges(BaseModel):
    """Fase 3: sólo lo que el roadmap pide — descripción, CPM, retención,
    OLAC. No incluye `safe_name` (no se puede renombrar por acá)."""

    description: str | None = None
    managing_cpm: str | None = None
    retention_type: Literal["days", "versions"] | None = None
    retention_value: int | None = Field(default=None, gt=0)
    olac_enabled: bool | None = None


class SafeUpdateIn(BaseModel):
    """Fase 3: `confirm` debe ser exactamente el `safeName` — ver
    `Plan.confirm_phrase`."""

    changes: SafeUpdateChanges = Field(default_factory=SafeUpdateChanges)
    dry_run: bool = True
    reason: str | None = None
    confirm: str | None = None


@router.post("/accounts/{account_id}/update")
async def account_update(
    tenant_id: str,
    account_id: str,
    body: AccountUpdateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_account(account_id)

    async def apply(client: CyberArkClient, plan: Plan) -> dict:
        return await client.update_account(account_id, plan.patch)

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.update",
        target=account_id,
        changes=body.changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_account_plan,
        apply=apply,
    )


@router.post("/accounts/bulk-update")
async def account_bulk_update(
    tenant_id: str,
    body: AccountBulkUpdateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Editar metadata (`name`/`address`/`userName`) de VARIAS cuentas a la
    vez con el mismo valor — equivalente al tilde-múltiple + Editar de la
    vista clásica del PVWA. Ej.: 5 cuentas de firewall que hay que
    apuntar a la misma `address` nueva. Sólo sirve si el valor nuevo es
    igual para todas; direcciones distintas por cuenta siguen siendo
    `account.update` una por una. Misma feature `account.update` y RBAC
    `operator+` que la edición individual — el circuit breaker de
    `writeop.run_write` (`PAM_WRITE_OBJECT_CAP`) limita cuántas cuentas
    entran en un solo pedido."""
    account_ids = list(dict.fromkeys(body.account_ids))  # dedup, preserva orden
    fetched: dict[str, dict] = {}

    async def fetch(client: CyberArkClient) -> dict:
        for aid in account_ids:
            fetched[aid] = await client.get_account(aid)
        return fetched

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        updated: list[str] = []
        for aid in account_ids:
            sub = build_account_plan(fetched[aid], body.changes)
            if sub.patch:
                await client.update_account(aid, sub.patch)
                updated.append(aid)
        return {"updated": updated}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.update",
        target=",".join(account_ids),
        changes=body.changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_account_bulk_plan,
        apply=apply,
    )


@router.post("/accounts/bulk-cpm")
async def account_bulk_cpm(
    tenant_id: str,
    body: AccountBulkCpmIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cambiar/verificar/reconciliar VARIAS cuentas a la vez con el CPM —
    equivalente al tilde-múltiple + Change/Verify/Reconcile de la vista
    clásica del PVWA. Mismo RBAC `operator+` y misma audit action
    (`account.change`/`.verify`/`.reconcile`) que la acción sobre una cuenta,
    con `target` = los ids separados por coma (mismo criterio que
    `bulk-update`). El circuit breaker (`PAM_WRITE_OBJECT_CAP`) limita
    cuántas cuentas entran por pedido. Cada pedido al CPM es independiente:
    uno que falla no frena al resto — los que fallaron vuelven en `failed`
    (y quedan en el audit, ver `writeop.run_write`); si fallan TODOS, el
    lote se trata como error."""
    account_ids = list(dict.fromkeys(body.account_ids))  # dedup, preserva orden
    action = body.action

    async def fetch(client: CyberArkClient) -> dict:
        accounts = await asyncio.gather(*(client.get_account(aid) for aid in account_ids))
        return dict(zip(account_ids, accounts, strict=True))

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        trigger = getattr(client, f"{action}_account")
        results = await asyncio.gather(
            *(trigger(aid) for aid in account_ids), return_exceptions=True
        )
        triggered: list[str] = []
        failed: list[dict] = []
        for aid, res in zip(account_ids, results, strict=True):
            if isinstance(res, CyberArkError):
                failed.append({"account_id": aid, "error": str(res)})
            elif isinstance(res, BaseException):
                raise res
            else:
                triggered.append(aid)
        if not triggered:
            raise CyberArkError(
                "el CPM rechazó el pedido para todas las cuentas: "
                + "; ".join(f"{f['account_id']}: {f['error']}" for f in failed)
            )
        return {"triggered": triggered, "failed": failed}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature=f"account.{action}",
        target=",".join(account_ids),
        changes={"action": action},
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_cpm_bulk(action),
        apply=apply,
    )


async def _cpm(
    *,
    action: str,
    feature: str,
    tenant_id: str,
    account_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator,
    session: AsyncSession,
) -> dict:
    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_account(account_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await getattr(client, f"{action}_account")(account_id)

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature=feature,
        target=account_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_cpm(action),
        apply=apply,
    )


@router.post("/safes/{safe_id}/members")
async def safe_member_add(
    tenant_id: str,
    safe_id: str,
    body: SafeMemberAddIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    changes = body.changes.model_dump()
    member_body = safe_member_body(changes)

    async def fetch(client: CyberArkClient) -> dict:
        return {"members": await client.safe_members(safe_id)}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.add_safe_member(safe_id, member_body)

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="safe.member.add",
        target=safe_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_safe_member_plan,
        apply=apply,
    )


async def _fetch_member(client: CyberArkClient, safe_id: str, member_name: str) -> dict:
    """Fase 3: trae los miembros y resuelve el puntual — `NotFound` acá
    (dentro del try/except de `run_write` que rodea al `fetch`) mapea a 404
    si alguien lo sacó desde el PVWA mientras tanto."""
    members = await client.safe_members(safe_id)
    member = find_member(members, member_name)
    if member is None:
        raise NotFound(f"'{member_name}' no es miembro de la safe '{safe_id}'")
    return {"member": member}


@router.put("/safes/{safe_id}/members/{member_name}")
async def safe_member_update(
    tenant_id: str,
    safe_id: str,
    member_name: str,
    body: SafeMemberUpdateIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: editar permisos/vencimiento de un miembro existente. RBAC
    `approver+`, confirmación reforzada (`confirm` = el `memberName`). Ver
    `docs/specs/safe-member-edit.md`."""
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        return await _fetch_member(client, safe_id, member_name)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.update_safe_member(
            safe_id, member_name, safe_member_update_body(member_name, changes)
        )

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="safe.member.update",
        target=f"{safe_id}/{member_name}",
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_safe_member_update_plan,
        apply=apply,
    )


@router.delete("/safes/{safe_id}/members/{member_name}")
async def safe_member_remove(
    tenant_id: str,
    safe_id: str,
    member_name: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: sacar a un miembro de la safe. RBAC `approver+`, confirmación
    reforzada. Ver `docs/specs/safe-member-edit.md`."""

    async def fetch(client: CyberArkClient) -> dict:
        return await _fetch_member(client, safe_id, member_name)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.remove_safe_member(safe_id, member_name)
        return {}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="safe.member.remove",
        target=f"{safe_id}/{member_name}",
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_safe_member_remove_plan,
        apply=apply,
    )


@router.post("/accounts/{account_id}/link")
async def account_link(
    tenant_id: str,
    account_id: str,
    body: AccountLinkIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    changes = body.changes.model_dump()
    link_body = account_link_body(changes)

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_account(account_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.link_account(account_id, link_body)

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.link",
        target=account_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_account_link,
        apply=apply,
    )


@router.post("/accounts")
async def account_create(
    tenant_id: str,
    body: AccountCreateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Alta de cuenta — wizard estilo PVWA (tipo de sistema -> plataforma ->
    safe -> campos). Ver `docs/specs/plantillas-alta-cuenta-safe.md`."""
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        if (
            changes["automatic_management_enabled"] is False
            and not (changes.get("manual_management_reason") or "").strip()
        ):
            raise HTTPException(
                status_code=422,
                detail="'manual_management_reason' es obligatoria al deshabilitar la "
                "gestión automática",
            )
        safe = await client.get_safe(changes["safe_name"])
        # sólo para confirmar que la plataforma existe (404 si no) — "Get
        # Platform Details" no expone el esquema de campos del Add Account,
        # ver la nota en app/domain/accounts.py.
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


async def _bulk_safe_platform_lookup(
    client: CyberArkClient, rows: list[dict[str, Any]]
) -> tuple[dict[str, dict], dict[str, str], dict[str, dict], dict[str, str]]:
    """Resuelve, en paralelo y deduplicado, todas las safes/plataformas que
    aparecen en las filas del CSV (un CSV real repite mucho la misma
    safe/plataforma) — comparten cliente y semáforo de concurrencia con el
    resto de PAM Web Console. Devuelve `(safe_ok, safe_err, platform_ok, platform_err)`,
    cada uno indexado por nombre/id."""
    unique_safes = {r["safe_name"] for r in rows}
    unique_platforms = {r["platform_id"] for r in rows}

    async def _safe(name: str) -> tuple[str, dict | None, str | None]:
        try:
            return name, await client.get_safe(name), None
        except (NotFound, Forbidden, CyberArkError) as e:
            return name, None, str(e)

    async def _platform(pid: str) -> tuple[str, dict | None, str | None]:
        try:
            return pid, await client.get_platform_details(pid), None
        except (NotFound, Forbidden, CyberArkError) as e:
            return pid, None, str(e)

    safe_results = await asyncio.gather(*[_safe(n) for n in unique_safes])
    platform_results = await asyncio.gather(*[_platform(p) for p in unique_platforms])
    safe_ok = {n: o for n, o, err in safe_results if o is not None}
    safe_err = {n: err for n, o, err in safe_results if err is not None and err}
    platform_ok = {p: o for p, o, err in platform_results if o is not None}
    platform_err = {p: err for p, o, err in platform_results if err is not None and err}
    return safe_ok, safe_err, platform_ok, platform_err


async def _read_bulk_csv(file: UploadFile) -> str:
    return (await file.read()).decode("utf-8-sig", errors="replace")


def _bulk_row_error(changes: dict[str, Any], safe_err: dict, platform_err: dict) -> str | None:
    if changes["safe_name"] in safe_err:
        return f"safe: {safe_err[changes['safe_name']]}"
    if changes["platform_id"] in platform_err:
        return f"plataforma: {platform_err[changes['platform_id']]}"
    if (
        changes.get("automatic_management_enabled") is False
        and not (changes.get("manual_management_reason") or "").strip()
    ):
        return "'manual_management_reason' es obligatoria al deshabilitar la gestión automática"
    return None


@router.post("/accounts/bulk-create/preview")
async def account_bulk_create_preview(
    tenant_id: str,
    file: UploadFile = File(...),
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Previsualiza un onboarding masivo por CSV: parsea + valida cada fila
    contra CyberArk (safe/plataforma existen) sin crear nada. Ver
    `docs/specs/done/account-bulk-onboard.md`."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    rows_ok, rows_error = parse_bulk_csv(await _read_bulk_csv(file))
    total = len(rows_ok) + len(rows_error)
    cap = get_settings().bulk_create_max_rows
    if total > cap:
        raise HTTPException(
            status_code=409,
            detail=f"el CSV tiene {total} filas (máx {cap}); dividilo en tandas más chicas",
        )

    results: list[dict[str, Any]] = [
        {"row": e["_row"], "ok": False, "error": e["error"]} for e in rows_error
    ]

    async with tenant_client(tenant_id, session) as (_, client):
        safe_ok, safe_err, platform_ok, platform_err = await _bulk_safe_platform_lookup(
            client, rows_ok
        )
        for changes in rows_ok:
            row = changes["_row"]
            err = _bulk_row_error(changes, safe_err, platform_err)
            if err:
                results.append({"row": row, "ok": False, "error": err})
                continue
            current = {
                "safe": safe_ok[changes["safe_name"]],
                "platform_details": platform_ok[changes["platform_id"]],
            }
            plan = plan_account_create(current, changes)
            results.append(
                {
                    "row": row,
                    "ok": True,
                    "diff": plan.diff,
                    "warnings": plan.warnings,
                    "target": f"{changes['safe_name']}/{changes['user_name']}",
                }
            )

    results.sort(key=lambda r: r["row"])
    valid = sum(1 for r in results if r["ok"])
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.create.bulk.preview",
        result="ok",
        tenant_id=tenant_id,
        detail={
            "filename": file.filename,
            "total": total,
            "valid": valid,
            "invalid": total - valid,
        },
    )
    await session.commit()
    return {"total": total, "valid": valid, "invalid": total - valid, "rows": results}


@router.post("/accounts/bulk-create")
async def account_bulk_create(
    tenant_id: str,
    file: UploadFile = File(...),
    reason: str = Form(...),
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ejecuta un onboarding masivo por CSV — crea las filas válidas una
    por una, sin abortar si alguna falla (mismo criterio que
    `account.reveal.bulk`: no hay corrida parcial que revertir, cada fila
    es independiente). Re-valida contra CyberArk (no confía en una preview
    vieja). Auditado una sola vez por lote, no por cuenta. Ver
    `docs/specs/done/account-bulk-onboard.md`."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")
    if len((reason or "").strip()) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422, detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)"
        )

    rows_ok, rows_error = parse_bulk_csv(await _read_bulk_csv(file))
    total = len(rows_ok) + len(rows_error)
    cap = get_settings().bulk_create_max_rows
    if total > cap:
        raise HTTPException(status_code=409, detail=f"el CSV tiene {total} filas (máx {cap})")

    failed: list[dict[str, Any]] = [{"row": e["_row"], "error": e["error"]} for e in rows_error]
    created: list[dict[str, Any]] = []

    async with tenant_client(tenant_id, session) as (_, client):
        _safe_ok, safe_err, _platform_ok, platform_err = await _bulk_safe_platform_lookup(
            client, rows_ok
        )

        async def _create(changes: dict[str, Any]) -> None:
            row = changes["_row"]
            err = _bulk_row_error(changes, safe_err, platform_err)
            if err:
                failed.append({"row": row, "error": err})
                return
            try:
                await client.create_account(build_account_create_body(changes))
                created.append(
                    {"row": row, "target": f"{changes['safe_name']}/{changes['user_name']}"}
                )
            except (NotFound, Forbidden, Conflict, CyberArkError) as e:
                failed.append({"row": row, "error": str(e)})

        await asyncio.gather(*[_create(c) for c in rows_ok])

    failed.sort(key=lambda r: r["row"])
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.create.bulk",
        result="ok",
        tenant_id=tenant_id,
        detail={
            "filename": file.filename,
            "reason": reason,
            "total": total,
            "created": len(created),
            "failed": len(failed),
            "failed_rows": failed,
        },
    )
    await session.commit()
    if created:
        await cache.delete(f"pam:safes:{tenant_id}")
    return {"total": total, "created": len(created), "failed": failed}


@router.post("/safes")
async def safe_create(
    tenant_id: str,
    body: SafeCreateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Alta de safe, con defaults de CPM/retención sugeridos por el frontend
    (`GET /safes/defaults`). Ver `docs/specs/plantillas-alta-cuenta-safe.md`."""
    changes = body.changes.model_dump()

    async def fetch(_client: CyberArkClient) -> dict:
        return {}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.create_safe(build_safe_create_body(changes))

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="safe.create",
        target=changes["safe_name"],
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_safe_create,
        apply=apply,
    )
    if not result.get("dry_run"):
        # invalida el caché de /safe-names y /safes/defaults (ver
        # app.api.inventory.safes_rows_cached) para que la safe nueva
        # aparezca sin esperar el TTL corto.
        await cache.delete(f"pam:safes:{tenant_id}")
    return result


@router.put("/safes/{safe_id}")
async def safe_update(
    tenant_id: str,
    safe_id: str,
    body: SafeUpdateIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: editar descripción/CPM/retención/OLAC de una safe existente.
    RBAC `approver+`, confirmación reforzada (`confirm` = el `safeName`).
    Ver `docs/specs/safe-update.md`."""
    changes = body.changes.model_dump(exclude_unset=True)
    fetched: dict[str, dict] = {}

    async def fetch(client: CyberArkClient) -> dict:
        fetched["safe"] = await client.get_safe(safe_id)
        return fetched["safe"]

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.update_safe(safe_id, safe_update_body(fetched["safe"], changes))

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="safe.update",
        target=safe_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_safe_update_plan,
        apply=apply,
    )
    if not result.get("dry_run"):
        # el CPM/retención cambiados pueden afectar /cpm-names y
        # /safes/defaults — mismo criterio que safe_create.
        await cache.delete(f"pam:safes:{tenant_id}")
    return result


@router.post("/accounts/{account_id}/reveal")
async def account_reveal(
    tenant_id: str,
    account_id: str,
    body: AccountRevealIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Revela la contraseña VIGENTE de una cuenta activa (no borrada).
    Gateado por la feature `account.reveal` (opt-in por tenant, apagada por
    defecto) + RBAC `tenant_admin+` — el nivel más alto de todo PAM Web Console, porque
    expone un secreto real en uso (no uno histórico como el de un backup).
    No pasa por `writeop.run_write`: no hay `dry-run` posible (revelar ES la
    acción), pero sí exige `reason` y audita `account.reveal` sin el valor
    de la contraseña. Ver `docs/specs/account-reveal.md`."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)",
        )

    async with tenant_client(tenant_id, session) as (_, client):
        try:
            password = await client.retrieve_password(account_id, reason)
        except (NotFound, Forbidden) as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="account.reveal",
                result="error",
                tenant_id=tenant_id,
                target=account_id,
                source_ip=request.client.host if request.client else None,
                detail={"error": str(e)},
            )
            await session.commit()
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.reveal",
        result="ok",
        tenant_id=tenant_id,
        target=account_id,
        source_ip=request.client.host if request.client else None,
        detail={"reason": reason},
    )
    await session.commit()
    return {"password": password}


@router.post("/accounts/{account_id}/reveal-version")
async def account_reveal_version(
    tenant_id: str,
    account_id: str,
    body: AccountRevealVersionIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Revela el valor de UNA versión HISTÓRICA de la contraseña (pestaña
    "Versiones" del detalle de cuenta, calcada de la del PVWA). Mismo
    endpoint real que `account.reveal` (`client.retrieve_password`, con
    `version` opcional), gateado por una feature separada
    (`account.reveal.history`, no `account.reveal`) porque el radio de
    exposición es distinto: acá potencialmente varias contraseñas
    históricas de la misma cuenta, no sólo la vigente — mismo criterio
    que separar `account.reveal.bulk` de `account.reveal`. Ver
    docs/specs/done/account-password-versions.md."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)",
        )

    async with tenant_client(tenant_id, session) as (_, client):
        try:
            password = await client.retrieve_password(account_id, reason, version=body.version)
        except (NotFound, Forbidden) as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="account.reveal.history",
                result="error",
                tenant_id=tenant_id,
                target=account_id,
                source_ip=request.client.host if request.client else None,
                detail={"error": str(e), "version": body.version},
            )
            await session.commit()
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.reveal.history",
        result="ok",
        tenant_id=tenant_id,
        target=account_id,
        source_ip=request.client.host if request.client else None,
        detail={"reason": reason, "version": body.version},
    )
    await session.commit()
    return {"password": password}


@router.post("/accounts/{account_id}/change-in-vault")
async def account_change_in_vault(
    tenant_id: str,
    account_id: str,
    body: ManualPasswordIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """ "Change credentials in the Vault" del PVWA: el operador especifica un
    valor que actualiza SOLO lo que la Vault tiene guardado — nunca llama al
    target. Usar cuando la contraseña real ya se cambió por fuera de
    CyberArk. RBAC `tenant_admin+` (igual que `account.reveal`: el operador
    maneja un valor de credencial real). El valor nunca aparece en el
    diff/audit. Ver `docs/specs/account-password-manual.md`."""
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_account(account_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.update_password_in_vault(account_id, changes["new_password"])
        return {"updated": True}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.change.vault_only",
        target=account_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_password_vault_only,
        apply=apply,
    )


@router.post("/accounts/{account_id}/set-next-password")
async def account_set_next_password(
    tenant_id: str,
    account_id: str,
    body: ManualPasswordIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """ "Set next password" del PVWA: el operador especifica el valor que el
    CPM va a usar en su PRÓXIMO cambio automático (`ChangeImmediately:
    false`) — no toca nada ahora. Mismo RBAC `tenant_admin+` que
    `change-in-vault`. Ver `docs/specs/account-password-manual.md`."""
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_account(account_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.set_next_password(account_id, changes["new_password"])
        return {"updated": True}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.change.next",
        target=account_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_password_set_next,
        apply=apply,
    )


async def _delete_account_with_backup(
    client: CyberArkClient,
    session: AsyncSession,
    *,
    tenant_id: str,
    op: Operator,
    account: dict,
    reason: str,
) -> dict[str, Any]:
    """Recupera la contraseña vigente, guarda el `AccountBackup` cifrado y
    recién entonces borra la cuenta — el único camino de "backup antes que
    borrado", compartido por `account_delete` y `account_bulk_delete`. Si el
    backup falla, levanta antes de tocar CyberArk (la cuenta NO se borra).

    Llamar una sola vez por cuenta, nunca concurrente sobre la misma
    `session` (`save_account_backup` hace `session.commit()`)."""
    account_id = str(account.get("id") or "")
    secret = await client.retrieve_password(account_id, reason)
    await save_account_backup(
        session,
        tenant_id=tenant_id,
        account=account,
        secret=secret,
        deleted_by=op.username,
        reason=reason,
    )
    await client.delete_account(account_id)
    return {"deleted": True, "id": account_id}


@router.delete("/accounts/{account_id}")
async def account_delete(
    tenant_id: str,
    account_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: borrar una cuenta. Antes de borrarla en CyberArk se recupera
    la contraseña vigente y se guarda un `AccountBackup` cifrado — si el
    borrado falla después, el backup igual queda (nunca se borra sin
    backup). RBAC `approver+`, confirmación reforzada (`confirm` = el
    `name` real de la cuenta). Ver `docs/specs/account-safe-delete.md`."""
    fetched: dict[str, dict] = {}
    reason = (body.reason or "").strip()

    async def fetch(client: CyberArkClient) -> dict:
        fetched["account"] = await client.get_account(account_id)
        return fetched["account"]

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await _delete_account_with_backup(
            client,
            session,
            tenant_id=tenant_id,
            op=op,
            account=fetched["account"],
            reason=reason,
        )

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.delete",
        target=account_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_account_delete_plan,
        apply=apply,
    )


@router.delete("/safes/{safe_id}")
async def safe_delete(
    tenant_id: str,
    safe_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: borrar una safe VACÍA. Si tiene cuentas, 422 antes de tocar
    CyberArk (hay que borrarlas primero, cada una queda respaldada
    individualmente por `account.delete`). RBAC `approver+`, confirmación
    reforzada (`confirm` = el `safeName`). Ver
    `docs/specs/account-safe-delete.md`."""

    async def fetch(client: CyberArkClient) -> dict:
        safe = await client.get_safe(safe_id)
        _rows, total = await client.accounts_page(
            safe_name=str(safe.get("safeName") or safe_id), limit=1, offset=0
        )
        if total > 0:
            raise HTTPException(
                status_code=422,
                detail=f"la safe tiene {total} cuenta(s); bórrelas antes de borrar la safe",
            )
        return {"safe": safe}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.delete_safe(safe_id)
        return {}

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="safe.delete",
        target=safe_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_safe_delete_plan,
        apply=apply,
    )
    if not result.get("dry_run"):
        await cache.delete(f"pam:safes:{tenant_id}")
    return result


@router.post("/users/{user_id}/activate")
async def user_activate(
    tenant_id: str,
    user_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_user(user_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.activate_user(user_id)

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="user.activate",
        target=user_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_user_activate,
        apply=apply,
    )


async def _identity_user_set_lock(
    tenant_id: str,
    user_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator,
    session: AsyncSession,
    *,
    target_lock: bool,
) -> dict:
    """Bloquear/desbloquear un usuario de **Identity/SSO**
    (`UserMgmt/SetCloudLock`) — distinto de `user.activate` (Vault). Una
    sola feature cubre ambas direcciones, mismo criterio que
    `platform.activate`/`deactivate`. RBAC `operator+`, sin confirmación
    reforzada (reversible, mismo criterio que
    `user.activate`/`session.terminate`). Ver `docs/specs/identity-users.md`."""

    async def fetch(client: CyberArkClient) -> dict:
        info, locked = await asyncio.gather(
            client.identity_get_user_info(user_id),
            client.identity_is_user_locked(user_id),
        )
        username = info.get("Username") or info.get("UserName") or info.get("SystemName") or user_id
        return {"locked": locked, "username": username}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.identity_set_cloud_lock(user_id, target_lock)
        return {"locked": target_lock}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="identity_user.lock",
        target=user_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_identity_user_lock(target_lock),
        apply=apply,
    )


@router.post("/identity-users/{user_id}/lock")
async def identity_user_lock(
    tenant_id: str,
    user_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _identity_user_set_lock(
        tenant_id, user_id, body, request, op, session, target_lock=True
    )


@router.post("/identity-users/{user_id}/unlock")
async def identity_user_unlock(
    tenant_id: str,
    user_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _identity_user_set_lock(
        tenant_id, user_id, body, request, op, session, target_lock=False
    )


class IdentityRoleCreateChanges(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)


class IdentityRoleCreateIn(BaseModel):
    changes: IdentityRoleCreateChanges
    dry_run: bool = True
    reason: str | None = None


@router.post("/identity-roles")
async def identity_role_create(
    tenant_id: str,
    body: IdentityRoleCreateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Alta de un rol ESTÁTICO de Identity, vacío (`Roles/StoreRole`:
    nombre + descripción, como el diálogo "Add Role" de Identity
    Administration). RBAC `operator+`: aditivo y reversible, no concede
    nada por sí solo (sin miembros ni derechos administrativos). Rechaza un
    nombre ya usado (sin distinguir mayúsculas). Ver
    `docs/specs/done/identity-role-create.md`."""
    name = body.changes.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="el nombre del rol no puede estar vacío")
    changes = {"name": name, "description": (body.changes.description or "").strip() or None}

    async def fetch(client: CyberArkClient) -> dict:
        roles = await client.redrock_query("SELECT Name FROM Role")
        exists = any(str(r.get("Name") or "").strip().lower() == name.lower() for r in roles)
        return {"exists": exists}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.create_identity_role(name, changes["description"])

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="identity_role.create",
        target=name,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_identity_role_create,
        apply=apply,
    )


class IdentityRoleMemberAddIn(BaseModel):
    changes: dict = Field(default_factory=dict)  # {"members": [{id, name, type}]}
    dry_run: bool = True
    reason: str | None = None


@router.post("/identity-roles/{role_id}/members")
async def identity_role_member_add(
    tenant_id: str,
    role_id: str,
    body: IdentityRoleMemberAddIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Agrega usuarios/roles de Identity como miembros de un rol
    (`Roles/UpdateRole`, sólo `Add`: nunca quita a nadie). RBAC `operator+`,
    mismo criterio que `safe.member.add` (aditivo y reversible); el plan avisa
    si el rol parece de administración. Ver
    `docs/specs/identity-role-members.md`."""

    fetched: dict = {}

    async def fetch(client: CyberArkClient) -> dict:
        roles, members = await asyncio.gather(
            client.redrock_query(
                "SELECT ID, Name FROM Role "  # noqa: S608
                f"WHERE ID = '{_safe_identity_id(role_id)}'"
            ),
            client.identity_role_members(role_id),
        )
        if not roles:
            raise NotFound(f"el rol de Identity '{role_id}' no existe")
        # `apply` reusa lo que ya trajo el fetch (sin volver a pedir miembros)
        fetched.update(role_name=roles[0].get("Name") or role_id, members=members)
        return fetched

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        new, _ = select_new_members(fetched, body.changes)
        users = [m["id"] for m in new if m["type"] == "User"]
        roles = [m["id"] for m in new if m["type"] == "Role"]
        await client.identity_role_add_members(role_id, users=users, roles=roles)
        return {"added_users": len(users), "added_roles": len(roles)}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="identity_role.member.add",
        target=role_id,
        changes=body.changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_identity_role_member_add_plan,
        apply=apply,
    )


def _safe_identity_id(value: str) -> str:
    """Un ID de Identity (UUID o nombre interno como `sysadmin`) saneado para
    entrar al SQL de Redrock, que no tiene binds."""
    return "".join(c for c in value if c.isalnum() or c in "-_")


class UserCreateChanges(BaseModel):
    username: str = Field(min_length=1)
    initial_password: str = Field(min_length=1)
    first_name: str | None = None
    last_name: str | None = None
    change_pass_on_next_logon: bool = True


class UserCreateIn(BaseModel):
    changes: UserCreateChanges
    dry_run: bool = True
    reason: str | None = None


@router.post("/users")
async def user_create(
    tenant_id: str,
    body: UserCreateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Alta de usuario del Vault. Riesgo bajo — aditivo, se revierte
    borrando el usuario — RBAC `operator+`, sin confirmación reforzada.
    El `initial_password` nunca viaja en el diff/audit. Ver
    `docs/specs/vault-users-groups-crud.md`."""
    changes = body.changes.model_dump()

    async def fetch(_client: CyberArkClient) -> dict:
        return {}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.create_user(build_user_create_body(changes))

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="user.create",
        target=changes["username"],
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_user_create,
        apply=apply,
    )


class UserUpdateChanges(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    change_pass_on_next_logon: bool | None = None


class UserUpdateIn(BaseModel):
    changes: UserUpdateChanges = Field(default_factory=UserUpdateChanges)
    dry_run: bool = True
    reason: str | None = None


@router.put("/users/{user_id}")
async def user_update(
    tenant_id: str,
    user_id: str,
    body: UserUpdateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Editar nombre/apellido/"cambiar contraseña en el próximo login" de un
    usuario existente. RBAC `operator+`, sin confirmación reforzada. Ver
    `docs/specs/vault-users-groups-crud.md`."""
    changes = body.changes.model_dump(exclude_unset=True)
    fetched: dict[str, dict] = {}

    async def fetch(client: CyberArkClient) -> dict:
        fetched["user"] = await client.get_user(user_id)
        return fetched["user"]

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.update_user(user_id, build_user_update_body(fetched["user"], changes))

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="user.update",
        target=user_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_user_update_plan,
        apply=apply,
    )


@router.delete("/users/{user_id}")
async def user_delete(
    tenant_id: str,
    user_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: borrar un usuario del Vault. RBAC `approver+` + confirmación
    reforzada (`confirm` = el `username` real). Sin backup (ver
    `docs/domain/users.py::build_user_delete_plan`). Ver
    `docs/specs/vault-users-groups-crud.md`."""

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_user(user_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.delete_user(user_id)
        return {}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="user.delete",
        target=user_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_user_delete_plan,
        apply=apply,
    )


@router.post("/accounts/{account_id}/change")
async def account_change(
    tenant_id: str,
    account_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _cpm(
        action="change",
        feature="account.change",
        tenant_id=tenant_id,
        account_id=account_id,
        body=body,
        request=request,
        op=op,
        session=session,
    )


@router.post("/accounts/{account_id}/verify")
async def account_verify(
    tenant_id: str,
    account_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _cpm(
        action="verify",
        feature="account.verify",
        tenant_id=tenant_id,
        account_id=account_id,
        body=body,
        request=request,
        op=op,
        session=session,
    )


@router.post("/accounts/{account_id}/reconcile")
async def account_reconcile(
    tenant_id: str,
    account_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _cpm(
        action="reconcile",
        feature="account.change",  # Reconcile va con la misma feature que Change
        tenant_id=tenant_id,
        account_id=account_id,
        body=body,
        request=request,
        op=op,
        session=session,
    )


async def _apply_platform_psm_update(
    client: CyberArkClient,
    session: AsyncSession,
    *,
    tenant_id: str,
    op: Operator,
    platform_id: str,
    numeric_id: str,
    original: dict,
    new_psm_server_id: str,
    new_psm_server_name: str | None,
    source_ip: str | None,
) -> dict:
    """Corazón de `platform.psm.update`: GET del objeto de gestión de
    sesiones ya resuelto por el caller -> muta sólo PSMServerId/
    PSMServerName -> PUT -> releer y verificar que nada más haya cambiado
    -> si algo más cambió (bug conocido de CyberArk con PSMConnectors/
    Connection Components), revertir automáticamente al original.
    Compartido por el endpoint de una plataforma (`platform_psm_update`) y
    el de varias (`platform_bulk_psm_update`) — mismo criterio que
    `_move_account`. Levanta `CyberArkError` si algo salió mal (con el
    rollback ya intentado y, de fallar, auditado)."""
    original_norm = normalize_psm(original)

    modified = dict(original)
    modified["PSMServerId"] = new_psm_server_id
    modified["PSMServerName"] = new_psm_server_name or new_psm_server_id
    await client.update_platform_psm(numeric_id, modified)

    verify = await client.get_platform_psm(numeric_id)
    if normalize_psm(verify) != original_norm:
        try:
            await client.update_platform_psm(numeric_id, original)
            rollback_ok = True
        except CyberArkError:
            rollback_ok = False
        if not rollback_ok:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="platform.psm.rollback_failed",
                result="error",
                tenant_id=tenant_id,
                target=platform_id,
                source_ip=source_ip,
                detail={"original_psm_config": original},
            )
            await session.commit()
            raise CyberArkError(
                "se detectaron cambios inesperados tras el cambio de PSM y el "
                "rollback automático FALLÓ — restaurar a mano; el objeto original "
                "quedó guardado en el audit (action=platform.psm.rollback_failed)"
            )
        raise CyberArkError(
            "CyberArk modificó algo más que el PSM al guardar (bug conocido de "
            "PSMConnectors/Connection Components) — se revirtió automáticamente al "
            "estado original. La plataforma NO quedó migrada."
        )

    if verify.get("PSMServerId") != new_psm_server_id:
        raise CyberArkError("el cambio de PSM no se aplicó según la verificación posterior")
    return verify


@router.put("/platforms/{platform_id}/psm")
async def platform_psm_update(
    tenant_id: str,
    platform_id: str,
    body: PlatformPsmIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reasigna el PSM de una plataforma — `Platform_PSM_Migration.ps1`
    llevado a PAM Web Console, mismo patrón de seguridad: GET del objeto de gestión de
    sesiones COMPLETO -> mutar sólo PSMServerId/PSMServerName -> PUT ->
    releer y verificar que nada más haya cambiado -> si algo más cambió
    (bug conocido de CyberArk con PSMConnectors/Connection Components),
    revertir automáticamente al original. RBAC `approver+` + confirmación
    reforzada (`confirm` = `platform_id`) — afecta a TODAS las cuentas de
    esa plataforma. Ver `docs/specs/platform-psm-migration.md`. Para mover
    VARIAS plataformas al mismo PSM de una, ver `platform_bulk_psm_update`
    más abajo (`docs/specs/done/platform-psm-bulk-migration.md`)."""
    fetched: dict[str, Any] = {}

    async def fetch(client: CyberArkClient) -> dict:
        targets = await client.list_target_platforms()
        match = find_target_platform(targets, platform_id)
        if match is None:
            raise NotFound(f"plataforma '{platform_id}' no existe")
        fetched["numeric_id"] = match["ID"]
        fetched["psm"] = await client.get_platform_psm(fetched["numeric_id"])
        return fetched["psm"]

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await _apply_platform_psm_update(
            client,
            session,
            tenant_id=tenant_id,
            op=op,
            platform_id=platform_id,
            numeric_id=fetched["numeric_id"],
            original=fetched["psm"],
            new_psm_server_id=body.changes.psm_server_id,
            new_psm_server_name=body.changes.psm_server_name,
            source_ip=request.client.host if request.client else None,
        )

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="platform.psm.update",
        target=platform_id,
        changes=body.changes.model_dump(),
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=plan_platform_psm(platform_id),
        apply=apply,
    )
    if not result.get("dry_run"):
        # el PSM cambiado se pinta en la grilla de /platforms (psmServerId,
        # ver platforms_rows_cached) — invalidar para que no espere al TTL.
        await cache.delete(f"pam:platforms:{tenant_id}")
    return result


@router.post("/platforms/bulk-psm-update/preview")
async def platform_bulk_psm_update_preview(
    tenant_id: str,
    body: PlatformBulkPsmPreviewIn,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Previsualiza reasignar el PSM de VARIAS plataformas al MISMO PSM
    destino de una — pensado para una migración real (ej. parche
    programado en uno de los dos servidores PSM de un tenant: mover todo
    lo que apunta ahí al otro mientras dura). Un diff por plataforma
    (mismo `plan_platform_psm` que la reasignación individual), sin tocar
    CyberArk todavía. Ver `platform_bulk_psm_update` para la ejecución y
    `docs/specs/done/platform-psm-bulk-migration.md`."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    platform_ids = list(dict.fromkeys(body.platform_ids))  # dedup, preserva orden
    cap = get_settings().write_object_cap
    if len(platform_ids) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"se eligieron {len(platform_ids)} plataformas (máx {cap} por lote)",
        )

    results: list[dict[str, Any]] = []
    async with tenant_client(tenant_id, session) as (_, client):
        targets = await client.list_target_platforms()

        async def _preview(pid: str) -> None:
            match = find_target_platform(targets, pid)
            if match is None:
                results.append({"platform_id": pid, "ok": False, "error": "plataforma no existe"})
                return
            try:
                psm = await client.get_platform_psm(match["ID"])
            except (NotFound, Forbidden) as e:
                results.append({"platform_id": pid, "ok": False, "error": str(e)})
                return
            if psm.get("PSMServerId") == body.psm_server_id:
                results.append({"platform_id": pid, "ok": False, "error": "ya está en ese PSM"})
                return
            plan = plan_platform_psm(pid)(psm, {"psm_server_id": body.psm_server_id})
            results.append(
                {
                    "platform_id": pid,
                    "ok": True,
                    "diff": plan.diff,
                    "warnings": plan.warnings,
                }
            )

        # sólo lecturas acá (get_platform_psm) -- a diferencia de
        # platform_bulk_psm_update, no hay escritura en `session` dentro de
        # cada tarea, así que correrlas en paralelo es seguro.
        await asyncio.gather(*[_preview(pid) for pid in platform_ids])

    results.sort(key=lambda r: platform_ids.index(r["platform_id"]))
    valid = sum(1 for r in results if r["ok"])
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="platform.psm.update.bulk.preview",
        result="ok",
        tenant_id=tenant_id,
        detail={"psm_server_id": body.psm_server_id, "total": len(platform_ids), "valid": valid},
    )
    await session.commit()
    return {
        "total": len(platform_ids),
        "valid": valid,
        "invalid": len(platform_ids) - valid,
        "rows": results,
    }


@router.post("/platforms/bulk-psm-update")
async def platform_bulk_psm_update(
    tenant_id: str,
    body: PlatformBulkPsmIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ejecuta la reasignación de PSM de VARIAS plataformas al MISMO PSM
    destino. Cada plataforma es independiente
    (`_apply_platform_psm_update`: GET completo -> mutar -> PUT -> verificar
    -> rollback automático si algo más cambió) — una que falla no aborta el
    lote, mismo criterio que `account.move.bulk`. Se procesan una por una,
    **secuencial** (no `asyncio.gather`): cada plataforma puede necesitar
    auditar+commitear un `platform.psm.rollback_failed` sobre la misma
    `session`, y correr eso en paralelo no es seguro sobre un
    `AsyncSession`.

    No hay `Plan.confirm_phrase` (no aplica a N objetos): la confirmación
    reforzada es manual, `confirm` debe repetir el `psm_server_id` destino
    exacto. Mismo RBAC `approver+` que la reasignación de una sola
    plataforma — cada una ya lleva su propia verificación y rollback, el
    lote no suma riesgo estructural nuevo (a diferencia de `account.move`,
    que sí sube a `platform_admin` en su versión bulk porque cada cuenta
    pierde historial nativo de forma irreversible)."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422, detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)"
        )
    if (body.confirm or "") != body.psm_server_id:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="platform.psm.update.bulk",
            result="denied",
            tenant_id=tenant_id,
            detail={"reason": "confirm_mismatch"},
        )
        await session.commit()
        raise HTTPException(
            status_code=422,
            detail="la confirmación no coincide — debe ser el ID exacto del PSM destino",
        )

    platform_ids = list(dict.fromkeys(body.platform_ids))
    cap = get_settings().write_object_cap
    if len(platform_ids) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"se eligieron {len(platform_ids)} plataformas (máx {cap} por lote)",
        )

    updated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    source_ip = request.client.host if request.client else None

    async with tenant_client(tenant_id, session) as (_, client):
        targets = await client.list_target_platforms()
        for pid in platform_ids:
            match = find_target_platform(targets, pid)
            if match is None:
                failed.append({"platform_id": pid, "error": "plataforma no existe"})
                continue
            try:
                psm = await client.get_platform_psm(match["ID"])
            except (NotFound, Forbidden) as e:
                failed.append({"platform_id": pid, "error": str(e)})
                continue
            if psm.get("PSMServerId") == body.psm_server_id:
                failed.append({"platform_id": pid, "error": "ya está en ese PSM"})
                continue
            try:
                await _apply_platform_psm_update(
                    client,
                    session,
                    tenant_id=tenant_id,
                    op=op,
                    platform_id=pid,
                    numeric_id=match["ID"],
                    original=psm,
                    new_psm_server_id=body.psm_server_id,
                    new_psm_server_name=body.psm_server_name,
                    source_ip=source_ip,
                )
            except CyberArkError as e:
                failed.append({"platform_id": pid, "error": str(e)})
                continue
            updated.append({"platform_id": pid})

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="platform.psm.update.bulk",
        result="ok",
        tenant_id=tenant_id,
        detail={
            "psm_server_id": body.psm_server_id,
            "reason": reason,
            "total": len(platform_ids),
            "updated": len(updated),
            "failed": len(failed),
            "failed_rows": failed,
        },
    )
    await session.commit()

    if updated:
        # el PSM cambiado se pinta en la grilla de /platforms (psmServerId,
        # ver platforms_rows_cached) — invalidar para que no espere al TTL.
        await cache.delete(f"pam:platforms:{tenant_id}")

    return {"total": len(platform_ids), "updated": updated, "failed": failed}


async def _fetch_target_platform(client: CyberArkClient, platform_id: str) -> dict:
    targets = await client.list_target_platforms()
    match = find_target_platform(targets, platform_id)
    if match is None:
        raise NotFound(f"plataforma '{platform_id}' no existe")
    return match


async def _platform_activation(
    tenant_id: str,
    platform_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator,
    session: AsyncSession,
    *,
    target_active: bool,
    feature: str,
    client_method: str,
) -> dict:
    """Fase 3: activar/desactivar una plataforma. `docs/specs/
    platform-lifecycle.md`. Afecta a TODAS las cuentas de la plataforma
    (el CPM deja/retoma de gestionarlas) — RBAC `approver+` + confirmación
    reforzada, aunque sea reversible."""
    numeric_id: dict[str, Any] = {}

    async def fetch(client: CyberArkClient) -> dict:
        match = await _fetch_target_platform(client, platform_id)
        numeric_id["value"] = match["ID"]
        return match

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await getattr(client, client_method)(numeric_id["value"])
        return {}

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature=feature,
        target=platform_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=plan_platform_activation(platform_id, target_active),
        apply=apply,
    )
    if not result.get("dry_run"):
        await cache.delete(f"pam:platforms:{tenant_id}")
    return result


@router.post("/platforms/{platform_id}/activate")
async def platform_activate(
    tenant_id: str,
    platform_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _platform_activation(
        tenant_id,
        platform_id,
        body,
        request,
        op,
        session,
        target_active=True,
        feature="platform.activate",
        client_method="activate_target_platform",
    )


@router.post("/platforms/{platform_id}/deactivate")
async def platform_deactivate(
    tenant_id: str,
    platform_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _platform_activation(
        tenant_id,
        platform_id,
        body,
        request,
        op,
        session,
        target_active=False,
        feature="platform.activate",
        client_method="deactivate_target_platform",
    )


_MAX_PLATFORM_IMPORT_BYTES = 20 * 1024 * 1024  # 20 MB — un paquete de plataforma es chico


@router.post("/platforms/import")
async def platform_import(
    tenant_id: str,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Form(True),
    reason: str | None = Form(None),
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Crea una plataforma NUEVA a partir de un paquete `.zip` (formato
    "Export Platform"). Riesgo bajo — sólo crea un objeto nuevo, no toca
    ninguna plataforma existente — RBAC `operator+`, sin confirmación
    reforzada, mismo criterio que `platform.duplicate`. Sin dry-run real:
    CyberArk no expone el contenido de un `.zip` sin importarlo, así que
    el "preview" es sólo nombre/tamaño del archivo. Ver
    `docs/specs/platform-import.md` (ambigüedad sin resolver sobre el
    formato del archivo en el body — base64 vs array de bytes)."""
    filename = file.filename or "archivo.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="el archivo debe ser un .zip")

    content = await file.read()
    if len(content) > _MAX_PLATFORM_IMPORT_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"el archivo supera el máximo permitido ({_MAX_PLATFORM_IMPORT_BYTES // (1024 * 1024)} MB)",
        )
    changes = {"filename": filename, "size_bytes": len(content)}

    async def fetch(_client: CyberArkClient) -> dict:
        return {}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.import_platform(content)

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="platform.import",
        target=filename,
        changes=changes,
        dry_run=dry_run,
        reason=reason,
        fetch=fetch,
        build_plan=plan_platform_import,
        apply=apply,
    )
    if not result.get("dry_run"):
        await cache.delete(f"pam:platforms:{tenant_id}")
    return result


class PlatformDuplicateChanges(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None


class PlatformDuplicateIn(BaseModel):
    changes: PlatformDuplicateChanges
    dry_run: bool = True
    reason: str | None = None


@router.post("/platforms/{platform_id}/duplicate")
async def platform_duplicate(
    tenant_id: str,
    platform_id: str,
    body: PlatformDuplicateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3 (riesgo bajo): crea una copia de la plataforma. No toca la
    original ni ninguna cuenta -> RBAC `operator+`, sin confirmación
    reforzada, mismo criterio que `account.create`/`safe.create`. Ver
    `docs/specs/platform-lifecycle.md`."""
    changes = body.changes.model_dump()
    numeric_id: dict[str, Any] = {}

    async def fetch(client: CyberArkClient) -> dict:
        match = await _fetch_target_platform(client, platform_id)
        if changes["name"] == match.get("PlatformID"):
            raise HTTPException(
                status_code=422, detail="el nombre de la copia debe ser distinto del origen"
            )
        numeric_id["value"] = match["ID"]
        return match

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.duplicate_target_platform(
            numeric_id["value"],
            {"name": changes["name"], "description": changes.get("description") or ""},
        )

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="platform.duplicate",
        target=platform_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_platform_duplicate_plan,
        apply=apply,
    )
    if not result.get("dry_run"):
        await cache.delete(f"pam:platforms:{tenant_id}")
    return result


@router.delete("/platforms/{platform_id}")
async def platform_delete(
    tenant_id: str,
    platform_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3 (riesgo alto): borra la plataforma. Bloqueado si alguna
    cuenta la usa (escanea el inventario completo del tenant — la API Gen2
    no filtra cuentas por `platformId` server-side). Guarda un backup del
    paquete de "Export Platform" ANTES de borrar. RBAC `approver+` +
    confirmación reforzada. Ver `docs/specs/platform-lifecycle.md`."""
    numeric_id: dict[str, Any] = {}

    async def fetch(client: CyberArkClient) -> dict:
        match = await _fetch_target_platform(client, platform_id)
        numeric_id["value"] = match["ID"]
        accounts = await client.list_accounts()
        in_use = sum(
            1 for a in accounts if isinstance(a, dict) and a.get("platformId") == platform_id
        )
        if in_use:
            raise HTTPException(
                status_code=422,
                detail=f"la plataforma tiene {in_use} cuenta(s); cámbielas de plataforma o "
                "bórrelas antes",
            )
        return {"platform": match}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        package = await client.export_platform(platform_id)
        await save_platform_backup(
            session,
            tenant_id=tenant_id,
            platform_id=platform_id,
            numeric_id=str(numeric_id["value"]),
            package=package,
            deleted_by=op.username,
        )
        await client.delete_target_platform(numeric_id["value"])
        return {}

    result = await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="platform.delete",
        target=platform_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_platform_delete_plan,
        apply=apply,
    )
    if not result.get("dry_run"):
        await cache.delete(f"pam:platforms:{tenant_id}")
    return result


class GroupCreateChanges(BaseModel):
    group_name: str = Field(min_length=1)
    description: str | None = None
    location: str | None = None


class GroupCreateIn(BaseModel):
    changes: GroupCreateChanges
    dry_run: bool = True
    reason: str | None = None


@router.post("/groups")
async def group_create(
    tenant_id: str,
    body: GroupCreateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Alta de grupo del Vault. Riesgo bajo — aditivo, RBAC `operator+`,
    sin confirmación reforzada. Ver
    `docs/specs/vault-users-groups-crud.md`."""
    changes = body.changes.model_dump()

    async def fetch(_client: CyberArkClient) -> dict:
        return {}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.create_group(build_group_create_body(changes))

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="group.create",
        target=changes["group_name"],
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_group_create,
        apply=apply,
    )


class GroupUpdateChanges(BaseModel):
    group_name: str = Field(min_length=1)


class GroupUpdateIn(BaseModel):
    changes: GroupUpdateChanges
    dry_run: bool = True
    reason: str | None = None


@router.put("/groups/{group_id}")
async def group_update(
    tenant_id: str,
    group_id: str,
    body: GroupUpdateIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Renombrar un grupo — es el único campo que "Update Group" acepta.
    RBAC `operator+`, sin confirmación reforzada. Ver
    `docs/specs/vault-users-groups-crud.md`."""
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_group(group_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.update_group(group_id, build_group_update_body(group_id, changes))

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="group.update",
        target=group_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_group_update_plan,
        apply=apply,
    )


@router.delete("/groups/{group_id}")
async def group_delete(
    tenant_id: str,
    group_id: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: borrar un grupo VACÍO. Si tiene miembros, 422 antes de tocar
    CyberArk. RBAC `approver+` + confirmación reforzada (`confirm` = el
    `groupName`). Ver `docs/specs/vault-users-groups-crud.md`."""

    async def fetch(client: CyberArkClient) -> dict:
        group = await client.get_group(group_id)
        members = group.get("members") or []
        if members:
            raise HTTPException(
                status_code=422,
                detail=f"el grupo tiene {len(members)} miembro(s); quítelos antes de borrarlo",
            )
        return {"group": group}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.delete_group(group_id)
        return {}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="group.delete",
        target=group_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_group_delete_plan,
        apply=apply,
    )


class GroupMemberChanges(BaseModel):
    member_id: str = Field(min_length=1)
    member_type: Literal["vault", "domain"] = "vault"
    domain_name: str | None = None


class GroupMemberAddIn(BaseModel):
    changes: GroupMemberChanges
    dry_run: bool = True
    reason: str | None = None


@router.post("/groups/{group_id}/members")
async def group_member_add(
    tenant_id: str,
    group_id: str,
    body: GroupMemberAddIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Agregar un miembro a un grupo. Riesgo bajo — RBAC `operator+`, sin
    confirmación reforzada, mismo criterio que `safe.member.add`. Ver
    `docs/specs/vault-users-groups-crud.md`."""
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        return await client.get_group(group_id)

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.add_group_member(group_id, group_member_body(changes))

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="group.member.add",
        target=group_id,
        changes=changes,
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=build_group_member_add_plan,
        apply=apply,
    )


@router.delete("/groups/{group_id}/members/{member_name}")
async def group_member_remove(
    tenant_id: str,
    group_id: str,
    member_name: str,
    body: ConfirmedActionIn,
    request: Request,
    op: Operator = Depends(_approver),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: quitar un miembro de un grupo. RBAC `approver+` +
    confirmación reforzada (`confirm` = el nombre del miembro), mismo
    criterio que `safe.member.remove`. Ver
    `docs/specs/vault-users-groups-crud.md`."""

    async def fetch(client: CyberArkClient) -> dict:
        members = await client.user_group_members(group_id)
        member = find_group_member(members, member_name)
        if member is None:
            raise NotFound(f"'{member_name}' no es miembro del grupo '{group_id}'")
        return {"member": member}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        await client.remove_group_member(group_id, member_name)
        return {}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="group.member.remove",
        target=f"{group_id}/{member_name}",
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_group_member_remove_plan,
        apply=apply,
    )


@router.post("/sessions/{session_id}/terminate")
async def session_terminate(
    tenant_id: str,
    session_id: str,
    body: WriteActionIn,
    request: Request,
    op: Operator = Depends(_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Terminar una sesión PSM activa. Riesgo bajo — no borra nada
    persistente (el usuario se puede reconectar), RBAC `operator+`, sin
    confirmación reforzada, mismo criterio que `account.verify`/
    `user.activate`. Ver `docs/specs/session-terminate.md`.

    `session_id` (de la URL) es el `SessionID` que ya trae la grilla —
    sirve para el `fetch` (`GET /API/LiveSessions/{id}` lo acepta), pero
    `Terminate` devuelve 400 `CAWS00001E` con ese formato: necesita el
    `SessionGuid` (confirmado contra Globex, 2026-09-24 — el parámetro
    real de `Stop-PASPSMSession` en `psPAS` es `LiveSessionId` con alias
    `SessionGuid`). El `GET /API/LiveSessions/{id}` puntual (el que usa
    `fetch`) no lo trae en la respuesta (segundo intento real contra
    Globex, mismo día: con el fallback al objeto de `fetch` seguía
    yendo el `SessionID` corto) — se resuelve en cambio contra
    `client.live_sessions()` (el listado, que sí lo trae confirmado),
    buscando la fila con el mismo `SessionID`."""
    fetched: dict = {}

    async def fetch(client: CyberArkClient) -> dict:
        data = await client.get_live_session(session_id)
        fetched["session"] = data
        return data

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        guid = str(fetched["session"].get("SessionGuid") or "")
        if not guid:
            for row in await client.live_sessions():
                if str(row.get("SessionID")) == session_id:
                    guid = str(row.get("SessionGuid") or "")
                    break
        await client.terminate_session(guid or session_id)
        return {}

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="session.terminate",
        target=session_id,
        changes={},
        dry_run=body.dry_run,
        reason=body.reason,
        fetch=fetch,
        build_plan=plan_session_terminate,
        apply=apply,
    )


async def _move_account(
    client: CyberArkClient,
    session: AsyncSession,
    *,
    tenant_id: str,
    op: Operator,
    account: dict,
    dest_safe: str,
    reason: str,
    source_ip: str | None,
) -> dict[str, Any]:
    """Retrieve + backup + create en la safe destino + delete de la
    original, con rollback si el delete falla — el corazón de
    `account.move`, compartido por el endpoint de una cuenta
    (`account_move`) y el de varias (`account_bulk_move`). `account` ya
    viene resuelto (`client.get_account`) por el caller. Devuelve
    `{"moved", "old_id", "new_id"}` o levanta `CyberArkError` (auditando
    `account.move.rollback_failed` antes, si el rollback también falla).

    Llamar esto una sola vez por cuenta, nunca concurrente sobre la misma
    `session` (cada llamada hace sus propios `session.commit()` — dos en
    paralelo corromperían el estado de un `AsyncSession`, que no es
    seguro para uso concurrente). `account_bulk_move` por eso mueve las
    cuentas del lote una por una, no con `asyncio.gather`."""
    account_id = str(account.get("id") or "")

    secret = await client.retrieve_password(account_id, reason)

    # Backup de la cuenta ORIGINAL antes de tocar nada -- mismo orden
    # seguro que account.delete: nunca se borra sin backup.
    await save_account_backup(
        session,
        tenant_id=tenant_id,
        account=account,
        secret=secret,
        deleted_by=op.username,
        reason=f"movida a la safe '{dest_safe}' — {reason}",
    )

    props = account.get("platformAccountProperties") or {}
    sm = account.get("secretManagement") or {}
    rma = account.get("remoteMachinesAccess") or {}

    create_body: dict[str, Any] = {
        "name": account.get("name"),
        "address": account.get("address"),
        "userName": account.get("userName"),
        "platformId": account.get("platformId"),
        "safeName": dest_safe,
        "secret": secret,
        "secretType": "password",
        "platformAccountProperties": props,
        "secretManagement": {
            "automaticManagementEnabled": sm.get("automaticManagementEnabled", True),
            "manualManagementReason": sm.get("manualManagementReason", ""),
        },
    }
    if rma.get("remoteMachines"):
        create_body["remoteMachinesAccess"] = {
            "remoteMachines": rma["remoteMachines"],
            "accessRestrictedToRemoteMachines": rma.get("accessRestrictedToRemoteMachines", False),
        }

    new_account = await client.create_account(create_body)
    new_id = str(new_account.get("id") or "")

    try:
        await client.delete_account(account_id)
    except CyberArkError:
        rollback_ok = True
        if new_id:
            try:
                await client.delete_account(new_id)
            except CyberArkError:
                rollback_ok = False
        if not rollback_ok:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action="account.move.rollback_failed",
                result="error",
                tenant_id=tenant_id,
                target=account_id,
                source_ip=source_ip,
                detail={"new_account_id": new_id, "dest_safe": dest_safe},
            )
            await session.commit()
            raise CyberArkError(
                "no se pudo borrar la cuenta original Y el rollback de la copia nueva "
                f"(id {new_id}) TAMBIÉN falló — quedaron dos copias vivas de la misma "
                "credencial, hay que borrar una a mano. El estado quedó guardado en el "
                "audit (action=account.move.rollback_failed)."
            )
        raise CyberArkError(
            "no se pudo borrar la cuenta original en la safe de origen — se revirtió "
            "el alta en la safe destino automáticamente. La cuenta NO se movió; el "
            "backup de la cuenta original ya quedó guardado de todos modos."
        )

    return {"moved": True, "old_id": account_id, "new_id": new_id}


@router.post("/accounts/{account_id}/move")
async def account_move(
    tenant_id: str,
    account_id: str,
    body: AccountMoveIn,
    request: Request,
    op: Operator = Depends(require_tenant_access(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fase 3: mueve una cuenta a otra Safe. CyberArk no expone un "Move"
    real vía API con Bearer token (confirmado tanto para el PVWA on-prem
    clásico como para Privilege Cloud: llaman al mismo servicio legacy
    `AccountsMgt.asmx/Move`, autenticado con la sesión de navegador del
    operador — ver `docs/specs/done/account-change-platform.md`) —
    workaround: retrieve + create en la safe destino + delete de la
    original (`_move_account`, compartido con `account_bulk_move`). Se
    pierde el historial nativo de CyberArk (versiones de contraseña,
    actividad) y los vínculos Logon/Reconcile, que hay que recrear a mano
    — el propio diálogo "Move Account" de Privilege Cloud advierte lo
    mismo, no es una limitación exclusiva de este workaround. Por eso
    `platform_admin`, el techo más alto de RBAC en PAM Web Console, más que el
    `approver+` del resto de Fase 3.

    Antes de borrar la cuenta original se guarda un `AccountBackup` cifrado
    (mismo criterio que `account.delete`: nunca se borra una cuenta sin
    backup). Si el alta en el destino falla, no se tocó nada. Si el alta
    funciona pero el borrado de la original falla, se intenta un rollback
    borrando la copia recién creada (para no dejar dos copias vivas de la
    misma credencial); si el rollback también falla, se audita el estado
    completo (`action=account.move.rollback_failed`) antes de cortar con un
    error explícito para resolver a mano — mismo patrón que
    `platform.psm.update`. RBAC `platform_admin` + confirmación reforzada
    (`confirm` = el `name` real de la cuenta, ver `build_account_move_plan`)."""
    fetched: dict[str, dict] = {}
    reason = (body.reason or "").strip()
    dest_safe = body.changes.safe_name

    async def fetch(client: CyberArkClient) -> dict:
        fetched["account"] = await client.get_account(account_id)
        if fetched["account"].get("safeName") == dest_safe:
            raise HTTPException(status_code=422, detail="la cuenta ya está en esa Safe")
        await client.get_safe(dest_safe)  # 404 si el safe destino no existe
        return fetched["account"]

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await _move_account(
            client,
            session,
            tenant_id=tenant_id,
            op=op,
            account=fetched["account"],
            dest_safe=dest_safe,
            reason=reason,
            source_ip=request.client.host if request.client else None,
        )

    return await run_write(
        session=session,
        op=op,
        request=request,
        tenant_id=tenant_id,
        feature="account.move",
        target=account_id,
        changes={"safe_name": dest_safe},
        dry_run=body.dry_run,
        reason=body.reason,
        confirm=body.confirm,
        fetch=fetch,
        build_plan=build_account_move_plan,
        apply=apply,
    )


@router.post("/accounts/bulk-move/preview")
async def account_bulk_move_preview(
    tenant_id: str,
    body: AccountBulkMovePreviewIn,
    op: Operator = Depends(require_tenant_access(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Previsualiza mover VARIAS cuentas a la MISMA Safe destino de una —
    equivalente al tilde-múltiple + "Move" de la vista clásica del PVWA.
    Un diff por cuenta (mismo `build_account_move_plan` que el move
    individual), sin tocar CyberArk todavía. Ver `account_bulk_move` para
    la ejecución y por qué no comparte el mismo request."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    account_ids = list(dict.fromkeys(body.account_ids))  # dedup, preserva orden
    cap = get_settings().write_object_cap
    if len(account_ids) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"se eligieron {len(account_ids)} cuentas (máx {cap} por lote)",
        )

    results: list[dict[str, Any]] = []
    async with tenant_client(tenant_id, session) as (_, client):
        try:
            await client.get_safe(body.safe_name)  # 404 si el safe destino no existe
        except NotFound as e:
            raise HTTPException(status_code=404, detail=str(e))

        async def _preview(aid: str) -> None:
            try:
                account = await client.get_account(aid)
            except (NotFound, Forbidden) as e:
                results.append({"account_id": aid, "ok": False, "error": str(e)})
                return
            if account.get("safeName") == body.safe_name:
                results.append({"account_id": aid, "ok": False, "error": "ya está en esa Safe"})
                return
            plan = build_account_move_plan(account, {"safe_name": body.safe_name})
            results.append(
                {
                    "account_id": aid,
                    "ok": True,
                    "name": account.get("name"),
                    "diff": plan.diff,
                    "warnings": plan.warnings,
                }
            )

        # sólo lecturas acá (get_account/get_safe) -- a diferencia de
        # account_bulk_move, no hay escritura en `session` dentro de cada
        # tarea, así que correrlas en paralelo es seguro.
        await asyncio.gather(*[_preview(aid) for aid in account_ids])

    results.sort(key=lambda r: account_ids.index(r["account_id"]))
    valid = sum(1 for r in results if r["ok"])
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.move.bulk.preview",
        result="ok",
        tenant_id=tenant_id,
        detail={"safe_name": body.safe_name, "total": len(account_ids), "valid": valid},
    )
    await session.commit()
    return {
        "total": len(account_ids),
        "valid": valid,
        "invalid": len(account_ids) - valid,
        "rows": results,
    }


@router.post("/accounts/bulk-move")
async def account_bulk_move(
    tenant_id: str,
    body: AccountBulkMoveIn,
    request: Request,
    op: Operator = Depends(require_tenant_access(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ejecuta el move de VARIAS cuentas a la MISMA Safe destino. Cada
    cuenta es independiente (`_move_account`: retrieve + backup + create +
    delete + rollback) — no aborta el lote si una falla, mismo criterio
    que `account.create.bulk`/`account.reveal.bulk` ("cada fila es
    independiente"). Se mueven **una por una, secuencial** — no con
    `asyncio.gather` como el resto de los endpoints bulk de PAM Web Console, porque
    cada cuenta hace sus propios `session.commit()` (backup, y el audit de
    rollback si hace falta) y correrlos en paralelo sobre el mismo
    `AsyncSession` no es seguro.

    No hay `Plan.confirm_phrase` (no aplica a N objetos): la confirmación
    reforzada es manual, `confirm` debe repetir el nombre exacto de la
    Safe destino. Auditado una vez por lote (`account.move.bulk`) más
    el/los `account.move.rollback_failed` individuales si corresponde.
    RBAC `platform_admin`, igual que el move de una cuenta."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422, detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)"
        )
    if (body.confirm or "") != body.safe_name:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="account.move.bulk",
            result="denied",
            tenant_id=tenant_id,
            detail={"reason": "confirm_mismatch"},
        )
        await session.commit()
        raise HTTPException(
            status_code=422,
            detail="la confirmación no coincide — debe ser el nombre exacto de la Safe destino",
        )

    account_ids = list(dict.fromkeys(body.account_ids))
    cap = get_settings().write_object_cap
    if len(account_ids) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"se eligieron {len(account_ids)} cuentas (máx {cap} por lote)",
        )

    moved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    source_ip = request.client.host if request.client else None

    async with tenant_client(tenant_id, session) as (_, client):
        try:
            await client.get_safe(body.safe_name)  # 404 si el safe destino no existe
        except NotFound as e:
            raise HTTPException(status_code=404, detail=str(e))

        for aid in account_ids:
            try:
                account = await client.get_account(aid)
            except (NotFound, Forbidden) as e:
                failed.append({"account_id": aid, "error": str(e)})
                continue
            if account.get("safeName") == body.safe_name:
                failed.append({"account_id": aid, "error": "ya está en esa Safe"})
                continue
            try:
                result = await _move_account(
                    client,
                    session,
                    tenant_id=tenant_id,
                    op=op,
                    account=account,
                    dest_safe=body.safe_name,
                    reason=reason,
                    source_ip=source_ip,
                )
            except CyberArkError as e:
                failed.append({"account_id": aid, "error": str(e)})
                continue
            moved.append({"account_id": aid, "new_id": result["new_id"]})

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.move.bulk",
        result="ok",
        tenant_id=tenant_id,
        detail={
            "safe_name": body.safe_name,
            "reason": reason,
            "total": len(account_ids),
            "moved": len(moved),
            "failed": len(failed),
            "failed_rows": failed,
        },
    )
    await session.commit()
    return {"total": len(account_ids), "moved": len(moved), "failed": failed}


@router.post("/accounts/bulk-delete/preview")
async def account_bulk_delete_preview(
    tenant_id: str,
    body: AccountBulkDeletePreviewIn,
    op: Operator = Depends(require_tenant_access(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Previsualiza borrar VARIAS cuentas de una: un diff por cuenta (mismo
    `build_account_delete_plan` que el borrado individual), sin tocar
    CyberArk más que para leer. Ver `account_bulk_delete` para la
    ejecución."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    account_ids = list(dict.fromkeys(body.account_ids))  # dedup, preserva orden
    cap = get_settings().write_object_cap
    if len(account_ids) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"se eligieron {len(account_ids)} cuentas (máx {cap} por lote)",
        )

    results: list[dict[str, Any]] = []
    async with tenant_client(tenant_id, session) as (_, client):

        async def _preview(aid: str) -> None:
            try:
                account = await client.get_account(aid)
            except (NotFound, Forbidden) as e:
                results.append({"account_id": aid, "ok": False, "error": str(e)})
                return
            plan = build_account_delete_plan(account, {})
            results.append(
                {
                    "account_id": aid,
                    "ok": True,
                    "name": account.get("name"),
                    "safe_name": account.get("safeName"),
                    "diff": plan.diff,
                }
            )

        # sólo lecturas acá -- a diferencia de account_bulk_delete, no hay
        # escritura en `session` dentro de cada tarea, es seguro en paralelo.
        await asyncio.gather(*[_preview(aid) for aid in account_ids])

    results.sort(key=lambda r: account_ids.index(r["account_id"]))
    valid = sum(1 for r in results if r["ok"])
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.delete.bulk.preview",
        result="ok",
        tenant_id=tenant_id,
        detail={"total": len(account_ids), "valid": valid},
    )
    await session.commit()
    return {
        "total": len(account_ids),
        "valid": valid,
        "invalid": len(account_ids) - valid,
        "rows": results,
    }


@router.post("/accounts/bulk-delete")
async def account_bulk_delete(
    tenant_id: str,
    body: AccountBulkDeleteIn,
    request: Request,
    op: Operator = Depends(require_tenant_access(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Borra VARIAS cuentas de una. Cada cuenta es independiente
    (`_delete_account_with_backup`: retrieve + backup + delete) — una que
    falla no aborta el lote, y **si el backup de una cuenta falla esa cuenta
    NO se borra** (nunca se borra sin backup). Secuencial, no
    `asyncio.gather`: cada backup hace su propio `session.commit()` y una
    `AsyncSession` no admite uso concurrente (mismo motivo que
    `account_bulk_move`).

    Sin `Plan.confirm_phrase` (no aplica a N objetos): la confirmación
    reforzada es manual, `confirm` debe ser exactamente `ELIMINAR <N>` con N
    = cantidad de cuentas del lote. Auditado una vez por lote
    (`account.delete.bulk`). RBAC `platform_admin`, igual que
    `account.move.bulk`. Ver `docs/specs/account-bulk-delete.md`."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422, detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)"
        )

    account_ids = list(dict.fromkeys(body.account_ids))
    expected_confirm = f"ELIMINAR {len(account_ids)}"
    if (body.confirm or "") != expected_confirm:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="account.delete.bulk",
            result="denied",
            tenant_id=tenant_id,
            detail={"reason": "confirm_mismatch", "total": len(account_ids)},
        )
        await session.commit()
        raise HTTPException(
            status_code=422,
            detail=f"la confirmación no coincide — debe ser exactamente '{expected_confirm}'",
        )

    cap = get_settings().write_object_cap
    if len(account_ids) > cap:
        raise HTTPException(
            status_code=409,
            detail=f"se eligieron {len(account_ids)} cuentas (máx {cap} por lote)",
        )

    deleted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    async with tenant_client(tenant_id, session) as (_, client):
        for aid in account_ids:
            try:
                account = await client.get_account(aid)
            except (NotFound, Forbidden) as e:
                failed.append({"account_id": aid, "error": str(e)})
                continue
            try:
                await _delete_account_with_backup(
                    client, session, tenant_id=tenant_id, op=op, account=account, reason=reason
                )
            except BackupCryptoError as e:
                failed.append(
                    {
                        "account_id": aid,
                        "name": account.get("name"),
                        "error": f"no se borró: falló el backup ({e})",
                    }
                )
                continue
            except CyberArkError as e:
                failed.append({"account_id": aid, "name": account.get("name"), "error": str(e)})
                continue
            deleted.append({"account_id": aid, "name": account.get("name")})

    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.delete.bulk",
        result="ok",
        tenant_id=tenant_id,
        detail={
            "reason": reason,
            "total": len(account_ids),
            "deleted": len(deleted),
            "deleted_rows": deleted,
            "failed": len(failed),
            "failed_rows": failed,
        },
    )
    await session.commit()
    return {"total": len(account_ids), "deleted": len(deleted), "failed": failed}
