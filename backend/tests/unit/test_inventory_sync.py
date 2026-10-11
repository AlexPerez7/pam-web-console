"""app.core.inventory_sync: sync on-demand de account_snapshots. Ver
docs/specs/inventory-sync-postgres.md."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import inventory_sync
from app.core.inventory_sync import last_synced_at, sync_accounts
from app.models import AccountSnapshot


class FakeClient:
    """Simula `client.accounts_page` paginado, como el real: cada llamada
    devuelve exactamente `limit` filas (o menos, si no queda tanto) a
    partir de `offset` — mismo contrato que `client.get_all`/
    `accounts_page` reales, de los que `sync_accounts` depende para saber
    cuándo cortar (página más chica que lo pedido = última)."""

    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = accounts
        self.calls: list[tuple[int, int]] = []

    async def accounts_page(self, *, offset: int = 0, limit: int = 100, **_kwargs):
        self.calls.append((offset, limit))
        page = self.accounts[offset : offset + limit]
        return page, len(self.accounts)


def _acct(id_: str, **overrides) -> dict:
    row = {
        "id": id_,
        "userName": f"user-{id_}",
        "address": f"10.0.0.{id_}",
        "safeName": "SAFE-A",
        "platformId": "WinDomain",
        "modificationTime": 1700000000,
    }
    row.update(overrides)
    return row


async def test_sync_accounts_inserta_y_pagina(session, monkeypatch):
    monkeypatch.setattr(inventory_sync, "_PAGE_SIZE", 2)  # fuerza varias páginas
    accounts = [_acct(str(i)) for i in range(1, 6)]  # 5 cuentas / páginas de 2 -> 3 llamadas
    fake = FakeClient(accounts)

    count = await sync_accounts("lab", fake, session)
    await session.commit()

    assert count == 5
    assert len(fake.calls) == 3
    rows = (
        (await session.execute(select(AccountSnapshot).where(AccountSnapshot.tenant_id == "lab")))
        .scalars()
        .all()
    )
    assert {r.account_id for r in rows} == {"1", "2", "3", "4", "5"}
    by_id = {r.account_id: r for r in rows}
    assert by_id["1"].user_name == "user-1"
    assert by_id["1"].platform_id == "WinDomain"
    assert by_id["1"].raw["id"] == "1"


async def test_sync_accounts_actualiza_existentes_y_borra_huerfanas(session):
    await sync_accounts("lab", FakeClient([_acct("1"), _acct("2")]), session)
    await session.commit()

    # segunda sync: la cuenta 2 cambia de plataforma, la cuenta 1 desaparece
    # (borrada en CyberArk), aparece una cuenta 3 nueva.
    fake2 = FakeClient([_acct("2", platformId="UnixSSH"), _acct("3")])
    count = await sync_accounts("lab", fake2, session)
    await session.commit()

    assert count == 2
    rows = (
        (await session.execute(select(AccountSnapshot).where(AccountSnapshot.tenant_id == "lab")))
        .scalars()
        .all()
    )
    by_id = {r.account_id: r for r in rows}
    assert set(by_id) == {"2", "3"}
    assert by_id["2"].platform_id == "UnixSSH"


async def test_sync_accounts_aisla_por_tenant(session):
    await sync_accounts("globex", FakeClient([_acct("1")]), session)
    await sync_accounts("acme", FakeClient([_acct("1"), _acct("2")]), session)
    await session.commit()

    globex_ids = (
        (
            await session.execute(
                select(AccountSnapshot.account_id).where(AccountSnapshot.tenant_id == "globex")
            )
        )
        .scalars()
        .all()
    )
    assert globex_ids == ["1"]


async def test_last_synced_at_none_sin_datos(session):
    assert await last_synced_at("lab", session) is None


async def test_last_synced_at_devuelve_timestamp_aware(session):
    await sync_accounts("lab", FakeClient([_acct("1")]), session)
    await session.commit()

    synced = await last_synced_at("lab", session)
    assert synced is not None
    assert synced.tzinfo is not None
    assert datetime.now(UTC) - synced < timedelta(seconds=30)
