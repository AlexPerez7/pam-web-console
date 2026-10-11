"""account.create.bulk — onboarding masivo de cuentas por CSV.

Ver docs/specs/done/account-bulk-onboard.md. v1 síncrono (sin cola de jobs):
una request procesa todas las filas, sin abortar si alguna falla.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writes as writes_mod
from app.api.writes import router as writes_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark.errors import NotFound
from app.db import get_session
from app.domain.accounts import parse_bulk_csv
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

# --- parse_bulk_csv (dominio, puro) ------------------------------------- #

_HEADER = "safe_name,platform_id,user_name,address"


def test_parse_filas_validas():
    csv_text = f"{_HEADER}\nCajaFuerte,WinDomain,jdoe,10.0.0.1\nOtraSafe,UnixSSH,root,10.0.0.2\n"
    ok, err = parse_bulk_csv(csv_text)
    assert err == []
    assert len(ok) == 2
    assert ok[0]["_row"] == 2
    assert ok[0]["safe_name"] == "CajaFuerte"
    assert ok[0]["automatic_management_enabled"] is True


def test_parse_columna_obligatoria_faltante_en_header():
    ok, err = parse_bulk_csv("safe_name,platform_id\nX,Y\n")
    assert ok == []
    assert len(err) == 1
    assert "user_name" in err[0]["error"]


def test_parse_fila_con_campo_obligatorio_vacio():
    csv_text = f"{_HEADER}\nCajaFuerte,WinDomain,,10.0.0.1\n"
    ok, err = parse_bulk_csv(csv_text)
    assert ok == []
    assert err[0]["_row"] == 2
    assert "user_name" in err[0]["error"]


def test_parse_columna_extra_va_a_properties():
    csv_text = f"{_HEADER},Port,Notes\nCajaFuerte,WinDomain,jdoe,10.0.0.1,3389,algo\n"
    ok, _err = parse_bulk_csv(csv_text)
    assert ok[0]["properties"] == {"Port": "3389", "Notes": "algo"}


def test_parse_gestion_manual_sin_motivo_es_error():
    csv_text = f"{_HEADER},automatic_management_enabled\nCajaFuerte,WinDomain,jdoe,10.0.0.1,false\n"
    ok, err = parse_bulk_csv(csv_text)
    assert ok == []
    assert "manual_management_reason" in err[0]["error"]


def test_parse_gestion_manual_con_motivo_ok():
    csv_text = (
        f"{_HEADER},automatic_management_enabled,manual_management_reason\n"
        "CajaFuerte,WinDomain,jdoe,10.0.0.1,false,cuenta legacy sin rotación\n"
    )
    ok, err = parse_bulk_csv(csv_text)
    assert err == []
    assert ok[0]["automatic_management_enabled"] is False
    assert ok[0]["manual_management_reason"] == "cuenta legacy sin rotación"


# --- endpoints ------------------------------------------------------------ #


class FakeClient:
    def __init__(self, missing_safes: set[str] | None = None) -> None:
        self.missing_safes = missing_safes or set()
        self.created: list[dict] = []

    async def get_safe(self, safe_id: str) -> dict:
        if safe_id in self.missing_safes:
            raise NotFound(f"safe '{safe_id}' no existe")
        return {"safeName": safe_id, "managingCPM": "CPM1"}

    async def get_platform_details(self, platform_id: str) -> dict:
        return {"Details": {}}

    async def create_account(self, body: dict) -> dict:
        self.created.append(body)
        return {"id": str(len(self.created))}


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient(missing_safes={"NoExiste"})

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writes_mod, "tenant_client", fake_tc)

    async def cache_delete(key: str) -> None:
        return None

    monkeypatch.setattr(writes_mod.cache, "delete", cache_delete)

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
    op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([op, viewer])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


CSV_MIXED = (
    f"{_HEADER}\n"
    "CajaFuerte,WinDomain,jdoe,10.0.0.1\n"  # ok
    "NoExiste,WinDomain,root,10.0.0.2\n"  # safe inexistente
    ",WinDomain,x,10.0.0.3\n"  # fila inválida (safe_name vacío)
)


def _upload(client: TestClient, path: str, csv_text: str, **kw):
    return client.post(path, files={"file": ("cuentas.csv", csv_text, "text/csv")}, **kw)


def test_preview_filas_mixtas(env):
    r = _upload(env["client"], "/tenants/lab/accounts/bulk-create/preview", CSV_MIXED)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["valid"] == 1
    assert body["invalid"] == 2
    assert env["fake"].created == []
    rows = {row["row"]: row for row in body["rows"]}
    assert rows[2]["ok"] is True
    assert rows[3]["ok"] is False and "safe" in rows[3]["error"]
    assert rows[4]["ok"] is False


def test_preview_tope_de_filas(env, monkeypatch):
    monkeypatch.setattr(writes_mod.get_settings(), "bulk_create_max_rows", 1)
    csv_text = f"{_HEADER}\nA,P,u,1\nB,P,u,2\n"
    r = _upload(env["client"], "/tenants/lab/accounts/bulk-create/preview", csv_text)
    assert r.status_code == 409


def test_ejecucion_crea_filas_validas_y_reporta_las_que_fallan(env):
    r = _upload(
        env["client"],
        "/tenants/lab/accounts/bulk-create",
        CSV_MIXED,
        data={"reason": "onboarding cliente nuevo, ticket OPS-20"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["created"] == 1
    assert len(body["failed"]) == 2
    assert len(env["fake"].created) == 1
    assert env["fake"].created[0]["safeName"] == "CajaFuerte"


def test_ejecucion_sin_reason_422(env):
    r = _upload(
        env["client"], "/tenants/lab/accounts/bulk-create", CSV_MIXED, data={"reason": "corto"}
    )
    assert r.status_code == 422
    assert env["fake"].created == []


def test_preview_rbac_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = _upload(env["client"], "/tenants/lab/accounts/bulk-create/preview", CSV_MIXED)
    assert r.status_code == 403
