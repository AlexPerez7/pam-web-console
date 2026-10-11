"""Sección Troubleshooting: errores de rotación/verificación del CPM por
cuenta. Ver docs/specs/troubleshooting-errores-cpm.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import troubleshooting as troubleshooting_mod
from app.api.troubleshooting import router as troubleshooting_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNTS = [
    {"id": "1", "name": "ok-01", "secretManagement": {"status": "success"}},
    {
        "id": "2",
        "name": "db-retry",
        "safeName": "SQL-PROD",
        "platformId": "WinDomain",
        "secretManagement": {"status": "failure", "automaticManagementEnabled": True},
    },
    {
        "id": "3",
        "name": "db-given-up",
        "safeName": "SQL-PROD",
        "platformId": "WinDomain",
        "secretManagement": {
            "status": "failure",
            "automaticManagementEnabled": False,
            "manualManagementReason": "(CPM)MaxRetries",
        },
    },
]


class FakeClient:
    def __init__(self) -> None:
        self.accounts = list(ACCOUNTS)

    async def list_accounts(self, search=None) -> list:
        return self.accounts


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(troubleshooting_mod, "tenant_client", fake_tc)

    async def get_json(key: str):
        return None

    async def set_json(key: str, value: dict, ttl: int) -> None:
        return None

    monkeypatch.setattr(troubleshooting_mod.cache, "get_json", get_json)
    monkeypatch.setattr(troubleshooting_mod.cache, "set_json", set_json)

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
    outsider = Operator(
        username="o", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([viewer, outsider])
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(troubleshooting_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {"app": app, "client": TestClient(app), "fake": fake, "outsider": outsider}


def test_account_errors_resumen_y_detalle(env):
    r = env["client"].get("/tenants/lab/troubleshooting/account-errors")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["failed_total"] == 2
    assert body["summary"]["active_retry"] == 1
    assert body["summary"]["given_up"] == 1
    ids = {a["id"] for a in body["accounts"]}
    assert ids == {"2", "3"}
    by_id = {a["id"]: a for a in body["accounts"]}
    assert by_id["2"]["retrying"] is True
    assert by_id["3"]["reason"] == "Reintentos agotados (CPM)"


def test_account_errors_sin_fallas(env):
    env["fake"].accounts = [ACCOUNTS[0]]
    r = env["client"].get("/tenants/lab/troubleshooting/account-errors")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["failed_total"] == 0
    assert body["accounts"] == []


def test_account_errors_sin_acceso_al_tenant_403(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["outsider"]
    r = env["client"].get("/tenants/lab/troubleshooting/account-errors")
    assert r.status_code == 403
