"""System Health (cross-tenant y por tenant). Ver docs/specs/done/system-health.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import system_health as system_health_mod
from app.api.system_health import _compute_snapshot, _friendly_error
from app.api.system_health import router as system_health_router
from app.core import security
from app.core.deps import current_operator
from app.core.secrets import SecretStoreError
from app.cyberark.auth import AuthError
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


class FakeClient:
    def __init__(
        self,
        *,
        accounts=None,
        sessions=None,
        components=None,
        monitoring=None,
        fail: Exception | None = None,
        sessions_error: Exception | None = None,
    ) -> None:
        self.accounts = accounts if accounts is not None else [{"id": "1"}, {"id": "2"}]
        self.sessions = sessions if sessions is not None else [{"id": "s1"}]
        self.components = components or {}
        self.monitoring = monitoring
        self.fail = fail
        self.sessions_error = sessions_error
        self.closed = False
        self.list_accounts_calls = 0

    async def authenticate(self) -> None:
        if self.fail:
            raise self.fail

    async def close(self) -> None:
        self.closed = True

    async def list_accounts(self, **kwargs) -> list:
        self.list_accounts_calls += 1
        return self.accounts

    async def live_sessions(self) -> list:
        if self.sessions_error:
            raise self.sessions_error
        return self.sessions

    async def component_details(self, component_id: str):
        return self.components.get(component_id)

    async def components_monitoring_summary(self):
        return self.monitoring


async def test_compute_snapshot_forma_general():
    fake = FakeClient(
        accounts=[{"id": "1"}, {"id": "2"}, {"id": "3"}],
        sessions=[{"id": "s1"}],
        components={
            "PVWA": [{"IsLoggedOn": True}, {"IsLoggedOn": True}],
            "CPM": [{"IsLoggedOn": True}],
            "SessionManagement": None,
            "AIM": [{"IsLoggedOn": False}],
        },
        monitoring={"Vaults": [{"Role": "Primary", "IP": "10.0.0.1", "IsLoggedOn": True}]},
    )
    data = await _compute_snapshot(fake)
    assert data["accounts_total"] == 3
    assert data["sessions_active"] == 1
    assert data["vault"] == {"available": True, "connected": True, "ip": "10.0.0.1"}
    pvwa = next(c for c in data["components"] if c["id"] == "PVWA")
    assert len(pvwa["instances"]) == 2 and pvwa["instances"][0]["connected"] is True
    psm = next(c for c in data["components"] if c["id"] == "SessionManagement")
    assert psm["instances"] == []
    by_id = {c["id"]: c for c in data["components"]}
    assert {k: v for k, v in by_id["PVWA"].items() if k != "instances"} == {
        "id": "PVWA",
        "name": "Web Portal",
        "available": True,
        "connected": 2,
        "total": 2,
    }
    assert by_id["SessionManagement"]["available"] is False


async def test_compute_snapshot_vault_sin_datos():
    fake = FakeClient(monitoring=None)
    data = await _compute_snapshot(fake)
    assert data["vault"] == {"available": False}


async def test_compute_snapshot_vault_ignora_entradas_dr():
    fake = FakeClient(
        monitoring={
            "Vaults": [
                {"Role": "DR", "IP": "10.0.0.2", "IsLoggedOn": True},
                {"Role": "Primary", "IP": "10.0.0.1", "IsLoggedOn": False},
            ]
        }
    )
    data = await _compute_snapshot(fake)
    assert data["vault"] == {"available": True, "connected": False, "ip": "10.0.0.1"}


async def test_compute_snapshot_una_lectura_caida_degrada_sola_no_tira_todo():
    """Caso real (Acme, 2026-09-17): GET /API/LiveSessions -> 404 no
    debe tapar el resto del snapshot del tenant."""
    fake = FakeClient(
        accounts=[{"id": "1"}, {"id": "2"}, {"id": "3"}],
        sessions_error=RuntimeError("GET /API/LiveSessions -> 404"),
        components={"PVWA": [{"IsLoggedOn": True}]},
        monitoring={"Vaults": [{"Role": "Primary", "IP": "10.0.0.1", "IsLoggedOn": True}]},
    )
    data = await _compute_snapshot(fake)
    assert data["sessions_active"] is None
    assert data["accounts_total"] == 3
    assert data["vault"]["available"] is True


def test_friendly_error_credenciales_invalidas():
    """Caso real (Umbrella, 2026-09-17): la Identity de CyberArk rechaza el
    client_id/secret del service account — el operador necesita un mensaje
    accionable, no el JSON crudo de la respuesta."""
    e = AuthError(
        'ISPSS auth falló (400): {"error":"access_denied",'
        '"error_description":"invalid client creds or client not allowed"}'
    )
    assert _friendly_error(e) == "credenciales inválidas o rechazadas por Identity"


def test_friendly_error_secret_store():
    msg = _friendly_error(SecretStoreError("secret_ref 'x' no está"))
    assert "credenciales" in msg
    assert "Traceback" not in msg


def test_friendly_error_excepcion_inesperada_no_expone_el_stacktrace():
    msg = _friendly_error(AttributeError("'NoneType' object has no attribute 'get'"))
    assert "NoneType" not in msg
    assert "AttributeError" in msg


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    tenant1 = FakeClient()
    tenant2 = FakeClient(accounts=[{"id": "1"}], sessions=[], components={}, monitoring=None)

    clients = {"t1": tenant1, "t2": tenant2}

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, clients[tenant_id])

    monkeypatch.setattr(system_health_mod, "tenant_client", fake_tc)

    cache_store: dict[str, dict] = {}

    async def get_json(key: str):
        return cache_store.get(key)

    async def set_json(key: str, value: dict, ttl: int) -> None:
        cache_store[key] = value

    monkeypatch.setattr(system_health_mod.cache, "get_json", get_json)
    monkeypatch.setattr(system_health_mod.cache, "set_json", set_json)

    # el fan-out cross-tenant no usa tenant_client (aislamiento por diseño,
    # ver el docstring del módulo) — se mockean get_secret/build_client.
    def fake_get_secret(ref: str):
        return {}

    def fake_build_client(tenant, secret, **kwargs):
        return clients[tenant.id]

    monkeypatch.setattr(system_health_mod, "get_secret", fake_get_secret)
    monkeypatch.setattr(system_health_mod, "build_client", fake_build_client)

    session.add_all(
        [
            Tenant(
                id="t1",
                name="Tenant Uno",
                client="A",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://x",
                secret_ref="t1",
            ),
            Tenant(
                id="t2",
                name="Tenant Dos",
                client="B",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://y",
                secret_ref="t2",
            ),
        ]
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add(viewer)
    await session.flush()
    # el viewer sólo tiene acceso a t1 — t2 debe quedar afuera del fan-out
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="t1", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(system_health_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {
        "app": app,
        "client": TestClient(app),
        "clients": clients,
        "cache_store": cache_store,
    }


def test_tenant_system_health_ok(env):
    r = env["client"].get("/tenants/t1/system-health")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"] == "t1"
    assert body["accounts_total"] == 2
    assert body["cached"] is False


def test_tenant_system_health_cache_hit_no_vuelve_a_pegarle_al_cliente(env):
    env["client"].get("/tenants/t1/system-health")
    assert env["clients"]["t1"].list_accounts_calls == 1
    r = env["client"].get("/tenants/t1/system-health")
    assert r.status_code == 200
    assert r.json()["cached"] is True
    assert env["clients"]["t1"].list_accounts_calls == 1


def test_system_health_fanout_filtra_por_rbac(env):
    r = env["client"].get("/system-health")
    assert r.status_code == 200
    body = r.json()
    tenant_ids = {row["tenant"] for row in body["tenants"]}
    assert tenant_ids == {"t1"}
    assert body["errors"] == []


def test_system_health_fanout_un_tenant_caido_no_rompe_los_demas(session, monkeypatch):
    ok_client = FakeClient()
    bad_client = FakeClient(fail=RuntimeError("tenant caído"))
    clients = {"ok": ok_client, "bad": bad_client}

    async def get_json(key: str):
        return None

    async def set_json(key: str, value: dict, ttl: int) -> None:
        return None

    monkeypatch.setattr(system_health_mod.cache, "get_json", get_json)
    monkeypatch.setattr(system_health_mod.cache, "set_json", set_json)

    def fake_get_secret(ref: str):
        return {}

    def fake_build_client(tenant, secret, **kwargs):
        return clients[tenant.id]

    monkeypatch.setattr(system_health_mod, "get_secret", fake_get_secret)
    monkeypatch.setattr(system_health_mod, "build_client", fake_build_client)

    session.add_all(
        [
            Tenant(
                id="ok",
                name="OK",
                client="A",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://x",
                secret_ref="ok",
            ),
            Tenant(
                id="bad",
                name="Bad",
                client="B",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://y",
                secret_ref="bad",
            ),
        ]
    )

    # platform_admin ve ambos tenants sin necesidad de RoleAssignment
    admin = Operator(
        username="a", password_hash=security.hash_password("x"), role=OperatorRole.platform_admin
    )
    session.add(admin)

    app = FastAPI()
    app.include_router(system_health_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: admin
    client = TestClient(app)

    r = client.get("/system-health")
    assert r.status_code == 200
    body = r.json()
    assert {row["tenant"] for row in body["tenants"]} == {"ok"}
    assert {e["tenant"] for e in body["errors"]} == {"bad"}
    assert "RuntimeError" in body["errors"][0]["error"]
