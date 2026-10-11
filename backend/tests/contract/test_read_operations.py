"""Ejemplos de contrato: lecturas basicas contra el tenant de lab.

Skippeados salvo que esten las PAM_CONTRACT_* (ver conftest.py de la carpeta).
"""

from __future__ import annotations


async def test_authenticate_ok(client):
    # el fixture ya llamo authenticate(); un segundo re-auth no debe fallar
    await client.authenticate()


async def test_list_platforms_forma_esperada(client):
    platforms = await client.list_platforms()
    assert isinstance(platforms, list)
    if platforms:
        p = platforms[0]
        assert isinstance(p, dict)
        assert "general" in p or "PlatformID" in p or "ID" in p


async def test_accounts_page_devuelve_total(client):
    rows, total = await client.accounts_page(limit=1)
    assert isinstance(rows, list)
    assert isinstance(total, int)
    assert len(rows) <= 1
