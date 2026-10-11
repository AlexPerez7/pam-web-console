"""GET /tenants/{id}/accounts — filtro por columna server-side
(platform_id/user_name/address) vía account_snapshots. Ver
docs/specs/inventory-sync-postgres.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import inventory as inventory_mod
from app.api.inventory import router as inventory_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNTS = [
    {
        "id": "1",
        "userName": "svc-a",
        "address": "10.0.0.1",
        "safeName": "SAFE-A",
        "platformId": "WinDomain",
    },
    {
        "id": "2",
        "userName": "svc-b",
        "address": "10.0.0.2",
        "safeName": "SAFE-A",
        "platformId": "UnixSSH",
    },
    {
        "id": "3",
        "userName": "svc-c",
        "address": "10.0.0.3",
        "safeName": "SAFE-B",
        "platformId": "WinDomain",
    },
]


class FakeClient:
    def __init__(self) -> None:
        self.accounts = list(ACCOUNTS)
        self.calls = 0

    async def accounts_page(self, *, offset=0, limit=100, **_kwargs):
        self.calls += 1
        page = self.accounts[offset : offset + limit]
        return page, len(self.accounts)


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(inventory_mod, "tenant_client", fake_tc)

    session.add(
        Tenant(
            id="lab",
            name="Lab",
            client="A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="lab",
        )
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add(viewer)
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(inventory_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {"client": TestClient(app), "fake": fake, "session": session}


async def _audits(session) -> list[AuditLog]:
    return list((await session.execute(select(AuditLog))).scalars().all())


def test_sin_filtros_nuevos_va_en_vivo_sin_sync(env):
    r = env["client"].get("/tenants/lab/accounts")
    assert r.status_code == 200
    body = r.json()
    assert {a["id"] for a in body["accounts"]} == {"1", "2", "3"}
    assert "synced_at" not in body
    assert env["fake"].calls == 1  # un solo llamado a accounts_page, nada de sync


def test_filtro_por_platform_id_sincroniza_y_filtra(env):
    r = env["client"].get("/tenants/lab/accounts", params={"platform_id": "WinDomain"})
    assert r.status_code == 200
    body = r.json()
    assert {a["id"] for a in body["accounts"]} == {"1", "3"}
    assert body["count"] == 2
    assert body["synced_at"] is not None


def test_filtro_por_platform_id_es_parcial(env):
    # visto real contra Globex: un match exacto en un input de texto libre
    # ("filtrar…") confunde -- "Win" tiene que encontrar "WinDomain" igual
    # que ya hacen user_name/address.
    r = env["client"].get("/tenants/lab/accounts", params={"platform_id": "Win"})
    assert r.status_code == 200
    assert {a["id"] for a in r.json()["accounts"]} == {"1", "3"}


def test_filtro_por_user_name_es_parcial_case_insensible_ilike(env):
    r = env["client"].get("/tenants/lab/accounts", params={"user_name": "svc-b"})
    assert r.status_code == 200
    body = r.json()
    assert {a["id"] for a in body["accounts"]} == {"2"}


def test_segunda_request_dentro_del_ttl_no_re_sincroniza(env):
    env["client"].get("/tenants/lab/accounts", params={"platform_id": "WinDomain"})
    assert env["fake"].calls == 1
    env["client"].get("/tenants/lab/accounts", params={"address": "10.0.0"})
    assert env["fake"].calls == 1  # sigue siendo 1: la copia local seguía fresca


async def test_audita_account_sync_y_account_list(env):
    env["client"].get("/tenants/lab/accounts", params={"platform_id": "WinDomain"})
    audits = await _audits(env["session"])
    actions = {a.action for a in audits}
    assert "account.sync" in actions
    assert "account.list" in actions
    sync_entry = next(a for a in audits if a.action == "account.sync")
    assert sync_entry.result == "ok"
    list_entry = next(a for a in audits if a.action == "account.list")
    assert list_entry.detail["source"] == "snapshot"
    assert list_entry.detail["filters"] == {"platform_id": "WinDomain"}


def test_borrado_en_cyberark_desaparece_tras_re_sync(env, monkeypatch):
    r = env["client"].get("/tenants/lab/accounts", params={"platform_id": "WinDomain"})
    assert {a["id"] for a in r.json()["accounts"]} == {"1", "3"}

    env["fake"].accounts = [a for a in ACCOUNTS if a["id"] != "1"]

    from app.config import get_settings

    monkeypatch.setenv("PAM_ACCOUNTS_SYNC_TTL", "0")  # fuerza a re-sincronizar
    get_settings.cache_clear()
    r = env["client"].get("/tenants/lab/accounts", params={"platform_id": "WinDomain"})
    get_settings.cache_clear()
    assert {a["id"] for a in r.json()["accounts"]} == {"3"}
