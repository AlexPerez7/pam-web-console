"""Fase 3: safe.update — editar descripción/CPM/retención/OLAC de una safe
existente. Ver docs/specs/safe-update.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.safes import build_safe_update_plan, list_cpm_names, safe_update_body
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

SAFE = {
    "safeName": "SQL-PROD",
    "description": "cuentas de SQL productivo",
    "managingCPM": "CPM1",
    "numberOfDaysRetention": 30,
    "olacEnabled": False,
}


# --- list_cpm_names ------------------------------------------------------- #
def test_list_cpm_names_distintos_y_ordenados():
    safes = [{"managingCPM": "CPM2"}, {"managingCPM": "CPM1"}, {"managingCPM": "CPM1"}, {}]
    assert list_cpm_names(safes) == ["CPM1", "CPM2"]


def test_list_cpm_names_vacio():
    assert list_cpm_names([]) == []


# --- build_safe_update_plan ------------------------------------------------ #
def test_update_plan_sin_cambios_es_no_op():
    plan = build_safe_update_plan(SAFE, {})
    assert plan.diff == []
    assert plan.effect is None
    assert plan.confirm_phrase == "SQL-PROD"


def test_update_plan_cambiar_cpm_agrega_warning():
    plan = build_safe_update_plan(SAFE, {"managing_cpm": "CPM2"})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["CPM"] == ("CPM1", "CPM2")
    assert any("rotación de TODAS las cuentas" in w for w in plan.warnings)


def test_update_plan_cpm_igual_no_genera_diff():
    plan = build_safe_update_plan(SAFE, {"managing_cpm": "CPM1"})
    assert plan.diff == []


def test_update_plan_subir_retencion_sin_warning():
    plan = build_safe_update_plan(SAFE, {"retention_type": "days", "retention_value": 90})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["retención"] == ("30 días", "90 días")
    assert plan.warnings == []


def test_update_plan_bajar_retencion_agrega_warning():
    plan = build_safe_update_plan(SAFE, {"retention_type": "days", "retention_value": 10})
    assert any("se reduce la retención" in w for w in plan.warnings)


def test_update_plan_cambiar_tipo_retencion():
    plan = build_safe_update_plan(SAFE, {"retention_type": "versions", "retention_value": 5})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["retención"] == ("30 días", "5 versiones")


def test_update_plan_activar_olac():
    plan = build_safe_update_plan(SAFE, {"olac_enabled": True})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["OLAC"] == (False, True)
    assert any("revisar los permisos de los miembros" in w for w in plan.warnings)


def test_update_plan_desactivar_olac_agrega_warning_distinto():
    safe_with_olac = {**SAFE, "olacEnabled": True}
    plan = build_safe_update_plan(safe_with_olac, {"olac_enabled": False})
    assert any("puede no estar soportado" in w for w in plan.warnings)


def test_update_plan_descripcion():
    plan = build_safe_update_plan(SAFE, {"description": "nueva descripción"})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["descripción"] == ("cuentas de SQL productivo", "nueva descripción")


# --- safe_update_body ------------------------------------------------------ #
def test_safe_update_body_preserva_lo_no_tocado():
    body = safe_update_body(SAFE, {"description": "nueva"})
    assert body["safeName"] == "SQL-PROD"
    assert body["managingCPM"] == "CPM1"  # sin tocar -> se preserva
    assert body["numberOfDaysRetention"] == 30
    assert body["description"] == "nueva"


def test_safe_update_body_cambia_tipo_de_retencion():
    body = safe_update_body(SAFE, {"retention_type": "versions", "retention_value": 5})
    assert body["numberOfVersionsRetention"] == 5
    assert "numberOfDaysRetention" not in body


# --- endpoint --------------------------------------------------------------- #
class FakeClient:
    def __init__(self) -> None:
        self.safe: dict = dict(SAFE)
        self.updated: list[tuple[str, dict]] = []

    async def get_safe(self, safe_id: str) -> dict:
        return self.safe

    async def update_safe(self, safe_id: str, body: dict) -> dict:
        self.updated.append((safe_id, body))
        return {**self.safe, **body}


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


def test_dry_run_trae_confirm_phrase(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/SQL-PROD",
        json={"changes": {"managing_cpm": "CPM2"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["confirm_phrase"] == "SQL-PROD"
    assert env["fake"].updated == []


def test_confirm_mismatch_422(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/SQL-PROD",
        json={
            "changes": {"managing_cpm": "CPM2"},
            "dry_run": False,
            "reason": "cambio de CPM OPS-1",
            "confirm": "SQL-Prod",  # case distinto
        },
    )
    assert r.status_code == 422
    assert env["fake"].updated == []


def test_ejecucion_ok_preserva_campos_no_tocados(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/SQL-PROD",
        json={
            "changes": {"managing_cpm": "CPM2"},
            "dry_run": False,
            "reason": "cambio de CPM OPS-1",
            "confirm": "SQL-PROD",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].updated) == 1
    safe_id, sent_body = env["fake"].updated[0]
    assert safe_id == "SQL-PROD"
    assert sent_body["managingCPM"] == "CPM2"
    assert sent_body["numberOfDaysRetention"] == 30  # preservado


def test_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request(
        "PUT", "/tenants/lab/safes/SQL-PROD", json={"changes": {"managing_cpm": "CPM2"}}
    )
    assert r.status_code == 403


def test_no_op_no_llama_a_cyberark(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/safes/SQL-PROD",
        json={
            "changes": {},
            "dry_run": False,
            "reason": "sin cambios reales OPS-1",
            "confirm": "SQL-PROD",
        },
    )
    assert r.status_code == 200
    assert r.json()["no_op"] is True
    assert env["fake"].updated == []
