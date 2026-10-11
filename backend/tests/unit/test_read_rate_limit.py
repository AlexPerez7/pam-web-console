"""Rate-limit general de lecturas (GET) por operador -- middleware
`read_rate_limit_middleware` + helpers puros de
`app.core.read_rate_limit`. Ver docs/specs/done/api-read-rate-limit.md."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core import cache, read_rate_limit, security
from app.core.deps import current_operator
from app.db import get_session
from app.main import read_rate_limit_middleware
from app.models import Operator


# --- helpers puros -------------------------------------------------------- #
def test_tenant_id_from_path():
    assert read_rate_limit.tenant_id_from_path("/tenants/globex/accounts") == "globex"
    assert read_rate_limit.tenant_id_from_path("/tenants/globex") == "globex"
    assert read_rate_limit.tenant_id_from_path("/search/accounts") is None
    assert read_rate_limit.tenant_id_from_path("/") is None


def test_is_exempt():
    assert read_rate_limit.is_exempt("POST", "/tenants/globex/accounts") is True
    assert read_rate_limit.is_exempt("GET", "/auth/me") is True
    assert read_rate_limit.is_exempt("GET", "/health") is True
    assert read_rate_limit.is_exempt("GET", "/tenants/globex/accounts") is False


# --- middleware end-to-end ------------------------------------------------ #
@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> dict:
    store: dict[str, int] = {}

    async def incr_with_ttl(key: str, ttl: int) -> int:
        store[key] = store.get(key, 0) + 1
        return store[key]

    monkeypatch.setattr(cache, "incr_with_ttl", incr_with_ttl)
    return store


@pytest_asyncio.fixture
async def ctx(session, fake_cache):
    op = Operator(
        username="alex",
        password_hash=security.hash_password("una-contrasena-larga"),
        totp_enrolled=True,
        is_active=True,
    )
    session.add(op)
    await session.flush()

    app = FastAPI()
    app.middleware("http")(read_rate_limit_middleware)

    @app.get("/tenants/{tenant_id}/ping")
    async def tenant_ping(tenant_id: str, _op: Operator = Depends(current_operator)) -> dict:
        return {"ok": True}

    @app.post("/tenants/{tenant_id}/ping")
    async def tenant_ping_write(tenant_id: str, _op: Operator = Depends(current_operator)) -> dict:
        return {"ok": True}

    @app.get("/search/ping")
    async def cross_tenant_ping(_op: Operator = Depends(current_operator)) -> dict:
        return {"ok": True}

    @app.get("/auth/me")
    async def auth_ping(_op: Operator = Depends(current_operator)) -> dict:
        return {"ok": True}

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    token = security.make_token(op.id, op.token_version)
    return {"client": TestClient(app), "token": token, "store": fake_cache}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_limite_por_tenant_se_dispara(ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_rate_max_per_window", 3)
    c, token = ctx["client"], ctx["token"]
    for _ in range(3):
        assert c.get("/tenants/globex/ping", headers=_auth(token)).status_code == 200
    r = c.get("/tenants/globex/ping", headers=_auth(token))
    assert r.status_code == 429
    assert r.headers.get("Retry-After")


def test_limite_es_independiente_por_tenant(ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_rate_max_per_window", 2)
    c, token = ctx["client"], ctx["token"]
    for _ in range(2):
        assert c.get("/tenants/globex/ping", headers=_auth(token)).status_code == 200
    assert c.get("/tenants/globex/ping", headers=_auth(token)).status_code == 429
    # otro tenant: contador propio, no afectado por el de globex
    assert c.get("/tenants/acme/ping", headers=_auth(token)).status_code == 200


def test_limite_global_cross_tenant_se_dispara(ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_rate_global_max_per_window", 2)
    c, token = ctx["client"], ctx["token"]
    for _ in range(2):
        assert c.get("/search/ping", headers=_auth(token)).status_code == 200
    assert c.get("/search/ping", headers=_auth(token)).status_code == 429


def test_escrituras_no_cuentan(ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_rate_max_per_window", 2)
    c, token = ctx["client"], ctx["token"]
    for _ in range(5):
        assert c.post("/tenants/globex/ping", headers=_auth(token)).status_code == 200
    assert ctx["store"] == {}


def test_auth_exento(ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_rate_global_max_per_window", 1)
    c, token = ctx["client"], ctx["token"]
    for _ in range(5):
        assert c.get("/auth/me", headers=_auth(token)).status_code == 200
    assert ctx["store"] == {}


def test_token_invalido_no_rompe_el_middleware(ctx):
    c = ctx["client"]
    r = c.get("/tenants/globex/ping", headers=_auth("token-basura"))
    assert r.status_code == 401  # lo rechaza current_operator, no un 500
    r = c.get("/tenants/globex/ping")  # sin Authorization
    assert r.status_code == 401


def test_degrada_abierto_si_redis_no_responde(ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_rate_max_per_window", 1)

    async def incr_none(key: str, ttl: int) -> None:
        return None

    monkeypatch.setattr(cache, "incr_with_ttl", incr_none)
    c, token = ctx["client"], ctx["token"]
    for _ in range(5):
        assert c.get("/tenants/globex/ping", headers=_auth(token)).status_code == 200
