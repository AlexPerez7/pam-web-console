"""identity_role.member.add — agregar miembros a un rol de Identity."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.identity_roles import build_identity_role_member_add_plan, normalize_new_members
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

CURRENT = {
    "role_name": "Acceso GCP Producción",
    "members": [{"Guid": "u-1", "Name": "alice@x", "Type": "User"}],
}


def test_normalize_descarta_vacios_duplicados_y_tipos_no_soportados():
    out = normalize_new_members(
        [
            {"id": "u-2", "name": "bob@x", "type": "User"},
            {"id": "u-2", "name": "bob@x", "type": "User"},
            {"id": "", "name": "sin id"},
            {"id": "g-1", "name": "grupo", "type": "Group"},
            "basura",
        ]
    )
    assert out == [{"id": "u-2", "name": "bob@x", "type": "User"}]


def test_plan_agrega_solo_los_nuevos():
    plan = build_identity_role_member_add_plan(
        CURRENT,
        {
            "members": [
                {"id": "u-1", "name": "alice@x", "type": "User"},
                {"id": "u-2", "name": "bob@x", "type": "User"},
            ]
        },
    )
    assert plan.diff == [{"field": "miembro (usuario)", "from": None, "to": "bob@x"}]
    assert plan.object_count == 1
    assert "bob@x" in plan.effect and "Acceso GCP Producción" in plan.effect
    assert any("alice@x" in w and "ya es miembro" in w for w in plan.warnings)


def test_plan_sin_nuevos_es_no_op():
    plan = build_identity_role_member_add_plan(
        CURRENT, {"members": [{"id": "u-1", "name": "alice@x", "type": "User"}]}
    )
    assert plan.effect is None and plan.diff == []


def test_plan_avisa_rol_admin_y_rol_anidado():
    plan = build_identity_role_member_add_plan(
        {"role_name": "System Administrator", "members": []},
        {"members": [{"id": "r-2", "name": "Operadores", "type": "Role"}]},
    )
    assert any("administración" in w for w in plan.warnings)
    assert any("TODOS los miembros" in w for w in plan.warnings)


class FakeClient:
    def __init__(self) -> None:
        self.add_calls: list[tuple[str, list[str], list[str]]] = []
        self.role_exists = True

    async def redrock_query(self, script: str) -> list[dict]:
        return [{"ID": "r-1", "Name": "Acceso GCP Producción"}] if self.role_exists else []

    async def identity_role_members(self, role_id: str) -> list[dict]:
        return CURRENT["members"]

    async def identity_role_add_members(self, role_id, *, users, roles) -> None:
        self.add_calls.append((role_id, users, roles))


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


BODY = {
    "members": [
        {"id": "u-1", "name": "alice@x", "type": "User"},
        {"id": "u-2", "name": "bob@x", "type": "User"},
        {"id": "r-9", "name": "Operadores", "type": "Role"},
    ]
}


def test_dry_run_no_toca_cyberark(env):
    r = env["client"].post(
        "/tenants/lab/identity-roles/r-1/members", json={"changes": BODY, "dry_run": True}
    )
    assert r.status_code == 200
    assert len(r.json()["diff"]) == 2
    assert env["fake"].add_calls == []


def test_ejecucion_agrega_solo_los_nuevos(env):
    r = env["client"].post(
        "/tenants/lab/identity-roles/r-1/members",
        json={"changes": BODY, "dry_run": False, "reason": "ticket OPS-123, acceso a GCP"},
    )
    assert r.status_code == 200
    assert env["fake"].add_calls == [("r-1", ["u-2"], ["r-9"])]


def test_rol_inexistente_404(env):
    env["fake"].role_exists = False
    r = env["client"].post(
        "/tenants/lab/identity-roles/r-x/members", json={"changes": BODY, "dry_run": True}
    )
    assert r.status_code == 404


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/identity-roles/r-1/members", json={"changes": BODY, "dry_run": True}
    )
    assert r.status_code == 403
