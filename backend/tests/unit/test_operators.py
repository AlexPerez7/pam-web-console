"""PATCH /operators/{id}: editar username/display_name/email de un
operador (antes sólo se podía cambiar rol y estado)."""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.operators import router as operators_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import Operator, OperatorRole


@pytest_asyncio.fixture
async def env(session):
    admin = Operator(
        username="admin",
        password_hash=security.hash_password("x"),
        role=OperatorRole.platform_admin,
    )
    other = Operator(
        username="alice", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    taken = Operator(
        username="bob", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([admin, other, taken])
    await session.flush()

    app = FastAPI()
    app.include_router(operators_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: admin
    return {
        "client": TestClient(app),
        "admin": admin,
        "other": other,
        "taken": taken,
        "session": session,
    }


def test_patch_username_ok(env):
    r = env["client"].patch(f"/operators/{env['other'].id}", json={"username": "alice2"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice2"


def test_patch_username_duplicado_409(env):
    r = env["client"].patch(f"/operators/{env['other'].id}", json={"username": "bob"})
    assert r.status_code == 409


def test_patch_username_vacio_400(env):
    r = env["client"].patch(f"/operators/{env['other'].id}", json={"username": "   "})
    assert r.status_code == 400


def test_patch_username_sin_cambios_no_rompe(env):
    r = env["client"].patch(f"/operators/{env['other'].id}", json={"username": "alice"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_patch_display_name_y_email(env):
    r = env["client"].patch(
        f"/operators/{env['other'].id}",
        json={"display_name": "Alice Smith", "email": "alice@example.com"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Alice Smith"
    assert body["email"] == "alice@example.com"
    assert body["username"] == "alice"  # sin tocar


async def test_patch_username_auditado(env, session):
    from sqlalchemy import select

    from app.models import AuditLog

    env["client"].patch(f"/operators/{env['other'].id}", json={"username": "alice2"})
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "operator.update")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].detail["username"] == {"from": "alice", "to": "alice2"}
