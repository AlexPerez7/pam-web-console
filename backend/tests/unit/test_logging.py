"""Middleware de request-id + integracion con el audit."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import audit
from app.core.context import current_request_id, request_id_var
from app.main import request_context


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(request_context)

    @app.get("/echo")
    def echo() -> dict:
        return {"rid": current_request_id()}

    return app


def test_genera_y_devuelve_request_id():
    with TestClient(_app()) as c:
        r = c.get("/echo")
    assert r.status_code == 200
    rid = r.json()["rid"]
    assert rid and rid != "-"
    assert r.headers["X-Request-ID"] == rid


def test_respeta_x_request_id_entrante():
    with TestClient(_app()) as c:
        r = c.get("/echo", headers={"X-Request-ID": "abc123"})
    assert r.json()["rid"] == "abc123"
    assert r.headers["X-Request-ID"] == "abc123"


def test_descarta_x_request_id_invalido():
    with TestClient(_app()) as c:
        for malo in ["a" * 65, "id con espacios", "<script>", "a;b=c"]:
            r = c.get("/echo", headers={"X-Request-ID": malo})
            assert r.headers["X-Request-ID"] != malo
            assert len(r.headers["X-Request-ID"]) == 16


def test_context_se_limpia_entre_requests():
    with TestClient(_app()) as c:
        c.get("/echo")
    assert current_request_id() is None


async def test_audit_toma_request_id_del_contexto(session):
    token = request_id_var.set("req-xyz")
    try:
        row = await audit.record(
            session, operator="alex", role="viewer", action="account.read", result="ok"
        )
    finally:
        request_id_var.reset(token)
    assert row.request_id == "req-xyz"


async def test_audit_request_id_explicito_gana(session):
    token = request_id_var.set("del-contexto")
    try:
        row = await audit.record(
            session,
            operator="alex",
            role="viewer",
            action="account.read",
            result="ok",
            request_id="explicito",
        )
    finally:
        request_id_var.reset(token)
    assert row.request_id == "explicito"
