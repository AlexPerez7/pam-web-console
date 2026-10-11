"""GET /identity-users y /identity-roles — listas de solo lectura vía Redrock."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import identity as identity_api
from app.core.deps import current_operator
from app.cyberark import CyberArkError
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


class FakeClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def redrock_query(self, script: str) -> list[dict]:
        self.queries.append(script)
        if "FROM User" in script:
            return [
                {
                    "ID": "u1",
                    "Username": "jdoe",
                    "DisplayName": "John Doe",
                    "Email": "j@x.com",
                    "Status": "Active",
                }
            ]
        return [
            {
                "ID": "r1",
                "Name": "System Administrator",
                "Description": "Full access",
                "RoleType": "Static",
                "Organization": None,
            }
        ]

    async def identity_role_members(self, role_id: str) -> list[dict]:
        self.queries.append(f"members:{role_id}")
        return [
            {"Guid": "u2", "Name": "zoe@x", "Type": "User"},
            {"Guid": "g1", "Name": "Operadores", "Type": "Group"},
            {"Guid": "u1", "Name": "Ana@x", "Type": "User"},
        ]


class FailingRolesClient:
    """Simula un tenant donde `RoleType`/`Organization` no son columnas
    válidas de la tabla `Role` -- las primeras `fail_count` consultas
    fallan (Redrock rechaza TODA la query, no columna por columna), hasta
    que se llega a una que sólo pide columnas confirmadas."""

    def __init__(self, fail_count: int) -> None:
        self.fail_count = fail_count
        self.queries: list[str] = []

    async def redrock_query(self, script: str) -> list[dict]:
        self.queries.append(script)
        if len(self.queries) <= self.fail_count:
            raise CyberArkError("Redrock: invalid column name")
        return [{"ID": "r1", "Name": "System Administrator", "Description": "Full access"}]


async def _build_client(session, monkeypatch, fake):
    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(identity_api, "tenant_client", fake_tc)

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
    viewer = Operator(username="v", password_hash="x", role=OperatorRole.viewer)
    session.add(viewer)
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(identity_api.router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return TestClient(app)


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()
    return {"client": await _build_client(session, monkeypatch, fake), "fake": fake}


def test_lista_usuarios(env):
    r = env["client"].get("/tenants/lab/identity-users")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["users"][0]["username"] == "jdoe"


def test_lista_roles(env):
    r = env["client"].get("/tenants/lab/identity-roles")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    role = body["roles"][0]
    assert role["name"] == "System Administrator"
    assert role["roleType"] == "Static"
    assert role["organization"] is None


async def test_roles_degrada_si_organization_no_existe(session, monkeypatch):
    """El 1er intento (con RoleType+Organization) falla -- cae al 2do (sólo
    RoleType), que sí funciona."""
    fake = FailingRolesClient(fail_count=1)
    client = await _build_client(session, monkeypatch, fake)
    r = client.get("/tenants/lab/identity-roles")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert len(fake.queries) == 2
    assert "Organization" in fake.queries[0]
    assert "Organization" not in fake.queries[1]
    assert "RoleType" in fake.queries[1]


async def test_roles_degrada_a_columnas_base(session, monkeypatch):
    """Los primeros 2 intentos fallan (ni RoleType ni Organization existen)
    -- cae a la consulta base ID/Name/Description, que siempre funcionó."""
    fake = FailingRolesClient(fail_count=2)
    client = await _build_client(session, monkeypatch, fake)
    r = client.get("/tenants/lab/identity-roles")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert len(fake.queries) == 3
    assert fake.queries[2] == identity_api._ROLES_SQL_BASE


async def test_roles_propaga_error_si_hasta_la_base_falla(session, monkeypatch):
    """Si ni siquiera la consulta base funciona (tenant sin Identity, error
    real de Redrock), el error se propaga -- no queda enmascarado como una
    lista vacía."""
    fake = FailingRolesClient(fail_count=3)
    client = await _build_client(session, monkeypatch, fake)
    r = client.get("/tenants/lab/identity-roles")
    assert r.status_code >= 400
    assert len(fake.queries) == 3


def test_miembros_de_un_rol(env):
    r = env["client"].get("/tenants/lab/identity-roles/r1/members")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    # agrupados por tipo y ordenados por nombre sin distinguir mayúsculas
    assert [m["name"] for m in body["members"]] == ["Operadores", "Ana@x", "zoe@x"]
    assert body["members"][1] == {"id": "u1", "name": "Ana@x", "type": "User"}
    assert "members:r1" in env["fake"].queries


def test_candidatos_para_un_rol(env):
    r = env["client"].get("/tenants/lab/identity-roles/r1/candidates", params={"q": "jdo'e"})
    assert r.status_code == 200
    res = r.json()["results"]
    # el rol r1 no se ofrece como miembro de sí mismo
    assert [(c["name"], c["type"]) for c in res] == [("jdoe", "User")]
    # u1 ya figura en GetRoleMembers (por ID, aunque el nombre difiera)
    assert res[0]["alreadyMember"] is True
    # el término llega saneado al SQL de Redrock (sin comilla)
    assert all("jdo'e" not in q for q in env["fake"].queries)
