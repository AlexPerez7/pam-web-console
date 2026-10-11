"""
Backups cifrados de cuentas borradas (Fase 3, `account.delete`).

Antes de borrar una cuenta, se guarda una copia completa — metadata y la
contraseña vigente — para poder recrearla si el borrado fue un error. Usa el
mismo binario `age` que `app.core.secrets`, pero con un keypair PROPIO
(`PAM_BACKUPS_AGE_KEY` / `PAM_BACKUPS_AGE_RECIPIENT`), separado del que
descifra las credenciales de tenant: comprometer uno no compromete el otro.
Ver docs/specs/account-safe-delete.md.
"""

from __future__ import annotations

import asyncio
import subprocess

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.account_backup import AccountBackup
from app.models.platform_backup import PlatformBackup


class BackupCryptoError(Exception):
    pass


def _encrypt_sync(plaintext: str) -> bytes:
    s = get_settings()
    try:
        # `-r <recipient>`: sólo necesita la pública, puede correr en
        # cualquier request de escritura (no requiere la privada).
        cmd = ["age", "-r", s.backups_age_recipient]
        out = subprocess.run(  # noqa: S603 — comando estático, args de config
            cmd, input=plaintext.encode(), capture_output=True, check=True, timeout=10
        )
    except FileNotFoundError:
        raise BackupCryptoError("el binario 'age' no está instalado en la imagen")
    except subprocess.CalledProcessError as e:
        raise BackupCryptoError(f"no se pudo cifrar el backup: {e.stderr.decode()[:200]}")
    return out.stdout


def _decrypt_sync(ciphertext: bytes) -> str:
    s = get_settings()
    try:
        cmd = ["age", "--decrypt", "-i", s.backups_age_key]
        out = subprocess.run(  # noqa: S603
            cmd, input=ciphertext, capture_output=True, check=True, timeout=10
        )
    except FileNotFoundError:
        raise BackupCryptoError("el binario 'age' no está instalado en la imagen")
    except subprocess.CalledProcessError as e:
        raise BackupCryptoError(f"no se pudo descifrar el backup: {e.stderr.decode()[:200]}")
    return out.stdout.decode()


async def encrypt_secret(plaintext: str) -> bytes:
    """Nunca loguea `plaintext` — sólo lo pasa por stdin al subprocess."""
    return await asyncio.to_thread(_encrypt_sync, plaintext)


async def decrypt_secret(ciphertext: bytes) -> str:
    return await asyncio.to_thread(_decrypt_sync, ciphertext)


async def save_account_backup(
    session: AsyncSession,
    *,
    tenant_id: str,
    account: dict,
    secret: str,
    deleted_by: str,
    reason: str,
) -> AccountBackup:
    """Guarda un backup cifrado ANTES de borrar la cuenta en CyberArk, y
    commitea su propia fila — independiente del commit de audit que hace
    `writeop.run_write` después. Si el borrado posterior en CyberArk falla,
    este backup igual queda: es el orden seguro, nunca se borra sin backup."""
    ciphertext = await encrypt_secret(secret)
    row = AccountBackup(
        tenant_id=tenant_id,
        account_id=str(account.get("id") or ""),
        account_name=str(account.get("name") or ""),
        safe_name=str(account.get("safeName") or ""),
        user_name=str(account.get("userName") or ""),
        address=account.get("address"),
        platform_id=str(account.get("platformId") or ""),
        metadata_json=account,
        secret_ciphertext=ciphertext,
        deleted_by=deleted_by,
        reason=reason,
    )
    session.add(row)
    await session.commit()
    return row


async def list_backups(session: AsyncSession, tenant_id: str) -> list[AccountBackup]:
    rows = (
        (
            await session.execute(
                select(AccountBackup)
                .where(AccountBackup.tenant_id == tenant_id)
                .order_by(AccountBackup.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_backup(session: AsyncSession, tenant_id: str, backup_id: int) -> AccountBackup | None:
    row = await session.get(AccountBackup, backup_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


async def save_platform_backup(
    session: AsyncSession,
    *,
    tenant_id: str,
    platform_id: str,
    numeric_id: str,
    package: bytes,
    deleted_by: str,
) -> PlatformBackup:
    """Guarda el paquete de "Export Platform" ANTES de borrar la plataforma
    en CyberArk, y commitea su propia fila — mismo orden seguro que
    `save_account_backup`. Sin cifrar: es política/config del CPM, no un
    secreto de cliente."""
    row = PlatformBackup(
        tenant_id=tenant_id,
        platform_id=platform_id,
        numeric_id=numeric_id,
        package=package,
        size_bytes=len(package),
        deleted_by=deleted_by,
    )
    session.add(row)
    await session.commit()
    return row


async def list_platform_backups(session: AsyncSession, tenant_id: str) -> list[PlatformBackup]:
    rows = (
        (
            await session.execute(
                select(PlatformBackup)
                .where(PlatformBackup.tenant_id == tenant_id)
                .order_by(PlatformBackup.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_platform_backup(
    session: AsyncSession, tenant_id: str, backup_id: int
) -> PlatformBackup | None:
    row = await session.get(PlatformBackup, backup_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row
