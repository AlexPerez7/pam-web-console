"""Grabaciones de sesiones PSM (listar, actividades, descargar). Ver
docs/specs/session-recordings.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api.recordings as recordings_mod
from app.api.recordings import router as recordings_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark import NotFound
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

RECORDING = {
    "SessionID": "rec-1",
    "User": "jsmith",
    "RemoteMachine": "10.1.2.30",
    "SafeName": "WinDomain",
    "Start": 1_700_000_000,
    "Duration": 120,
}


class FakeClient:
    def __init__(self) -> None:
        self.recordings = [RECORDING]
        self.activities = {"rec-1": [{"ActivityText": "logon", "ActivityType": 1}]}
        self.played: list[str] = []
        self.missing = False

    async def list_recordings(
        self, from_time: int, to_time: int, *, safe: str | None = None, search: str | None = None
    ) -> list:
        return self.recordings

    async def get_recording_activities(self, session_id: str) -> list:
        if session_id not in self.activities:
            raise NotFound(f"grabación '{session_id}' no existe")
        return self.activities[session_id]

    async def play_recording(self, session_id: str) -> bytes:
        if self.missing:
            raise NotFound(f"grabación '{session_id}' no existe")
        self.played.append(session_id)
        return b"video-bytes"


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(recordings_mod, "tenant_client", fake_tc)

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
    tenant_admin = Operator(
        username="ta", password_hash=security.hash_password("x"), role=OperatorRole.tenant_admin
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    operator_op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    session.add_all([tenant_admin, viewer, operator_op])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="lab", role=OperatorRole.tenant_admin
            ),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(recordings_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "tenant_admin": tenant_admin,
        "viewer": viewer,
        "operator_op": operator_op,
        "session": session,
    }


REASON = "investigar incidente reportado por el cliente"


def test_list_recordings_ok(env):
    r = env["client"].get("/tenants/lab/recordings")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["recordings"][0]["SessionID"] == "rec-1"


def test_recording_activities_ok(env):
    r = env["client"].get("/tenants/lab/recordings/rec-1/activities")
    assert r.status_code == 200
    assert r.json()["activities"][0]["ActivityText"] == "logon"


def test_recording_activities_inexistente_404(env):
    r = env["client"].get("/tenants/lab/recordings/no-existe/activities")
    assert r.status_code == 404


def test_play_ok(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post("/tenants/lab/recordings/rec-1/play", json={"reason": REASON})
    assert r.status_code == 200
    assert r.content == b"video-bytes"
    assert env["fake"].played == ["rec-1"]


def test_play_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post("/tenants/lab/recordings/rec-1/play", json={"reason": REASON})
    assert r.status_code == 403
    assert env["fake"].played == []


def test_play_sin_reason(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post("/tenants/lab/recordings/rec-1/play", json={"reason": "corta"})
    assert r.status_code == 422
    assert env["fake"].played == []


def test_play_grabacion_inexistente(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    env["fake"].missing = True
    r = env["client"].post("/tenants/lab/recordings/rec-1/play", json={"reason": REASON})
    assert r.status_code == 404


async def test_play_audita_sin_contenido(env, session):
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    env["client"].post("/tenants/lab/recordings/rec-1/play", json={"reason": REASON})
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "recording.play")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].result == "ok"
    assert "video-bytes" not in str(rows[0].detail)


def test_play_mp4_ok(env, monkeypatch):
    monkeypatch.setattr(recordings_mod.recording_transcode, "to_mp4", lambda avi: b"mp4-bytes")
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post("/tenants/lab/recordings/rec-1/play/mp4", json={"reason": REASON})
    assert r.status_code == 200
    assert r.content == b"mp4-bytes"
    assert r.headers["content-type"] == "video/mp4"
    assert env["fake"].played == ["rec-1"]


def test_play_mp4_transcode_error(env, monkeypatch):
    def _fail(avi: bytes) -> bytes:
        raise recordings_mod.recording_transcode.TranscodeError("scpr_decode fallo")

    monkeypatch.setattr(recordings_mod.recording_transcode, "to_mp4", _fail)
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post("/tenants/lab/recordings/rec-1/play/mp4", json={"reason": REASON})
    assert r.status_code == 502
