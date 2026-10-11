"""CRUD de usuarios y grupos del Vault (Fase 3).

Ver docs/specs/vault-users-groups-crud.md. Endpoints/casing verificados
contra el módulo open-source `psPAS` (docs.cyberark.com bloqueó el fetch
automatizado para estas páginas puntuales).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.groups import (
    build_group_create_body,
    build_group_delete_plan,
    build_group_member_add_plan,
    build_group_member_remove_plan,
    build_group_update_body,
    build_group_update_plan,
    find_group_member,
    group_member_body,
    plan_group_create,
)
from app.domain.users import (
    build_user_create_body,
    build_user_delete_plan,
    build_user_update_body,
    build_user_update_plan,
    plan_user_create,
)
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

USER = {
    "id": 42,
    "username": "jsmith",
    "personalDetails": {"firstName": "John", "lastName": "Smith"},
    "changePassOnNextLogon": False,
}

GROUP_EMPTY = {"id": 7, "groupName": "DBAs", "description": "grupo de DBAs", "members": []}
GROUP_WITH_MEMBERS = {
    "id": 8,
    "groupName": "Ops",
    "members": [{"username": "op1"}],
}


# --- dominio: usuarios ---------------------------------------------------- #


def test_build_user_create_body_sin_password_en_ningun_lado_menos_el_body():
    changes = {
        "username": "jsmith",
        "initial_password": "s3cr3t!",
        "first_name": "John",
        "last_name": "Smith",
        "change_pass_on_next_logon": True,
    }
    body = build_user_create_body(changes)
    assert body == {
        "UserName": "jsmith",
        "InitialPassword": "s3cr3t!",
        "ChangePassOnNextLogon": True,
        "FirstName": "John",
        "LastName": "Smith",
    }


def test_plan_user_create_nunca_expone_el_password():
    plan = plan_user_create({}, {"username": "jsmith", "initial_password": "s3cr3t!"})
    dumped = str(plan.diff) + str(plan.warnings) + str(plan.effect)
    assert "s3cr3t!" not in dumped
    assert plan.diff[0] == {"field": "usuario", "from": None, "to": "jsmith"}


def test_build_user_update_body_parte_del_actual():
    body = build_user_update_body(USER, {"first_name": "Jonathan"})
    assert body["id"] == 42
    assert body["username"] == "jsmith"
    assert body["FirstName"] == "Jonathan"
    assert body["LastName"] == "Smith"  # sin cambios, viene del current
    assert body["ChangePassOnNextLogon"] is False


def test_build_user_update_plan_diff_solo_lo_que_cambia():
    plan = build_user_update_plan(USER, {"first_name": "Jonathan"})
    assert plan.diff == [{"field": "nombre", "from": "John", "to": "Jonathan"}]
    assert plan.confirm_phrase is None


def test_build_user_update_plan_sin_cambios_es_no_op():
    plan = build_user_update_plan(USER, {"first_name": "John"})
    assert plan.diff == []
    assert plan.effect is None


def test_build_user_delete_plan_confirm_phrase():
    plan = build_user_delete_plan(USER, {})
    assert plan.confirm_phrase == "jsmith"
    assert {"field": "usuario", "from": "jsmith", "to": None} in plan.diff


# --- dominio: grupos -------------------------------------------------------- #


def test_build_group_create_body():
    body = build_group_create_body({"group_name": "DBAs", "description": "grupo de DBAs"})
    assert body == {"groupName": "DBAs", "description": "grupo de DBAs"}


def test_plan_group_create_effect():
    plan = plan_group_create({}, {"group_name": "DBAs"})
    assert "DBAs" in plan.effect
    assert plan.confirm_phrase is None


def test_build_group_update_body_solo_nombre():
    assert build_group_update_body("7", {"group_name": "DBAs-Nuevo"}) == {
        "id": "7",
        "groupName": "DBAs-Nuevo",
    }


def test_build_group_update_plan_renombra():
    plan = build_group_update_plan(GROUP_EMPTY, {"group_name": "DBAs-Nuevo"})
    assert plan.diff == [{"field": "nombre", "from": "DBAs", "to": "DBAs-Nuevo"}]
    assert plan.confirm_phrase is None


def test_build_group_update_plan_mismo_nombre_no_op():
    plan = build_group_update_plan(GROUP_EMPTY, {"group_name": "DBAs"})
    assert plan.diff == []
    assert plan.effect is None


def test_build_group_delete_plan_confirm_phrase():
    plan = build_group_delete_plan({"group": GROUP_EMPTY}, {})
    assert plan.confirm_phrase == "DBAs"


def test_group_member_body_vault():
    body = group_member_body({"member_id": "jsmith", "member_type": "vault"})
    assert body == {"memberId": "jsmith", "memberType": "vault"}


def test_group_member_body_domain_incluye_domain_name():
    body = group_member_body(
        {"member_id": "jsmith", "member_type": "domain", "domain_name": "corp.local"}
    )
    assert body == {"memberId": "jsmith", "memberType": "domain", "domainName": "corp.local"}


def test_build_group_member_add_plan_warning_si_ya_es_miembro():
    current = {"members": [{"username": "op1"}]}
    plan = build_group_member_add_plan(current, {"member_id": "op1", "member_type": "vault"})
    assert any("ya es miembro" in w for w in plan.warnings)


def test_find_group_member():
    members = [{"memberName": "op1", "memberType": "vault"}]
    assert find_group_member(members, "OP1")["memberName"] == "op1"
    assert find_group_member(members, "nadie") is None


def test_build_group_member_remove_plan_confirm_phrase():
    plan = build_group_member_remove_plan(
        {"member": {"memberName": "op1", "memberType": "vault"}}, {}
    )
    assert plan.confirm_phrase == "op1"


# --- endpoints ---------------------------------------------------------------- #


class FakeClient:
    def __init__(self) -> None:
        self.users = {"42": dict(USER)}
        self.groups = {"7": dict(GROUP_EMPTY), "8": dict(GROUP_WITH_MEMBERS)}
        self.created_users: list[dict] = []
        self.updated_users: list[tuple[str, dict]] = []
        self.deleted_users: list[str] = []
        self.created_groups: list[dict] = []
        self.updated_groups: list[tuple[str, dict]] = []
        self.deleted_groups: list[str] = []
        self.added_members: list[tuple[str, dict]] = []
        self.removed_members: list[tuple[str, str]] = []

    async def get_user(self, user_id: str) -> dict:
        return self.users[user_id]

    async def create_user(self, body: dict) -> dict:
        self.created_users.append(body)
        return {"id": 99, **body}

    async def update_user(self, user_id: str, body: dict) -> dict:
        self.updated_users.append((user_id, body))
        return body

    async def delete_user(self, user_id: str) -> None:
        self.deleted_users.append(user_id)

    async def get_group(self, group_id: str) -> dict:
        return self.groups[group_id]

    async def create_group(self, body: dict) -> dict:
        self.created_groups.append(body)
        return {"id": 100, **body}

    async def update_group(self, group_id: str, body: dict) -> dict:
        self.updated_groups.append((group_id, body))
        return body

    async def delete_group(self, group_id: str) -> None:
        self.deleted_groups.append(group_id)

    async def add_group_member(self, group_id: str, body: dict) -> dict:
        self.added_members.append((group_id, body))
        return body

    async def remove_group_member(self, group_id: str, member: str) -> None:
        self.removed_members.append((group_id, member))

    async def user_group_members(self, group_id: str) -> list[dict]:
        raw = self.groups[group_id].get("members") or []
        return [{"memberName": m.get("username"), "memberType": "vault"} for m in raw]


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
    approver = Operator(
        username="ap", password_hash=security.hash_password("x"), role=OperatorRole.approver
    )
    operator_op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    session.add_all([approver, operator_op])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=approver.id, tenant_id="lab", role=OperatorRole.approver),
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: approver
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "approver": approver,
        "operator_op": operator_op,
    }


REASON = "alta pedida por el equipo, ticket OPS-3"


def test_user_create_ejecucion_ok(env):
    r = env["client"].post(
        "/tenants/lab/users",
        json={
            "changes": {"username": "new.user", "initial_password": "s3cr3t!"},
            "dry_run": False,
            "reason": REASON,
        },
    )
    assert r.status_code == 200
    assert env["fake"].created_users == [
        {
            "UserName": "new.user",
            "InitialPassword": "s3cr3t!",
            "ChangePassOnNextLogon": True,
        }
    ]


def test_user_create_rbac_operator_permitido(env):
    """Riesgo bajo: RBAC operator+, no requiere approver."""
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post(
        "/tenants/lab/users",
        json={
            "changes": {"username": "new.user", "initial_password": "s3cr3t!"},
            "dry_run": False,
            "reason": REASON,
        },
    )
    assert r.status_code == 200


def test_user_update_ejecucion_ok(env):
    r = env["client"].put(
        "/tenants/lab/users/42",
        json={"changes": {"first_name": "Jonathan"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].updated_users[0][1]["FirstName"] == "Jonathan"


def test_user_delete_requiere_confirm(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/users/42",
        json={"dry_run": False, "reason": REASON, "confirm": "otro"},
    )
    assert r.status_code == 422
    assert env["fake"].deleted_users == []


def test_user_delete_ejecucion_ok(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/users/42",
        json={"dry_run": False, "reason": REASON, "confirm": "jsmith"},
    )
    assert r.status_code == 200
    assert env["fake"].deleted_users == ["42"]


def test_user_delete_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/users/42", json={})
    assert r.status_code == 403


def test_group_create_ejecucion_ok(env):
    r = env["client"].post(
        "/tenants/lab/groups",
        json={"changes": {"group_name": "DBAs"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].created_groups == [{"groupName": "DBAs"}]


def test_group_update_renombra(env):
    r = env["client"].put(
        "/tenants/lab/groups/7",
        json={"changes": {"group_name": "DBAs-Nuevo"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].updated_groups == [("7", {"id": "7", "groupName": "DBAs-Nuevo"})]


def test_group_delete_bloqueado_si_tiene_miembros(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/groups/8",
        json={"dry_run": False, "reason": REASON, "confirm": "Ops"},
    )
    assert r.status_code == 422
    assert env["fake"].deleted_groups == []


def test_group_delete_vacio_ok(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/groups/7",
        json={"dry_run": False, "reason": REASON, "confirm": "DBAs"},
    )
    assert r.status_code == 200
    assert env["fake"].deleted_groups == ["7"]


def test_group_delete_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/groups/7", json={})
    assert r.status_code == 403


def test_group_member_add_ejecucion_ok(env):
    r = env["client"].post(
        "/tenants/lab/groups/7/members",
        json={
            "changes": {"member_id": "new.user", "member_type": "vault"},
            "dry_run": False,
            "reason": REASON,
        },
    )
    assert r.status_code == 200
    assert env["fake"].added_members == [("7", {"memberId": "new.user", "memberType": "vault"})]


def test_group_member_remove_requiere_confirm(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/groups/8/members/op1",
        json={"dry_run": False, "reason": REASON, "confirm": "otro"},
    )
    assert r.status_code == 422
    assert env["fake"].removed_members == []


def test_group_member_remove_ejecucion_ok(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/groups/8/members/op1",
        json={"dry_run": False, "reason": REASON, "confirm": "op1"},
    )
    assert r.status_code == 200
    assert env["fake"].removed_members == [("8", "op1")]


def test_group_member_remove_no_es_miembro_404(env):
    r = env["client"].request("DELETE", "/tenants/lab/groups/8/members/nadie", json={})
    assert r.status_code == 404


def test_group_member_remove_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/groups/8/members/op1", json={})
    assert r.status_code == 403
