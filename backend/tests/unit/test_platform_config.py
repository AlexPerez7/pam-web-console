"""Visor de configuración completa de una plataforma — parser puro del
paquete de "Export Platform" + `GET /tenants/{id}/platforms/{id}/config`.
Ver docs/specs/platform-config-viewer.md."""

from __future__ import annotations

import io
import zipfile
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import inventory as inventory_mod
from app.api.inventory import router as inventory_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.domain.platform_config import parse_ini, parse_platform_package, parse_xml
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

INI = """PolicyID=WinDomain ;Mandatory
PolicyName=Windows Domain
AllowedSafes=.*
MaximumRetries=5
DllName=CyberArk.TPC.dll
PerformPeriodicChange=Yes
ExecutionDays=Mon,Tue
VFPerformPeriodicVerification=No
RCAutomaticReconcileWhenUnsynched=Yes
NFNotifyOnPasswordDisable=Yes
PasswordLength=12
PasswordForbiddenChars=;:
[ExtraInfo]
Port=445
"""

XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Device Name="Operating System">
  <Policies>
    <Policy ID="WinDomain" PlatformBaseID="WinDomain" AutoChangeOnAdd="Yes">
      <Properties>
        <Required><Property Name="Address"/><Property Name="Username"/></Required>
        <Optional><Property Name="LogonDomain"/></Optional>
      </Properties>
      <LinkedPasswords><Link Name="LogonAccount" PropertyIndex="1"/></LinkedPasswords>
      <PrivilegedSessionManagement ID="PSMServer" Enable="Yes"/>
      <ConnectionComponents>
        <ConnectionComponent Id="PSM-RDP"/>
        <ConnectionComponent Id="PSM-SSH">
          <OverrideUserParameters><Parameter Name="AllowMappingLocalDrives" Value="No"/></OverrideUserParameters>
        </ConnectionComponent>
      </ConnectionComponents>
    </Policy>
  </Policies>
</Device>
"""


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _by_id(nodes, node_id):
    for n in nodes:
        if n["id"] == node_id:
            return n
        found = _by_id(n["children"], node_id)
        if found:
            return found
    return None


def _row(node, name):
    return next(r for r in node["rows"] if r["name"] == name)


# --- .ini ---------------------------------------------------------------


def test_ini_agrupa_como_el_pvwa():
    nodes = parse_ini(INI)
    ids = [n["id"] for n in nodes]
    assert ids == [
        "ini/general",
        "ini/plugin",
        "ini/change",
        "ini/verification",
        "ini/reconciliation",
        "ini/notifications",
        "ini/generate",
    ]
    assert _row(_by_id(nodes, "ini/general"), "MaximumRetries")["value"] == "5"
    assert _row(_by_id(nodes, "ini/plugin"), "DllName")["value"] == "CyberArk.TPC.dll"
    assert _row(_by_id(nodes, "ini/change"), "ExecutionDays")["value"] == "Mon,Tue"
    assert _row(_by_id(nodes, "ini/verification"), "VFPerformPeriodicVerification")["value"] == "No"


def test_ini_comentario_inline_y_punto_y_coma_en_el_valor():
    nodes = parse_ini(INI)
    policy = _row(_by_id(nodes, "ini/general"), "PolicyID")
    assert policy["value"] == "WinDomain"
    assert policy["hint"]  # descripción propia de PAM Web Console tiene prioridad
    # ";:" pegado al "=" es parte del valor, no un comentario
    assert _row(_by_id(nodes, "ini/generate"), "PasswordForbiddenChars")["value"] == ";:"
    assert parse_ini("Foo=bar ;texto del ini")[0]["rows"][0]["hint"] == "texto del ini"


def test_ini_secciones_cuelgan_del_plugin():
    plugin = _by_id(parse_ini(INI), "ini/plugin")
    assert [c["label"] for c in plugin["children"]] == ["ExtraInfo"]
    assert plugin["children"][0]["rows"][0] == {"name": "Port", "value": "445", "hint": None}


def test_ini_utf16_con_bom():
    pkg = _zip({"Policy-X.ini": INI.encode("utf-16")})
    tree = parse_platform_package(pkg)["tree"]
    assert tree[0]["id"] == "ini"
    assert _row(_by_id(tree, "ini/general"), "PolicyName")["value"] == "Windows Domain"


# --- .xml ---------------------------------------------------------------


def test_xml_saltea_envoltorios_y_arma_secciones():
    nodes = parse_xml(XML)
    labels = [n["label"] for n in nodes]
    assert labels[0] == "General"
    assert "Propiedades" in labels
    assert "Cuentas vinculadas" in labels
    general = nodes[0]
    assert _row(general, "PlatformBaseID")["value"] == "WinDomain"


def test_xml_psm_es_seccion_propia_no_fila_de_general():
    nodes = parse_xml(XML)
    assert [r["name"] for r in nodes[0]["rows"]] == ["ID", "PlatformBaseID", "AutoChangeOnAdd"]
    psm = next(n for n in nodes if n["source_name"] == "PrivilegedSessionManagement")
    assert psm["label"] == "Gestión de sesiones (PSM)"
    assert {r["name"]: r["value"] for r in psm["rows"]} == {"ID": "PSMServer", "Enable": "Yes"}


def test_xml_listas_se_vuelven_filas():
    nodes = parse_xml(XML)
    props = next(n for n in nodes if n["source_name"] == "Properties")
    required = next(c for c in props["children"] if c["source_name"] == "Required")
    assert [r["name"] for r in required["rows"]] == ["Address", "Username"]
    linked = next(n for n in nodes if n["source_name"] == "LinkedPasswords")
    assert linked["rows"] == [{"name": "LogonAccount", "value": "PropertyIndex: 1", "hint": None}]


def test_xml_connection_component_con_overrides_es_subnodo():
    nodes = parse_xml(XML)
    cc = next(n for n in nodes if n["source_name"] == "ConnectionComponents")
    assert [r["name"] for r in cc["rows"]] == ["PSM-RDP"]
    assert [c["label"] for c in cc["children"]] == ["Connection Component: PSM-SSH"]


# --- paquete ------------------------------------------------------------


def test_paquete_completo_orden_del_pvwa():
    parsed = parse_platform_package(
        _zip({"Policy-X.xml": XML, "Policy-X.ini": INI.encode(), "leeme.txt": b"x"})
    )
    assert [n["id"] for n in parsed["tree"]] == ["xml", "ini"]
    assert sorted(parsed["files"]) == ["Policy-X.ini", "Policy-X.xml"]
    assert parsed["errors"] == []


def test_paquete_xml_roto_no_aborta_el_ini():
    parsed = parse_platform_package(_zip({"a.xml": b"<roto", "a.ini": INI.encode()}))
    assert [n["id"] for n in parsed["tree"]] == ["ini"]
    assert len(parsed["errors"]) == 1


def test_paquete_no_zip():
    with pytest.raises(ValueError):
        parse_platform_package(b"no es un zip")


# --- endpoint -----------------------------------------------------------


class FakeClient:
    def __init__(self, package: bytes) -> None:
        self.package = package

    async def export_platform(self, platform_id: str) -> bytes:
        return self.package


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient(_zip({"Policy-X.xml": XML, "Policy-X.ini": INI.encode()}))

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
    return {"client": TestClient(app), "fake": fake, "session": session}


async def test_endpoint_devuelve_arbol_y_audita(env):
    r = env["client"].get("/tenants/lab/platforms/WinDomain/config")
    assert r.status_code == 200
    body = r.json()
    assert body["platform_id"] == "WinDomain"
    assert [n["id"] for n in body["tree"]] == ["xml", "ini"]
    rows = (
        (
            await env["session"].execute(
                select(AuditLog).where(AuditLog.action == "platform.config.view")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].target == "WinDomain"


def test_endpoint_paquete_invalido_502(env):
    env["fake"].package = b"no es un zip"
    r = env["client"].get("/tenants/lab/platforms/WinDomain/config")
    assert r.status_code == 502
