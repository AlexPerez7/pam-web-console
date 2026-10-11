"""Aprobación de un segundo operador (principio 4) sobre una op de Fase 3
(`safe.update`). platform_admin ejecuta directo; el resto deja una solicitud
que aprueba otro operador re-enviando el request con X-PAM-Approval-Id.
Ver docs/specs/dual-approval.md."""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.approvals import router as approvals_router
from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.models import (
    ApprovalRequest,
    AuditLog,
    Deployment,
    Operator,
    OperatorRole,
    RoleAssignment,
    Tenant,
)

SAFE = {
    "safeName": "SQL-PROD",
    "description": "cuentas de SQL productivo",
    "managingCPM": "CPM1",
    "numberOfDaysRetention": 30,
    "olacEnabled": False,
}
PATH = "/tenants/lab/safes/SQL-PROD"
CHANGES = {"managing_cpm": "CPM2"}


class FakeClient:
    def __init__(self) -> None:
        self.safe: dict = dict(SAFE)
        self.updated: list[dict] = []

    async def get_safe(self, safe_id: str) -> dict:
        return self.safe

    async def update_safe(self, safe_id: str, body: dict) -> dict:
        self.updated.append(body)
        return {**self.safe, **body}


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    monkeypatch.setenv("PAM_DUAL_APPROVAL_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
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
    ops = {}
    for name, role in [
        ("ap1", OperatorRole.viewer),
        ("ap2", OperatorRole.viewer),
        ("op", OperatorRole.viewer),
        ("pa", OperatorRole.platform_admin),
    ]:
        ops[name] = Operator(username=name, password_hash=security.hash_password("x"), role=role)
    session.add_all(ops.values())
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=ops["ap1"].id, tenant_id="lab", role=OperatorRole.approver),
            RoleAssignment(operator_id=ops["ap2"].id, tenant_id="lab", role=OperatorRole.approver),
            RoleAssignment(operator_id=ops["op"].id, tenant_id="lab", role=OperatorRole.operator),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)
    app.include_router(approvals_router)
    current = {"op": ops["ap1"]}

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: current["op"]

    def as_(name: str) -> TestClient:
        current["op"] = ops[name]
        return TestClient(app)

    return {"as": as_, "fake": fake, "session": session}


def _execute(c: TestClient, approval_id: str | None = None, reason: str = "cambio de CPM OPS-1"):
    headers = {"X-PAM-Approval-Id": approval_id} if approval_id else {}
    return c.request(
        "PUT",
        PATH,
        headers=headers,
        json={"changes": CHANGES, "dry_run": False, "reason": reason, "confirm": "SQL-PROD"},
    )


def _request_approval(env) -> str:
    r = _execute(env["as"]("ap1"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_approval"] is True
    return body["approval_id"]


def test_dry_run_avisa_que_requiere_aprobacion(env):
    r = env["as"]("ap1").request("PUT", PATH, json={"changes": CHANGES})
    assert r.json()["requires_approval"] is True
    r = env["as"]("pa").request("PUT", PATH, json={"changes": CHANGES})
    assert r.json()["requires_approval"] is False


def test_approver_deja_solicitud_y_no_toca_cyberark(env):
    approval_id = _request_approval(env)
    assert env["fake"].updated == []
    r = env["as"]("ap2").get("/approvals")
    items = r.json()["approvals"]
    assert [a["id"] for a in items] == [approval_id]
    assert items[0]["can_approve"] is True
    assert items[0]["body"]["changes"] == CHANGES
    assert "reason" not in items[0]["body"] and "confirm" not in items[0]["body"]
    assert env["as"]("ap2").get("/approvals/count").json() == {"pending": 1}
    # el solicitante la ve pero no la puede aprobar
    mine = env["as"]("ap1").get("/approvals").json()["approvals"][0]
    assert mine["is_mine"] is True and mine["can_approve"] is False


def test_platform_admin_ejecuta_directo(env):
    r = _execute(env["as"]("pa"))
    assert r.status_code == 200
    assert "pending_approval" not in r.json()
    assert len(env["fake"].updated) == 1


async def test_otro_approver_aprueba_y_se_ejecuta(env):
    approval_id = _request_approval(env)
    c = env["as"]("ap2")
    preview = c.request(
        "PUT", PATH, headers={"X-PAM-Approval-Id": approval_id}, json={"changes": CHANGES}
    )
    assert preview.json()["approval_matches"] is True
    assert preview.json()["requires_approval"] is False
    r = _execute(c, approval_id, reason="revisado y aprobado OPS-1")
    assert r.status_code == 200, r.text
    assert len(env["fake"].updated) == 1
    row = await env["session"].get(ApprovalRequest, approval_id)
    assert row.status == "approved" and row.decided_by == "ap2"
    audits = (await env["session"].execute(select(AuditLog))).scalars().all()
    final = [a for a in audits if a.action == "safe.update" and a.result == "ok" and not a.dry_run]
    assert final[-1].operator == "ap2"
    assert final[-1].detail["requested_by"] == "ap1"
    # no se puede aprobar dos veces
    assert _execute(c, approval_id).status_code == 409


def test_no_puede_aprobar_su_propia_solicitud(env):
    approval_id = _request_approval(env)
    r = _execute(env["as"]("ap1"), approval_id)
    assert r.status_code == 403
    assert env["fake"].updated == []


def test_platform_admin_puede_aprobar(env):
    approval_id = _request_approval(env)
    r = _execute(env["as"]("pa"), approval_id)
    assert r.status_code == 200
    assert len(env["fake"].updated) == 1


def test_changes_distintos_no_aprueban(env):
    approval_id = _request_approval(env)
    r = env["as"]("ap2").request(
        "PUT",
        PATH,
        headers={"X-PAM-Approval-Id": approval_id},
        json={
            "changes": {"managing_cpm": "OTRO"},
            "dry_run": False,
            "reason": "cambio de CPM OPS-1",
            "confirm": "SQL-PROD",
        },
    )
    assert r.status_code == 409
    assert env["fake"].updated == []


async def test_si_el_objeto_cambio_la_solicitud_queda_stale(env):
    approval_id = _request_approval(env)
    env["fake"].safe["managingCPM"] = "CPM3"  # alguien lo cambió en el medio
    r = _execute(env["as"]("ap2"), approval_id)
    assert r.status_code == 409
    assert env["fake"].updated == []
    row = await env["session"].get(ApprovalRequest, approval_id)
    assert row.status == "stale"


async def test_solicitud_vencida_no_se_aprueba(env):
    approval_id = _request_approval(env)
    row = await env["session"].get(ApprovalRequest, approval_id)
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await env["session"].commit()
    r = _execute(env["as"]("ap2"), approval_id)
    assert r.status_code == 409
    assert "expired" in r.json()["detail"]


def test_rechazar_exige_motivo_y_otro_operador(env):
    approval_id = _request_approval(env)
    assert (
        env["as"]("ap1")
        .post(f"/approvals/{approval_id}/reject", json={"reason": "no corresponde"})
        .status_code
        == 403
    )
    c = env["as"]("ap2")
    assert c.post(f"/approvals/{approval_id}/reject", json={"reason": "no"}).status_code == 422
    r = c.post(f"/approvals/{approval_id}/reject", json={"reason": "no corresponde el CPM"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _execute(c, approval_id).status_code == 409


def test_cancelar_solo_el_solicitante(env):
    approval_id = _request_approval(env)
    assert env["as"]("ap2").post(f"/approvals/{approval_id}/cancel").status_code == 403
    r = env["as"]("ap1").post(f"/approvals/{approval_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_operator_sin_rol_approver_no_ve_la_solicitud(env):
    approval_id = _request_approval(env)
    c = env["as"]("op")
    assert c.get("/approvals").json()["approvals"] == []
    assert c.get(f"/approvals/{approval_id}").status_code == 404
