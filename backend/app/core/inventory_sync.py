"""
Sync on-demand (con TTL) de la copia local de cuentas (`account_snapshots`)
que alimenta el filtro por columna server-side de la grilla de Cuentas —
mismo patrón que el caché del Dashboard/Safes/Platforms, pero en Postgres
en vez de Redis, porque acá se necesita filtrar/ordenar por campos
arbitrarios en SQL, no sólo devolver la lista completa. Ver
docs/specs/inventory-sync-postgres.md.

Sólo lectura para la grilla: ninguna escritura de PAM Web Console lee de acá, todas
van en vivo contra CyberArk vía `writeop.run_write`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cyberark import CyberArkClient
from app.models import AccountSnapshot

_PAGE_SIZE = 1000


async def last_synced_at(tenant_id: str, session: AsyncSession) -> datetime | None:
    """`synced_at` más viejo de la copia local del tenant, o `None` si
    nunca se sincronizó (o quedó vacía, ej. tenant sin cuentas)."""
    result = (
        await session.execute(
            select(func.min(AccountSnapshot.synced_at)).where(
                AccountSnapshot.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    # SQLite (tests unit) no preserva tzinfo al releer un DateTime(timezone=True)
    # -- siempre escribimos en UTC, así que un valor naive es UTC igual.
    if result is not None and result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result


async def sync_accounts(tenant_id: str, client: CyberArkClient, session: AsyncSession) -> int:
    """Pagina TODAS las cuentas del tenant y actualiza `account_snapshots`:
    upsert de lo que vino, borrado de lo que ya no está (cuentas borradas
    en CyberArk). Devuelve la cantidad sincronizada. No commitea — el
    llamador decide (mismo criterio que el resto de `app.core`)."""
    existing_ids = set(
        (
            await session.execute(
                select(AccountSnapshot.account_id).where(AccountSnapshot.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    seen_ids: set[str] = set()
    synced_at = datetime.now(UTC)

    offset = 0
    while True:
        rows, _total = await client.accounts_page(offset=offset, limit=_PAGE_SIZE)
        if not rows:
            break
        for row in rows:
            account_id = str(row.get("id"))
            if not account_id or account_id == "None":
                continue
            seen_ids.add(account_id)
            values = {
                "platform_id": row.get("platformId"),
                "user_name": row.get("userName"),
                "address": row.get("address"),
                "safe_name": row.get("safeName"),
                "modification_time": row.get("modificationTime"),
                "raw": row,
                "synced_at": synced_at,
            }
            if account_id in existing_ids:
                await session.execute(
                    update(AccountSnapshot)
                    .where(
                        AccountSnapshot.tenant_id == tenant_id,
                        AccountSnapshot.account_id == account_id,
                    )
                    .values(**values)
                )
            else:
                session.add(AccountSnapshot(tenant_id=tenant_id, account_id=account_id, **values))
        # mismo criterio que `client.get_all`: una página más chica que lo
        # pedido es la última, sin depender de que `total`/`count` sea exacto.
        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    orphan_ids = existing_ids - seen_ids
    if orphan_ids:
        await session.execute(
            delete(AccountSnapshot).where(
                AccountSnapshot.tenant_id == tenant_id,
                AccountSnapshot.account_id.in_(orphan_ids),
            )
        )

    return len(seen_ids)
