"""GET /tenants/{id}/accounts?search=… — la barra de búsqueda también
encuentra cuentas de dominio por sus remote machines (CyberArk no las busca)
y acepta una lista de términos separados por coma (pegada de un ticket)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import inventory as inventory_mod
from app.api.inventory import router as inventory_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.domain.accounts import (
    account_matches_remote_machines,
    account_matches_terms,
    account_remote_machines,
    parse_search_terms,
)
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def _acc(id_, name, address=None, machines=None, user="svc"):
    acc = {
        "id": id_,
        "name": name,
        "userName": user,
        "address": address,
        "safeName": "SAFE-A",
        "platformId": "WinDomain",
    }
    if machines is not None:
        acc["remoteMachinesAccess"] = {
            "remoteMachines": machines,
            "accessRestrictedToRemoteMachines": True,
        }
    return acc


ACCOUNTS = [
    _acc("1", "dom-a", machines="srv-xrail02;otro-host.local"),
    _acc("2", "dom-b", machines="203.0.113.11"),
    _acc("3", "dom-c", machines="203.0.113.110"),  # prefijo del anterior: no debe confundirse
    _acc("4", "local-d", address="203.0.113.13"),  # coincide por address, no por remote machines
    _acc("5", "dom-e", machines="vcenter.globex.local"),
    _acc("6", "sin-nada", address="10.9.9.9"),
]


# --- dominio ------------------------------------------------------------------ #


def test_parse_search_terms():
    assert parse_search_terms("a, b;c\nd") == ["a", "b", "c", "d"]
    assert parse_search_terms("  x  ") == ["x"]
    assert parse_search_terms("A,a, A ") == ["A"]  # dedup sin distinguir mayúsculas
    assert parse_search_terms("svc db") == ["svc db"]  # el espacio NO separa
    assert parse_search_terms("") == []
    assert parse_search_terms(None) == []


def test_account_remote_machines():
    assert account_remote_machines(ACCOUNTS[0]) == ["srv-xrail02", "otro-host.local"]
    assert account_remote_machines(ACCOUNTS[5]) == []
    assert account_remote_machines({"remoteMachinesAccess": None}) == []


def test_remote_machines_exacto_vs_parcial():
    acc = ACCOUNTS[2]  # 203.0.113.110
    assert account_matches_remote_machines(acc, ["203.0.113.11"], exact=False) is True
    assert account_matches_remote_machines(acc, ["203.0.113.11"], exact=True) is False
    assert account_matches_remote_machines(acc, ["203.0.113.110"], exact=True) is True


def test_account_matches_terms_include_remote():
    acc = ACCOUNTS[1]
    assert account_matches_terms(acc, ["203.0.113.11"], exact=False) is True
    assert account_matches_terms(acc, ["203.0.113.11"], exact=False, include_remote=False) is False


# --- endpoint ----------------------------------------------------------------- #


class FakeClient:
    """La búsqueda de CyberArk: mira nombre/usuario/address, NO las remote
    machines (ese es el hueco que cubre esta feature)."""

    def __init__(self) -> None:
        self.accounts = list(ACCOUNTS)

    async def accounts_page(self, *, offset=0, limit=100, search=None, **_kwargs):
        rows = self.accounts
        if search:
            s = search.lower()
            rows = [
                a
                for a in rows
                if any(s in str(a.get(k) or "").lower() for k in ("name", "userName", "address"))
            ]
        return rows[offset : offset + limit], len(rows)


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(inventory_mod, "tenant_client", fake_tc)

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
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add(viewer)
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(inventory_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    client = TestClient(app)
    # deja la copia local armada (sync nocturno): un filtro por columna la dispara
    client.get("/tenants/lab/accounts", params={"platform_id": "WinDomain"})
    return {"client": client, "fake": fake, "session": session}


def _ids(r) -> list[str]:
    return [a["id"] for a in r.json()["accounts"]]


def test_un_termino_encuentra_cuenta_de_dominio_por_remote_machine(env):
    r = env["client"].get("/tenants/lab/accounts", params={"search": "srv-xrail02"})
    assert r.status_code == 200
    assert _ids(r) == ["1"]
    assert r.json()["count"] == 1


def test_un_termino_no_duplica_lo_que_ya_trae_cyberark(env):
    # "203.0.113.13" es el address de la cuenta 4: la trae CyberArk en vivo y
    # no hay ninguna remote machine igual -> sin extras ni duplicados
    r = env["client"].get("/tenants/lab/accounts", params={"search": "203.0.113.13"})
    assert _ids(r) == ["4"]
    assert r.json()["count"] == 1


def test_un_termino_es_parcial_en_remote_machines_y_case_insensible(env):
    r = env["client"].get("/tenants/lab/accounts", params={"search": "VCENTER"})
    assert _ids(r) == ["5"]


def test_un_termino_parcial_trae_los_prefijos_y_se_ven_en_pantalla(env):
    # modo un-término es parcial: "203.0.113.11" trae la 2 y la 3 (110)
    r = env["client"].get("/tenants/lab/accounts", params={"search": "203.0.113.11"})
    assert set(_ids(r)) == {"2", "3"}


def test_lista_de_terminos_es_exacta_y_mezcla_address_y_remote_machines(env):
    r = env["client"].get(
        "/tenants/lab/accounts",
        params={"search": "srv-xrail02, 203.0.113.11, 203.0.113.13, vcenter.globex.local"},
    )
    assert r.status_code == 200
    body = r.json()
    # 1 (rm), 2 (rm exacta), 4 (address), 5 (rm, sin distinguir mayúsculas);
    # NO la 3 (203.0.113.110) ni la 6
    assert set(_ids(r)) == {"1", "2", "4", "5"}
    assert body["count"] == 4
    assert body["synced_at"] is not None


def test_lista_con_salto_de_linea_y_punto_y_coma(env):
    r = env["client"].get(
        "/tenants/lab/accounts", params={"search": "srv-xrail02\n203.0.113.11;203.0.113.13"}
    )
    assert set(_ids(r)) == {"1", "2", "4"}


def test_termino_sin_resultados(env):
    r = env["client"].get("/tenants/lab/accounts", params={"search": "no-existe-en-ningun-lado"})
    assert r.status_code == 200
    assert r.json()["accounts"] == []
    assert r.json()["count"] == 0


def test_extras_van_arriba_de_la_primera_pagina_y_cuentan_en_el_total(env):
    r = env["client"].get("/tenants/lab/accounts", params={"search": "srv-xrail02", "limit": 1})
    assert r.json()["count"] == 1
    r2 = env["client"].get(
        "/tenants/lab/accounts", params={"search": "srv-xrail02", "limit": 1, "offset": 1}
    )
    assert r2.json()["accounts"] == []
    assert r2.json()["count"] == 1  # mismo total en todas las páginas
