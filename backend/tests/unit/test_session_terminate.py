"""session.terminate — terminar una sesión PSM activa.

Ver docs/specs/session-terminate.md. Endpoints verificados contra el
módulo open-source `psPAS` (docs.cyberark.com bloqueó el fetch
automatizado).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import NotFound
from app.db import get_session
from app.domain.sessions import plan_session_terminate
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

SESSION = {
    "SessionID": "abc-123",
    "SessionGuid": "guid-456",
    "User": "jsmith",
    "RemoteMachine": "10.1.2.30",
}


def test_plan_session_terminate_effect():
    plan = plan_session_terminate(SESSION, {})
    assert "jsmith" in plan.effect
    assert "10.1.2.30" in plan.effect
    assert plan.diff == []
    assert plan.confirm_phrase is None
    # No es una acción del CPM -- el frontend no debe mostrar "pedido
    # enviado al CPM" para esto (bug real encontrado 2026-09-24, ver
    # docs/specs/done/session-terminate.md "Bug de UX").
    assert plan.is_cpm_action is False


class FakeClient:
    """El `GET /API/LiveSessions/{id}` puntual (Get Live Session) NO trae
    `SessionGuid` en su respuesta — bug real confirmado contra Globex
    (2026-09-24, segunda vuelta): el fallback al objeto de `fetch` seguía
    mandando el `SessionID` corto. El listado (Get Live Sessions) sí lo
    trae, y `apply` cae a buscarlo ahí."""

    def __init__(self) -> None:
        self.sessions = {"abc-123": {k: v for k, v in SESSION.items() if k != "SessionGuid"}}
        self.session_list = [dict(SESSION)]
        self.terminated: list[str] = []

    async def get_live_session(self, session_id: str) -> dict:
        if session_id not in self.sessions:
            raise NotFound(f"sesión '{session_id}' no existe")
        return self.sessions[session_id]

    async def live_sessions(self) -> list:
        return self.session_list

    async def terminate_session(self, session_guid: str) -> None:
        self.terminated.append(session_guid)


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


REASON = "sesión abandonada, ticket OPS-4"


def test_terminate_dry_run_no_toca_nada(env):
    r = env["client"].post("/tenants/lab/sessions/abc-123/terminate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert "jsmith" in body["effect"]
    assert env["fake"].terminated == []


def test_terminate_ejecucion_ok(env):
    """CyberArk devuelve 400 `CAWS00001E` si se manda el `SessionID`
    ("abc-123") en vez del `SessionGuid` real — confirmado contra
    Globex (2026-09-24). El GET puntual no trae `SessionGuid`; `apply`
    debe resolverlo contra el listado y mandar el guid correcto."""
    r = env["client"].post(
        "/tenants/lab/sessions/abc-123/terminate",
        json={"dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].terminated == ["guid-456"]


def test_terminate_sin_session_guid_en_ningun_lado_usa_session_id(env):
    """Si ni el GET puntual ni el listado traen `SessionGuid` (no debería
    pasar contra un tenant real, pero por las dudas), cae al `session_id`
    de la URL en vez de romper."""
    env["fake"].sessions["sin-guid"] = {"User": "x", "RemoteMachine": "y"}
    r = env["client"].post(
        "/tenants/lab/sessions/sin-guid/terminate",
        json={"dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].terminated == ["sin-guid"]


def test_terminate_sesion_inexistente_404(env):
    r = env["client"].post("/tenants/lab/sessions/no-existe/terminate", json={})
    assert r.status_code == 404


def test_terminate_rbac_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post("/tenants/lab/sessions/abc-123/terminate", json={})
    assert r.status_code == 403
