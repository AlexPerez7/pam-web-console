"""Fase 3: safe.member.update / safe.member.remove — editar y quitar un
miembro existente. Ver docs/specs/safe-member-edit.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.safes import (
    build_safe_member_remove_plan,
    build_safe_member_update_plan,
    find_member,
    safe_member_update_body,
)
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

MEMBER = {
    "memberName": "PAM-Auditors",
    "memberType": "Group",
    "permissions": {"useAccounts": True, "retrieveAccounts": True, "listAccounts": True},
}


# --- find_member -------------------------------------------------------- #
def test_find_member_case_insensitive():
    assert find_member([MEMBER], "pam-auditors") == MEMBER


def test_find_member_ausente():
    assert find_member([MEMBER], "no-existe") is None


# --- build_safe_member_update_plan --------------------------------------- #
def test_update_plan_diff_solo_lo_que_cambia():
    plan = build_safe_member_update_plan({"member": MEMBER}, {"preset": "accounts_manager"})
    fields = {d["field"] for d in plan.diff}
    assert "Use" not in fields  # useAccounts ya estaba en true en ambos presets -> sin diff
    assert "Add" in fields  # accounts_manager agrega addAccounts
    assert plan.confirm_phrase == "PAM-Auditors"
    assert plan.effect is not None


def test_update_plan_sin_cambios_es_no_op():
    same = {"preset": "custom", "permissions": dict(MEMBER["permissions"])}
    plan = build_safe_member_update_plan({"member": MEMBER}, same)
    assert plan.diff == []
    assert plan.effect is None  # no_op en run_write: not patch and not effect


def test_update_plan_warning_sin_permisos():
    plan = build_safe_member_update_plan(
        {"member": MEMBER}, {"preset": "custom", "permissions": {}}
    )
    assert any("sin ningún permiso" in w for w in plan.warnings)


def test_update_plan_warning_admin():
    plan = build_safe_member_update_plan({"member": MEMBER}, {"preset": "full"})
    assert any("configuración y los permisos de la safe" in w for w in plan.warnings)


def test_update_plan_vencimiento_en_diff():
    plan = build_safe_member_update_plan(
        {"member": MEMBER},
        {
            "preset": "custom",
            "permissions": MEMBER["permissions"],
            "membership_expiration_date": 1893456000,
        },
    )
    assert any(d["field"] == "vence" for d in plan.diff)


def test_safe_member_update_body():
    body = safe_member_update_body("PAM-Auditors", {"preset": "read_only"})
    assert body["memberName"] == "PAM-Auditors"
    assert body["permissions"]["retrieveAccounts"] is True
    assert "membershipExpirationDate" not in body


# --- build_safe_member_remove_plan --------------------------------------- #
def test_remove_plan_muestra_lo_que_se_pierde():
    plan = build_safe_member_remove_plan({"member": MEMBER}, {})
    fields = {d["field"]: d["to"] for d in plan.diff}
    assert fields["miembro"] is None  # from -> None
    assert "PAM-Auditors" in plan.effect
    assert plan.confirm_phrase == "PAM-Auditors"


# --- endpoints ------------------------------------------------------------ #
class FakeClient:
    def __init__(self) -> None:
        self.members: list[dict] = [dict(MEMBER)]
        self.updated: list[tuple[str, str, dict]] = []
        self.removed: list[tuple[str, str]] = []

    async def safe_members(self, safe_id: str) -> list[dict]:
        return self.members

    async def update_safe_member(self, safe_id: str, member_name: str, body: dict) -> dict:
        self.updated.append((safe_id, member_name, body))
        return {"memberName": member_name}

    async def remove_safe_member(self, safe_id: str, member_name: str) -> None:
        self.removed.append((safe_id, member_name))


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
    operator_op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    approver_op = Operator(
        username="ap", password_hash=security.hash_password("x"), role=OperatorRole.approver
    )
    session.add_all([operator_op, approver_op])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(operator_id=approver_op.id, tenant_id="lab", role=OperatorRole.approver),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: approver_op
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "operator_op": operator_op,
        "approver_op": approver_op,
    }


def test_update_dry_run_trae_confirm_phrase(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/MiSafe/members/PAM-Auditors",
        json={"changes": {"preset": "accounts_manager"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["confirm_phrase"] == "PAM-Auditors"
    assert env["fake"].updated == []


def test_update_confirm_mismatch_422(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/MiSafe/members/PAM-Auditors",
        json={
            "changes": {"preset": "accounts_manager"},
            "dry_run": False,
            "reason": "ajuste de permisos OPS-1",
            "confirm": "PAM-Auditor",  # typo
        },
    )
    assert r.status_code == 422
    assert env["fake"].updated == []


def test_update_ejecucion_ok(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/MiSafe/members/PAM-Auditors",
        json={
            "changes": {"preset": "accounts_manager"},
            "dry_run": False,
            "reason": "ajuste de permisos OPS-1",
            "confirm": "PAM-Auditors",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].updated) == 1
    _safe_id, member_name, sent_body = env["fake"].updated[0]
    assert member_name == "PAM-Auditors"
    assert sent_body["permissions"]["addAccounts"] is True


def test_update_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/MiSafe/members/PAM-Auditors",
        json={"changes": {"preset": "read_only"}},
    )
    assert r.status_code == 403


def test_update_miembro_inexistente_404(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/MiSafe/members/no-existe",
        json={"changes": {"preset": "read_only"}},
    )
    assert r.status_code == 404


def test_remove_confirm_mismatch_422(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/safes/MiSafe/members/PAM-Auditors",
        json={"dry_run": False, "reason": "sale del equipo OPS-2", "confirm": "otro"},
    )
    assert r.status_code == 422
    assert env["fake"].removed == []


def test_remove_ejecucion_ok(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/safes/MiSafe/members/PAM-Auditors",
        json={
            "dry_run": False,
            "reason": "sale del equipo OPS-2",
            "confirm": "PAM-Auditors",
        },
    )
    assert r.status_code == 200
    assert env["fake"].removed == [("MiSafe", "PAM-Auditors")]


def test_remove_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/safes/MiSafe/members/PAM-Auditors", json={})
    assert r.status_code == 403
