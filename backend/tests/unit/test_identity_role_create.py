"""identity_role.create — alta de un rol estático y vacío de Identity."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.identity_roles import plan_identity_role_create
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def test_plan_describe_nombre_descripcion_y_tipo():
    plan = plan_identity_role_create({}, {"name": "Acceso FW", "description": "Firewalls"})
    assert plan.diff == [
        {"field": "nombre", "from": None, "to": "Acceso FW"},
        {"field": "descripción", "from": None, "to": "Firewalls"},
        {"field": "tipo", "from": None, "to": "Static"},
    ]
    assert plan.object_count == 1
    assert "Acceso FW" in plan.effect and "sin miembros" in plan.effect
    assert plan.warnings == []


def test_plan_sin_descripcion_no_la_lista():
    plan = plan_identity_role_create({}, {"name": "Acceso FW", "description": None})
    assert [d["field"] for d in plan.diff] == ["nombre", "tipo"]


def test_plan_avisa_si_el_nombre_parece_de_administracion():
    plan = plan_identity_role_create({}, {"name": "Platform Admins"})
    assert any("administración" in w for w in plan.warnings)


class FakeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, str | None]] = []
        self.existing = [{"Name": "Acceso GCP Producción"}, {"Name": "Operadores"}]

    async def redrock_query(self, script: str) -> list[dict]:
        return self.existing

    async def create_identity_role(self, name: str, description: str | None = None) -> dict:
        self.created.append((name, description))
        return {"id": "r-new", "name": name}


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
    op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([op, viewer])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


BODY = {"name": "  Acceso Firewalls  ", "description": "Equipo de redes"}
REASON = "ticket OPS-200, rol para el equipo de redes"


def test_dry_run_no_toca_cyberark(env):
    r = env["client"].post("/tenants/lab/identity-roles", json={"changes": BODY, "dry_run": True})
    assert r.status_code == 200
    assert [d["field"] for d in r.json()["diff"]] == ["nombre", "descripción", "tipo"]
    assert env["fake"].created == []


def test_ejecucion_crea_el_rol_con_el_nombre_recortado(env):
    r = env["client"].post(
        "/tenants/lab/identity-roles",
        json={"changes": BODY, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].created == [("Acceso Firewalls", "Equipo de redes")]


def test_sin_descripcion_manda_none(env):
    r = env["client"].post(
        "/tenants/lab/identity-roles",
        json={"changes": {"name": "Solo Nombre"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].created == [("Solo Nombre", None)]


def test_nombre_duplicado_sin_distinguir_mayusculas_no_crea_nada(env):
    r = env["client"].post(
        "/tenants/lab/identity-roles",
        json={"changes": {"name": "acceso gcp producción"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert r.json()["no_op"] is True
    assert any("ya existe" in w for w in r.json()["warnings"])
    assert env["fake"].created == []


def test_plan_de_nombre_duplicado_es_no_op():
    plan = plan_identity_role_create({"exists": True}, {"name": "Operadores"})
    assert plan.effect is None and plan.diff == [] and plan.patch == []
    assert any("ya existe" in w for w in plan.warnings)


def test_nombre_en_blanco_es_422(env):
    r = env["client"].post(
        "/tenants/lab/identity-roles", json={"changes": {"name": "   "}, "dry_run": True}
    )
    assert r.status_code == 422


def test_ejecutar_sin_razon_es_rechazado(env):
    r = env["client"].post("/tenants/lab/identity-roles", json={"changes": BODY, "dry_run": False})
    assert r.status_code in (400, 422)
    assert env["fake"].created == []


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post("/tenants/lab/identity-roles", json={"changes": BODY, "dry_run": True})
    assert r.status_code == 403
