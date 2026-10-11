"""account.reveal.bulk — backup cifrado de TODAS las cuentas del tenant
(usuario/address/plataforma/safe/remoteMachines/contraseña). Ver
docs/specs/account-credentials-backup.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.credentials_backup as cb_mod
from app.api.credentials_backup import router as credentials_backup_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark import Forbidden, NotFound
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNTS = [
    {
        "id": "1",
        "name": "db-prod-01",
        "userName": "svc-db",
        "address": "10.1.2.30",
        "platformId": "WinServerLocal",
        "safeName": "SQL-PROD",
        "secretType": "password",
    },
    {
        "id": "2",
        "name": "dom-svc-02",
        "userName": "svc-domain",
        "address": "corp.local",
        "platformId": "WinDomain",
        "safeName": "DOM-PROD",
        "secretType": "password",
        "remoteMachinesAccess": {"remoteMachines": "10.1.2.1;10.1.2.2"},
    },
    {
        "id": "3",
        "name": "broken-03",
        "userName": "svc-broken",
        "address": "10.1.2.99",
        "platformId": "WinServerLocal",
        "safeName": "SQL-PROD",
        "secretType": "password",
    },
]


class FakeClient:
    def __init__(self) -> None:
        self.accounts = [dict(a) for a in ACCOUNTS]
        self.retrieved: list[tuple[str, str]] = []
        self.fail_list = False

    async def list_accounts(self, search: str | None = None) -> list:
        if self.fail_list:
            raise NotFound("no se pudo listar")
        return self.accounts

    async def retrieve_password(self, account_id: str, reason: str) -> str:
        self.retrieved.append((account_id, reason))
        if account_id == "3":
            raise Forbidden("sin permiso Retrieve en esta safe")
        return f"s3cr3t-{account_id}"


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(cb_mod, "tenant_client", fake_tc)

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
    app.include_router(credentials_backup_router)

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


REASON = "backup previo a limpieza de cuentas OPS-1"


def _rows_from_zip(resp) -> list[dict]:
    import csv
    import io
    import zipfile

    import pyzipper

    password = resp.headers["x-backup-password"]
    with pyzipper.AESZipFile(io.BytesIO(resp.content)) as zf:
        zf.setpassword(password.encode())
        names = zf.namelist()
        assert len(names) == 1
        plain = zf.read(names[0]).decode()
    # confirma que un password incorrecto realmente falla (no es un no-op)
    with pyzipper.AESZipFile(io.BytesIO(resp.content)) as zf:
        zf.setpassword(b"password-incorrecto")
        try:
            zf.read(names[0])
        except (RuntimeError, zipfile.BadZipFile):
            pass
        else:
            raise AssertionError("el zip se abrió con una contraseña incorrecta")
    return list(csv.DictReader(io.StringIO(plain)))


def test_export_ok(env):
    r = env["client"].post("/tenants/lab/credentials-backup/export", json={"reason": REASON})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert ".zip" in r.headers["content-disposition"]
    assert len(r.headers["x-backup-password"]) > 10

    rows = _rows_from_zip(r)
    assert len(rows) == 3
    by_id = {row["accountId"]: row for row in rows}
    assert by_id["1"]["secret"] == "s3cr3t-1"
    assert by_id["1"]["retrieveStatus"] == "OK"
    assert by_id["2"]["remoteMachines"] == "10.1.2.1;10.1.2.2"
    assert by_id["3"]["retrieveStatus"] == "ERROR"
    assert by_id["3"]["secret"] == ""


def test_export_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post("/tenants/lab/credentials-backup/export", json={"reason": REASON})
    assert r.status_code == 403
    assert env["fake"].retrieved == []


def test_export_sin_reason(env):
    r = env["client"].post("/tenants/lab/credentials-backup/export", json={"reason": "corta"})
    assert r.status_code == 422
    assert env["fake"].retrieved == []


def test_export_falla_listado(env):
    env["fake"].fail_list = True
    r = env["client"].post("/tenants/lab/credentials-backup/export", json={"reason": REASON})
    assert r.status_code == 404


async def test_export_audita_conteo_sin_secretos(env, session):
    from sqlalchemy import select

    r = env["client"].post("/tenants/lab/credentials-backup/export", json={"reason": REASON})
    password = r.headers["x-backup-password"]
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "account.reveal.bulk")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["count"] == 3
    assert detail["ok"] == 2
    assert detail["error"] == 1
    assert "s3cr3t" not in str(detail)
    assert password not in str(detail)
