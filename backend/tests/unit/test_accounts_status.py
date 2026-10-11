"""Estado de cuentas en la grilla: `account_status_flags`,
`GET /accounts?status=` y `GET /accounts/summary` (chips de la grilla)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import inventory as inventory_mod
from app.api.inventory import router as inventory_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.domain.accounts import account_status_flags
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def _acc(i, *, status="success", auto=True, kind="password", reason=""):
    return {
        "id": str(i),
        "userName": f"svc{i}",
        "address": f"10.0.0.{i}",
        "safeName": "S",
        "platformId": "WinDomain",
        "secretType": kind,
        "secretManagement": {
            "status": status,
            "automaticManagementEnabled": auto,
            "manualManagementReason": reason,
        },
    }


ACCOUNTS = [
    _acc(1),
    _acc(2, status="failure"),
    _acc(3, auto=False),
    _acc(4, status="Failed", auto=False),
    _acc(5, kind="key"),
    _acc(6, status="failure", auto=False, reason="(CPM)MaxRetries"),
]


def test_flags():
    assert account_status_flags(ACCOUNTS[0]) == set()
    assert account_status_flags(ACCOUNTS[1]) == {"failure"}
    assert account_status_flags(ACCOUNTS[3]) == {"failure", "manual"}
    # el tipo de secreto (llave SSH) no es un estado
    assert account_status_flags(ACCOUNTS[4]) == set()
    # deshabilitada por el CPM tras agotar reintentos: no cuenta como "manual"
    assert account_status_flags(ACCOUNTS[5]) == {"failure", "cpm_disabled"}
    assert account_status_flags({}) == set()


class FakeClient:
    async def accounts_page(self, *, offset=0, limit=100, **_kwargs):
        return ACCOUNTS[offset : offset + limit], len(ACCOUNTS)


@pytest_asyncio.fixture
async def client(session, monkeypatch):
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
    return TestClient(app)


def test_summary_cuenta_por_estado(client):
    r = client.get("/tenants/lab/accounts/summary")
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["failure"], body["manual"]) == (6, 3, 2)
    assert "ssh_key" not in body
    assert body["cpm_disabled"] == 1
    assert body["synced_at"] is not None


def test_filtro_por_estado_pagina_sobre_lo_filtrado(client):
    r = client.get("/tenants/lab/accounts", params={"status": "failure", "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3 and len(body["accounts"]) == 1
    r2 = client.get("/tenants/lab/accounts", params={"status": "failure", "offset": 1})
    assert [a["id"] for a in r2.json()["accounts"]] == ["4", "6"]


def test_filtro_deshabilitada_por_el_cpm(client):
    r = client.get("/tenants/lab/accounts", params={"status": "cpm_disabled"})
    assert [a["id"] for a in r.json()["accounts"]] == ["6"]


def test_filtro_por_estado_se_combina_con_otros(client):
    r = client.get("/tenants/lab/accounts", params={"status": "manual", "user_name": "svc3"})
    assert [a["id"] for a in r.json()["accounts"]] == ["3"]


def test_estado_invalido_422(client):
    assert client.get("/tenants/lab/accounts", params={"status": "otro"}).status_code == 422
