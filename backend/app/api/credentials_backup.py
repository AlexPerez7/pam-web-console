"""Backup completo de credenciales de un tenant (TODAS las cuentas, con
contraseña) — pedido explícito de un cliente: antes de una limpieza masiva
de cuentas, tener un CSV con todo lo necesario para recrearlas a mano si
hiciera falta. Ver `docs/specs/account-credentials-backup.md`.

Distinto del backup de `account.delete` (automático, por cuenta, al
borrar): esto es un export manual y ocasional de TODO el tenant, más
parecido en espíritu al script `Account_Secret_Backup.ps1` de
PowerShell-Scripts (se reusa su misma lógica: listar todo, recuperar
el secreto cuenta por cuenta, seguir aunque alguna falle).

NO vive en `app.api.reports` a propósito: el catálogo de Reportes es
siempre de sólo lectura, sin secretos, rol `viewer`, cacheado en Redis —
mezclar esto ahí rompería esa garantía. Esta es su propia ruta, con el
mismo nivel de RBAC/gating que `account.reveal`.
"""

from __future__ import annotations

import asyncio
import io
import secrets
from datetime import UTC, datetime

import pyzipper
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.csv_export import Column, dotted, to_csv
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.core.writeop import MIN_REASON_LEN
from app.cyberark import CyberArkClient, CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole, Tenant

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["credentials-backup"])

_tenant_admin = require_tenant_access(OperatorRole.tenant_admin)

_COLUMNS: list[Column] = [
    ("accountId", dotted("id")),
    ("accountName", dotted("name")),
    ("userName", dotted("userName")),
    ("address", dotted("address")),
    ("platformId", dotted("platformId")),
    ("safeName", dotted("safeName")),
    ("remoteMachines", dotted("remoteMachinesAccess.remoteMachines")),
    ("secretType", dotted("secretType")),
    ("secret", dotted("_secret")),
    ("retrieveStatus", dotted("_secret_status")),
    ("retrieveError", dotted("_secret_error")),
]


class CredentialsBackupIn(BaseModel):
    reason: str | None = None


async def _retrieve(client: CyberArkClient, account: dict, reason: str) -> dict:
    """Nunca levanta — una cuenta que falla (sin permiso, sin secreto
    guardado, error transitorio) queda con `_secret_status: ERROR/EMPTY` y
    el resto del backup sigue. Mismo criterio que
    `Account_Secret_Backup.ps1`."""
    account_id = str(account.get("id") or "")
    try:
        secret = await client.retrieve_password(account_id, reason)
    except (NotFound, Forbidden, CyberArkError) as e:
        return {**account, "_secret": "", "_secret_status": "ERROR", "_secret_error": str(e)}
    if not secret:
        return {
            **account,
            "_secret": "",
            "_secret_status": "EMPTY",
            "_secret_error": "sin secreto guardado en el Vault",
        }
    return {**account, "_secret": secret, "_secret_status": "OK", "_secret_error": ""}


def _random_password() -> str:
    return secrets.token_urlsafe(18)


def _build_password_zip_sync(csv_text: str, inner_filename: str, password: str) -> bytes:
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode())
        zf.writestr(inner_filename, csv_text)
    return buf.getvalue()


async def _build_password_zip(csv_text: str, inner_filename: str, password: str) -> bytes:
    return await asyncio.to_thread(_build_password_zip_sync, csv_text, inner_filename, password)


@router.post("/credentials-backup/export")
async def credentials_backup_export(
    tenant_id: str,
    body: CredentialsBackupIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Lista TODAS las cuentas del tenant, recupera el secreto de cada una
    (concurrencia limitada por `PAM_CYBERARK_MAX_CONCURRENCY`, la misma
    que cualquier otra llamada al cliente) y devuelve un ZIP protegido con
    contraseña (AES real vía `pyzipper`, no el ZipCrypto débil del zip
    estándar) — pensado para entregarle directo al cliente, la contraseña
    se genera al azar en cada export y viaja en el header
    `X-Backup-Password` de esta misma respuesta, nunca se guarda en PAM Web Console
    (ni DB ni audit ni logs) — comunicarla por un canal separado del
    archivo. Puede tardar varios minutos en un tenant con miles de
    cuentas; el nginx del server tiene un `proxy_read_timeout` extendido
    para esta ruta.

    Gateado por RBAC `tenant_admin+` — el mismo techo que `account.reveal`,
    pero acá el radio de exposición es TODO el tenant de una vez. Genera un
    evento "Retrieve" por cuenta en el audit nativo de CyberArk. Se audita
    una sola vez en PAM Web Console (`account.reveal.bulk`) con el conteo por estado,
    nunca ningún secreto ni la contraseña del zip."""
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
            accounts = await client.list_accounts()
        except (NotFound, Forbidden) as e:
            await _audit(
                session, op, request, tenant_id, "error", {"error": str(e), "phase": "list"}
            )
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))
        except CyberArkError as e:
            await _audit(
                session, op, request, tenant_id, "error", {"error": str(e), "phase": "list"}
            )
            raise HTTPException(status_code=502, detail=str(e))

        rows = await asyncio.gather(*[_retrieve(client, a, reason) for a in accounts])

    counts = {"ok": 0, "empty": 0, "error": 0}
    for r in rows:
        counts[str(r["_secret_status"]).lower()] += 1

    await _audit(
        session,
        op,
        request,
        tenant_id,
        "ok",
        {"reason": reason, "count": len(rows), **counts},
    )

    csv_text = to_csv(rows, _COLUMNS)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    password = _random_password()
    zip_bytes = await _build_password_zip(
        csv_text, f"pam-{tenant_id}-credentials-backup-{stamp}.csv", password
    )

    filename = f"pam-{tenant_id}-credentials-backup-{stamp}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "x-backup-password": password,
        },
    )


async def _audit(
    session: AsyncSession,
    op: Operator,
    request: Request,
    tenant_id: str,
    result: str,
    detail: dict,
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="account.reveal.bulk",
        result=result,
        tenant_id=tenant_id,
        source_ip=request.client.host if request.client else None,
        detail=detail,
    )
    await session.commit()
