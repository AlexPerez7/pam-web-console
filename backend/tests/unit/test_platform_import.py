"""platform.import — crear una plataforma nueva desde un .zip subido.

Ver docs/specs/platform-import.md. Endpoint verificado contra el módulo
open-source `psPAS` (docs.cyberark.com bloqueó el fetch automatizado);
queda una ambigüedad real sin resolver sobre el formato del archivo en el
body (base64 vs array de bytes), a decidir probando contra Globex.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writes as writes_mod
from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.platforms import plan_platform_import
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ZIP_BYTES = b"PK\x03\x04fake-platform-package"


def test_plan_platform_import_effect():
    plan = plan_platform_import({}, {"filename": "WinDomain.zip", "size_bytes": 2048})
    assert "WinDomain.zip" in plan.effect
    assert "2.0 KB" in plan.effect
    assert plan.confirm_phrase is None
    assert plan.diff == []


class FakeClient:
    def __init__(self) -> None:
        self.imported: list[bytes] = []

    async def import_platform(self, file_bytes: bytes) -> dict:
        self.imported.append(file_bytes)
        return {"PlatformID": "Imported1"}


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writeop, "tenant_client", fake_tc)

    async def incr(k: str, ttl: int) -> int:
        return 1

    monkeypatch.setattr(writeop.cache, "incr_with_ttl", incr)

    async def cache_delete(key: str) -> None:
        return None

    monkeypatch.setattr(writes_mod.cache, "delete", cache_delete)

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
    operator_op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([operator_op, viewer])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: operator_op
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


REASON = "importar plataforma exportada de otro tenant, ticket OPS-5"


def test_import_dry_run_no_toca_nada(env):
    r = env["client"].post(
        "/tenants/lab/platforms/import",
        files={"file": ("WinDomain.zip", ZIP_BYTES, "application/zip")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert "WinDomain.zip" in body["effect"]
    assert env["fake"].imported == []


def test_import_ejecucion_ok_manda_base64(env):
    r = env["client"].post(
        "/tenants/lab/platforms/import",
        data={"dry_run": "false", "reason": REASON},
        files={"file": ("WinDomain.zip", ZIP_BYTES, "application/zip")},
    )
    assert r.status_code == 200
    assert env["fake"].imported == [ZIP_BYTES]


def test_import_extension_invalida_422(env):
    r = env["client"].post(
        "/tenants/lab/platforms/import",
        files={"file": ("WinDomain.txt", ZIP_BYTES, "text/plain")},
    )
    assert r.status_code == 422
    assert env["fake"].imported == []


def test_import_archivo_muy_grande_422(env, monkeypatch):
    monkeypatch.setattr(writes_mod, "_MAX_PLATFORM_IMPORT_BYTES", 4)
    r = env["client"].post(
        "/tenants/lab/platforms/import",
        files={"file": ("WinDomain.zip", ZIP_BYTES, "application/zip")},
    )
    assert r.status_code == 422
    assert env["fake"].imported == []


def test_import_rbac_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/platforms/import",
        files={"file": ("WinDomain.zip", ZIP_BYTES, "application/zip")},
    )
    assert r.status_code == 403


async def test_client_import_platform_encodes_base64(monkeypatch):
    """El body real que le llega a CyberArk — confirma que se manda en
    base64 (la ambigüedad de la spec), no como array de bytes crudo."""
    from app.cyberark.client import CyberArkClient

    captured: dict = {}

    async def fake_post(self, path, json=None, **kw):
        captured["path"] = path
        captured["json"] = json
        return {}

    monkeypatch.setattr(CyberArkClient, "post", fake_post)
    client = CyberArkClient.__new__(CyberArkClient)
    await client.import_platform(ZIP_BYTES)
    assert captured["path"] == "/API/Platforms/Import"
    assert captured["json"] == {"ImportFile": base64.b64encode(ZIP_BYTES).decode("ascii")}
