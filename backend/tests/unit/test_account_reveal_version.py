"""account.reveal.history — revelar el valor de una versión HISTÓRICA de
la contraseña (pestaña "Versiones"). Ver
docs/specs/done/account-password-versions.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api.writes as writes_mod
from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import NotFound
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant


class FakeClient:
    def __init__(self) -> None:
        self.retrieved: list[tuple[str, str, int | None]] = []
        self.password = "old-s3cr3t!"
        self.missing = False

    async def retrieve_password(
        self, account_id: str, reason: str, version: int | None = None
    ) -> str:
        if self.missing:
            raise NotFound(f"cuenta '{account_id}' no existe")
        self.retrieved.append((account_id, reason, version))
        return self.password


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writeop, "tenant_client", fake_tc)
    monkeypatch.setattr(writes_mod, "tenant_client", fake_tc)

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
    tenant_admin = Operator(
        username="ta", password_hash=security.hash_password("x"), role=OperatorRole.tenant_admin
    )
    approver = Operator(
        username="ap", password_hash=security.hash_password("x"), role=OperatorRole.approver
    )
    session.add_all([tenant_admin, approver])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="lab", role=OperatorRole.tenant_admin
            ),
            RoleAssignment(operator_id=approver.id, tenant_id="lab", role=OperatorRole.approver),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: tenant_admin
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "tenant_admin": tenant_admin,
        "approver": approver,
        "session": session,
    }


def _post(env, **body):
    return env["client"].post("/tenants/lab/accounts/42/reveal-version", json=body)


def test_reveal_version_ok(env):
    r = _post(env, reason="verificar credencial con el cliente", version=3)
    assert r.status_code == 200
    assert r.json()["password"] == "old-s3cr3t!"
    assert env["fake"].retrieved == [("42", "verificar credencial con el cliente", 3)]


def test_reveal_version_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = _post(env, reason="verificar credencial con el cliente", version=3)
    assert r.status_code == 403
    assert env["fake"].retrieved == []


def test_reveal_version_sin_reason(env):
    r = _post(env, reason="corta", version=3)
    assert r.status_code == 422
    assert env["fake"].retrieved == []


def test_reveal_version_cuenta_inexistente(env):
    env["fake"].missing = True
    r = _post(env, reason="verificar credencial con el cliente", version=3)
    assert r.status_code == 404


async def test_reveal_version_audita_sin_el_secreto(env, session):
    _post(env, reason="verificar credencial con el cliente", version=3)
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "account.reveal.history")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].result == "ok"
    assert rows[0].detail["version"] == 3
    assert "old-s3cr3t!" not in str(rows[0].detail)
