"""account.update — el motor writeop end-to-end (fase-2-escritura)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNT = {
    "id": "42",
    "name": "svc-sql",
    "address": "old.db.local",
    "userName": "svc",
    "platformId": "WinServerLocal",
    "platformAccountProperties": {"Location": "DC1"},
}


class FakeClient:
    def __init__(self) -> None:
        self.account = dict(ACCOUNT)
        self.patched: list | None = None

    async def get_account(self, account_id: str) -> dict:
        return dict(self.account)

    async def update_account(self, account_id: str, patch: list[dict]) -> dict:
        self.patched = patch
        acc = dict(self.account)
        acc["platformAccountProperties"] = dict(acc.get("platformAccountProperties") or {})
        for p in patch:
            path = p["path"]
            if path.startswith("/platformAccountProperties/"):
                acc["platformAccountProperties"][path.split("/")[-1]] = p["value"]
            else:
                acc[path.lstrip("/")] = p["value"]
        self.account = acc
        return acc


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writeop, "tenant_client", fake_tc)

    store: dict[str, int] = {}

    async def incr(k: str, ttl: int) -> int:
        store[k] = store.get(k, 0) + 1
        return store[k]

    monkeypatch.setattr(writeop.cache, "incr_with_ttl", incr)

    tenant = Tenant(
        id="lab",
        name="Lab",
        client="Laboratorio",
        deployment=Deployment.privilege_cloud,
        pvwa_url="https://x",
        secret_ref="lab",
    )
    session.add(tenant)
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session

    async def operator(role: OperatorRole = OperatorRole.operator) -> Operator:
        op = Operator(
            username=f"op-{role.value}", password_hash=security.hash_password("x"), role=role
        )
        session.add(op)
        await session.flush()
        session.add(RoleAssignment(operator_id=op.id, tenant_id="lab", role=role))
        await session.flush()
        return op

    def client_as(op: Operator) -> TestClient:
        app.dependency_overrides[current_operator] = lambda: op
        return TestClient(app)

    return {
        "session": session,
        "tenant": tenant,
        "fake": fake,
        "store": store,
        "operator": operator,
        "client_as": client_as,
    }


def _url(aid: str = "42") -> str:
    return f"/tenants/lab/accounts/{aid}/update"


async def _audits(session) -> list[AuditLog]:
    return list((await session.execute(select(AuditLog))).scalars().all())


async def test_dry_run_no_toca_cyberark(env):
    c = env["client_as"](await env["operator"]())
    r = c.post(_url(), json={"changes": {"address": "new.db.local"}, "dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["diff"] == [{"field": "address", "from": "old.db.local", "to": "new.db.local"}]
    assert body["patch"] == [{"op": "replace", "path": "/address", "value": "new.db.local"}]
    assert env["fake"].patched is None  # no se ejecutó
    a = await _audits(env["session"])
    assert any(x.action == "account.update" and x.dry_run and x.result == "ok" for x in a)


async def test_ejecucion_con_reason(env):
    c = env["client_as"](await env["operator"]())
    r = c.post(
        _url(),
        json={
            "changes": {
                "address": "new.db.local",
                "platformAccountProperties": {"Location": "DC2"},
            },
            "dry_run": False,
            "reason": "la base migró de host",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["no_op"] is False
    assert body["before"]["address"] == "old.db.local"
    assert body["after"]["address"] == "new.db.local"
    assert {"op": "replace", "path": "/address", "value": "new.db.local"} in env["fake"].patched
    assert env["fake"].account["platformAccountProperties"]["Location"] == "DC2"
    a = await _audits(env["session"])
    row = next(x for x in a if x.action == "account.update" and not x.dry_run)
    assert row.detail["reason"] == "la base migró de host"
    assert row.detail["before"]["address"] == "old.db.local"


async def test_ejecucion_sin_reason_es_422(env):
    c = env["client_as"](await env["operator"]())
    r = c.post(_url(), json={"changes": {"address": "x"}, "dry_run": False})
    assert r.status_code == 422


async def test_viewer_no_puede_escribir(env):
    c = env["client_as"](await env["operator"](OperatorRole.viewer))
    r = c.post(_url(), json={"changes": {"address": "x"}, "dry_run": True})
    assert r.status_code == 403


async def test_rate_limit(env):
    c = env["client_as"](await env["operator"]())
    payload = {"changes": {"name": "svc-sql"}, "dry_run": False, "reason": "no-op de prueba xxxx"}
    # write_max_per_window = 30 -> el 31 debe cortar
    for _ in range(30):
        assert c.post(_url(), json=payload).status_code == 200
    assert c.post(_url(), json=payload).status_code == 429


async def test_cambiar_platform_id_agrega_warning(env):
    c = env["client_as"](await env["operator"]())
    r = c.post(_url(), json={"changes": {"platformId": "WinDomain"}, "dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["diff"] == [{"field": "platformId", "from": "WinServerLocal", "to": "WinDomain"}]
    assert any("propiedades que la plataforma nueva requiere" in w for w in body["warnings"])


async def test_ejecucion_cambia_platform_id(env):
    c = env["client_as"](await env["operator"]())
    r = c.post(
        _url(),
        json={
            "changes": {"platformId": "WinDomain"},
            "dry_run": False,
            "reason": "migración a plataforma de dominio",
        },
    )
    assert r.status_code == 200
    assert {"op": "replace", "path": "/platformId", "value": "WinDomain"} in env["fake"].patched
    assert env["fake"].account["platformId"] == "WinDomain"


async def test_no_op(env):
    c = env["client_as"](await env["operator"]())
    r = c.post(
        _url(),
        json={"changes": {"name": "svc-sql"}, "dry_run": False, "reason": "sin cambios reales"},
    )
    assert r.status_code == 200
    assert r.json()["no_op"] is True
    assert env["fake"].patched is None
