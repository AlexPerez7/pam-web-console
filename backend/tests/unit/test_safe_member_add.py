"""safe.member.add — buscar principal + agregar miembro a una safe."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.member_search import merge_member_candidates
from app.domain.safe_permissions import PRESETS, granted_keys, resolve_permissions
from app.domain.safes import build_safe_member_plan, safe_member_body
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


# --- permisos --------------------------------------------------------- #
def test_resolve_permissions_preset():
    perms = resolve_permissions("connect_only", None)
    assert perms["useAccounts"] is True
    assert perms["retrieveAccounts"] is False
    assert set(perms) == set(PRESETS["connect_only"])  # todas las keys presentes


def test_resolve_permissions_preset_con_overrides():
    perms = resolve_permissions("connect_only", {"retrieveAccounts": True, "viewAuditLog": True})
    assert perms["useAccounts"] is True  # del preset
    assert perms["retrieveAccounts"] is True  # override
    assert perms["viewAuditLog"] is True


def test_resolve_permissions_custom():
    perms = resolve_permissions("custom", {"listAccounts": True})
    assert perms["listAccounts"] is True
    assert perms["useAccounts"] is False
    assert all(isinstance(v, bool) for v in perms.values())


# --- build_safe_member_plan ------------------------------------------ #
def test_plan_preview_basico():
    plan = build_safe_member_plan(
        {"members": []},
        {"member_name": "PAM-Auditors", "member_type": "Group", "preset": "read_only"},
    )
    assert plan.patch == []
    assert "PAM-Auditors" in plan.effect
    fields = {d["field"]: d["to"] for d in plan.diff}
    assert fields["miembro"] == "PAM-Auditors (Group)"
    assert "listAccounts" in fields["permisos"]
    assert plan.warnings == []


def test_plan_warning_admin():
    plan = build_safe_member_plan(
        {"members": []},
        {"member_name": "x", "preset": "full"},
    )
    assert any("configuración y los permisos de la safe" in w for w in plan.warnings)


def test_plan_warning_duplicado():
    plan = build_safe_member_plan(
        {"members": [{"memberName": "Juan"}]},
        {"member_name": "juan", "preset": "connect_only"},
    )
    assert any("ya es miembro" in w for w in plan.warnings)


def test_plan_vencimiento_en_diff():
    plan = build_safe_member_plan(
        {"members": []},
        {"member_name": "x", "preset": "connect_only", "membership_expiration_date": 1893456000},
    )
    assert any(d["field"] == "vence" for d in plan.diff)


def test_safe_member_body():
    body = safe_member_body(
        {"member_name": "svc", "member_type": "User", "search_in": "Vault", "preset": "read_only"}
    )
    assert body["memberName"] == "svc"
    assert body["MemberType"] == "User"
    assert body["permissions"]["retrieveAccounts"] is True
    assert "membershipExpirationDate" not in body


# --- merge_member_candidates ---------------------------------------- #
def test_merge_dedup_y_already_member():
    vault_users = [{"username": "svc-app", "source": "Vault"}]
    identity_users = [
        {"Username": "svc-app", "DisplayName": "Servicio App", "SourceDs": "AD-Corp"},
        {"Username": "otro", "SourceDs": "AD-Corp"},
    ]
    out = merge_member_candidates(
        vault_users, [], identity_users, existing_members={"otro"}, kind="any"
    )
    by_name = {c["name"]: c for c in out}
    assert by_name["svc-app"]["searchIn"] == "Vault"  # gana el del Vault
    assert by_name["svc-app"]["displayName"] == "Servicio App"  # enriquecido de Identity
    assert by_name["otro"]["alreadyMember"] is True
    assert out[-1]["name"] == "otro"  # los ya-miembros al final


def test_merge_kind_group():
    out = merge_member_candidates(
        [{"username": "u1"}],
        [{"groupName": "G1", "directory": "AD-Corp"}],
        [],
        existing_members=set(),
        kind="group",
    )
    assert [c["name"] for c in out] == ["G1"]
    assert out[0]["kind"] == "group"
    assert out[0]["searchIn"] == "AD-Corp"


def test_granted_keys_orden_canonico():
    perms = resolve_permissions("read_only", None)
    keys = granted_keys(perms)
    assert keys == ["useAccounts", "retrieveAccounts", "listAccounts"]


# --- endpoint ------------------------------------------------------- #
class FakeClient:
    def __init__(self) -> None:
        self.added: list[dict] = []
        self.members: list[dict] = []

    async def safe_members(self, safe_id: str) -> list[dict]:
        return self.members

    async def add_safe_member(self, safe_id: str, body: dict) -> dict:
        self.added.append(body)
        return {"memberName": body["memberName"]}


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
    return {"app": app, "client": TestClient(app), "fake": fake, "op": op, "viewer": viewer}


def test_dry_run_no_escribe(env):
    r = env["client"].post(
        "/tenants/lab/safes/MiSafe/members",
        json={"changes": {"member_name": "PAM-Auditors", "member_type": "Group"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["no_op"] is False
    assert env["fake"].added == []


def test_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/safes/MiSafe/members",
        json={
            "changes": {
                "member_name": "PAM-Auditors",
                "member_type": "Group",
                "preset": "read_only",
            },
            "dry_run": False,
            "reason": "onboarding grupo auditoría OPS-1234",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].added) == 1
    sent = env["fake"].added[0]
    assert sent["memberName"] == "PAM-Auditors"
    assert sent["permissions"]["listAccounts"] is True


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/safes/MiSafe/members", json={"changes": {"member_name": "x"}}
    )
    assert r.status_code == 403


def test_preset_invalido_422(env):
    r = env["client"].post(
        "/tenants/lab/safes/MiSafe/members",
        json={"changes": {"member_name": "x", "preset": "no-existe"}},
    )
    assert r.status_code == 422


# --- member-search (endpoint, tolerante a fuente caída) ------------- #
async def test_member_search_tolera_fuente_caida(session, monkeypatch):
    import app.api.inventory as inv

    class SearchClient:
        async def list_users(self, search=None):
            return [{"username": "svc-backup", "source": "Vault"}]

        async def list_user_groups(self):
            raise RuntimeError("UserGroups caído")  # una fuente falla

        async def redrock_query(self, sql):
            return [{"Username": "svc-backup-ad", "SourceDs": "AD-Corp"}]

        async def safe_members(self, safe_id):
            return [{"memberName": "svc-backup"}]

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, SearchClient())

    monkeypatch.setattr(inv, "tenant_client", fake_tc)

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
    op = Operator(username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer)
    session.add(op)
    await session.flush()
    session.add(RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(inv.router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op

    r = TestClient(app).get(
        "/tenants/lab/member-search", params={"q": "svc-backup", "safe_id": "S"}
    )
    assert r.status_code == 200
    results = r.json()["results"]
    names = {c["name"] for c in results}
    assert "svc-backup" in names and "svc-backup-ad" in names  # vault + identity
    backup = next(c for c in results if c["name"] == "svc-backup")
    assert backup["alreadyMember"] is True
