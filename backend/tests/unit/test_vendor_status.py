"""Estado de status.cyberark.com (app/vendor_status.py + app/api/vendor_status.py).

Recurso global, sin tenant_id, sólo exige autenticación (sin RBAC
adicional — es información pública del fabricante).
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import vendor_status as vendor_status_mod
from app.api.vendor_status import router as vendor_status_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import AuditLog, Operator, OperatorRole
from app.vendor_status import VendorStatusError, fetch_summary

# --- app.vendor_status.fetch_summary (pura, vía MockTransport) ----------- #


def _fake_transport(*, fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("no se pudo conectar", request=request)
        path = request.url.path
        if path == "/api/v1/status":
            return httpx.Response(
                200,
                json={
                    "page": {
                        "state": "operational",
                        "state_text": "All systems are go!",
                        "url": "https://status.cyberark.com",
                        "updated_at": "2026-09-30T12:24:25.485Z",
                    }
                },
            )
        if path == "/api/v1/components":
            return httpx.Response(
                200,
                json={
                    "components": [
                        {
                            "id": 2,
                            "name": "Identity Cloud",
                            "state": "operational",
                            "url": None,
                            "position": 2,
                        },
                        {
                            "id": 1,
                            "name": "Privilege Cloud",
                            "state": "degraded_performance",
                            "url": "https://status.cyberark.com/services/privilege-cloud",
                            "position": 1,
                        },
                    ]
                },
            )
        if path == "/api/v1/notices":
            timeline = request.url.params.get("filter[timeline_state_eq]")
            if timeline == "current":
                return httpx.Response(
                    200,
                    json={
                        "notices": [
                            {
                                "id": 100,
                                "type": "incident",
                                "state": "investigating",
                                "timeline_state": "current",
                                "subject": "Privilege Cloud degradado",
                                "url": "https://status.cyberark.com/notices/x",
                                "begins_at": None,
                                "ends_at": None,
                                "began_at": "2026-09-30T10:00:00.000Z",
                                "ended_at": None,
                                "updated_at": "2026-09-30T10:05:00.000Z",
                                "latest_update": {"content": "Investigando."},
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"notices": []})
        raise AssertionError(f"ruta inesperada: {path}")

    return httpx.MockTransport(handler)


async def test_fetch_summary_arma_resumen_ordenado_por_position():
    data = await fetch_summary(transport=_fake_transport())
    assert data["page_state"] == "operational"
    assert [c["id"] for c in data["components"]] == [1, 2]  # ordenado por position
    assert data["active_notices"][0]["subject"] == "Privilege Cloud degradado"
    assert data["active_notices"][0]["latest_update"] == "Investigando."
    assert data["upcoming_notices"] == []


async def test_fetch_summary_error_de_red_levanta_vendor_status_error():
    try:
        await fetch_summary(transport=_fake_transport(fail=True))
        raise AssertionError("debía levantar VendorStatusError")
    except VendorStatusError:
        pass


# --- endpoint GET /vendor-status/cyberark --------------------------------- #

_SUMMARY = {
    "page_state": "operational",
    "page_state_text": "All systems are go!",
    "page_url": "https://status.cyberark.com",
    "updated_at": "2026-09-30T12:24:25.485Z",
    "components": [{"id": 1, "name": "Privilege Cloud", "state": "operational", "url": None}],
    "active_notices": [],
    "upcoming_notices": [],
}


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add(op)
    await session.flush()

    app = FastAPI()
    app.include_router(vendor_status_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op

    cache_store: dict[str, dict] = {}

    async def fake_get_json(key: str):
        return cache_store.get(key)

    async def fake_set_json(key: str, value: dict, ttl: int) -> None:
        cache_store[key] = value

    monkeypatch.setattr(vendor_status_mod.cache, "get_json", fake_get_json)
    monkeypatch.setattr(vendor_status_mod.cache, "set_json", fake_set_json)

    return {"app": app, "client": TestClient(app), "op": op, "cache_store": cache_store}


def test_cache_miss_llama_fetch_y_cachea(env, monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(transport=None):
        calls["n"] += 1
        return dict(_SUMMARY)

    monkeypatch.setattr(vendor_status_mod, "fetch_summary", fake_fetch)

    r = env["client"].get("/vendor-status/cyberark")
    assert r.status_code == 200
    assert r.json()["page_state"] == "operational"
    assert r.json()["cached"] is False
    assert calls["n"] == 1
    assert vendor_status_mod._CACHE_KEY in env["cache_store"]


def test_cache_hit_no_llama_fetch(env, monkeypatch):
    env["cache_store"][vendor_status_mod._CACHE_KEY] = dict(_SUMMARY, cached=False)

    async def fake_fetch(transport=None):
        raise AssertionError("no debía llamar a fetch_summary en cache hit")

    monkeypatch.setattr(vendor_status_mod, "fetch_summary", fake_fetch)

    r = env["client"].get("/vendor-status/cyberark")
    assert r.status_code == 200
    assert r.json()["cached"] is True


def test_refresh_ignora_cache(env, monkeypatch):
    env["cache_store"][vendor_status_mod._CACHE_KEY] = dict(_SUMMARY, cached=False)
    calls = {"n": 0}

    async def fake_fetch(transport=None):
        calls["n"] += 1
        return dict(_SUMMARY)

    monkeypatch.setattr(vendor_status_mod, "fetch_summary", fake_fetch)

    r = env["client"].get("/vendor-status/cyberark?refresh=true")
    assert r.status_code == 200
    assert calls["n"] == 1


def test_error_upstream_devuelve_503_y_audita(env, monkeypatch):
    async def fake_fetch(transport=None):
        raise VendorStatusError("no se pudo consultar status.cyberark.com: boom")

    monkeypatch.setattr(vendor_status_mod, "fetch_summary", fake_fetch)

    r = env["client"].get("/vendor-status/cyberark")
    assert r.status_code == 503


async def test_audita_siempre_ok_y_error(env, monkeypatch, session):
    async def fake_fetch(transport=None):
        return dict(_SUMMARY)

    monkeypatch.setattr(vendor_status_mod, "fetch_summary", fake_fetch)
    env["client"].get("/vendor-status/cyberark")

    rows = (await session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "vendor_status.view"
    assert rows[0].result == "ok"
    assert rows[0].operator == "op"
    assert rows[0].tenant_id is None


def test_cualquier_operador_autenticado_puede_ver_sin_rbac_extra(env, monkeypatch):
    """Es información pública del fabricante — no requiere RoleAssignment en
    ningún tenant, a diferencia de knowledge-base (require_any_tenant_role)."""

    async def fake_fetch(transport=None):
        return dict(_SUMMARY)

    monkeypatch.setattr(vendor_status_mod, "fetch_summary", fake_fetch)
    r = env["client"].get("/vendor-status/cyberark")
    assert r.status_code == 200
