"""Reporte `user-access` (accesos de un usuario: safes + targets)."""

from __future__ import annotations

import asyncio

from app.cyberark import CyberArkError
from app.reports import REGISTRY
from app.reports.user_access import (
    compute_user_access,
    match_memberships,
    report,
    vault_groups_of,
)

USER = "jdoe@globex.example"


def _member(name, **perms):
    return {"memberName": name, "permissions": perms}


MEMBERS = {
    "SAFE-DIRECTO": [_member("jdoe@globex.example", listAccounts=True, useAccounts=True)],
    "SAFE-ROL": [
        _member("Soporte Linux", listAccounts=True, retrieveAccounts=True),
        _member("otro@globex.example", manageSafe=True),
    ],
    "SAFE-GRUPO": [_member("VaultOperadores", listAccounts=True)],
    "SAFE-AMBOS": [
        _member(USER, listAccounts=True),
        _member("Soporte Linux", useAccounts=True),
    ],
    "SAFE-AJENO": [_member("otro@globex.example", listAccounts=True)],
}


def test_match_memberships_directo_rol_y_grupo():
    m = match_memberships(USER, ["Soporte Linux"], ["VaultOperadores"], MEMBERS)
    assert set(m) == {"SAFE-DIRECTO", "SAFE-ROL", "SAFE-GRUPO", "SAFE-AMBOS"}
    assert m["SAFE-DIRECTO"]["via"] == ["Directo"]
    assert m["SAFE-ROL"]["via"] == ["Rol de Identity: Soporte Linux"]
    assert m["SAFE-GRUPO"]["via"] == ["Grupo: VaultOperadores"]
    # el miembro "otro@..." con manageSafe no se le suma al usuario
    assert m["SAFE-ROL"]["manageSafe"] is False
    assert m["SAFE-ROL"]["retrieveAccounts"] is True


def test_match_memberships_suma_permisos_de_varias_vias():
    m = match_memberships(USER, ["soporte linux"], [], MEMBERS)
    ambos = m["SAFE-AMBOS"]
    assert ambos["via"] == ["Directo", "Rol de Identity: soporte linux"]
    assert ambos["listAccounts"] is True
    assert ambos["useAccounts"] is True


def test_compute_user_access_lista_targets_y_safes_vacios():
    m = match_memberships(USER, [], ["VaultOperadores"], MEMBERS)
    accounts = [
        {"name": "a1", "safeName": "safe-directo", "userName": "root", "address": "10.0.0.2"},
        {"name": "a2", "safeName": "SAFE-DIRECTO", "userName": "admin", "address": "10.0.0.1"},
        {"name": "a3", "safeName": "SAFE-AJENO", "userName": "x", "address": "10.9.9.9"},
    ]
    rows = compute_user_access(m, accounts, USER)
    assert all(r["usuarioConsultado"] == USER for r in rows)
    directo = [r for r in rows if r["safeName"] == "SAFE-DIRECTO"]
    assert [r["address"] for r in directo] == ["10.0.0.1", "10.0.0.2"]
    assert directo[0]["useAccounts"] is True
    grupo = [r for r in rows if r["safeName"] == "SAFE-GRUPO"]
    assert len(grupo) == 1 and grupo[0]["accountName"] is None
    assert not any(r["safeName"] == "SAFE-AJENO" for r in rows)


def test_vault_groups_of():
    users = [
        {"username": "otro", "groupsMembership": [{"groupName": "X"}]},
        {"username": USER.upper(), "groupsMembership": [{"groupName": "VaultOperadores"}]},
    ]
    assert vault_groups_of(USER, users) == ["VaultOperadores"]
    assert vault_groups_of("nadie", users) == []


class _FakeClient:
    def __init__(self, roles_error: bool = False):
        self.roles_error = roles_error

    async def identity_find_user(self, username):
        return {"ID": "uuid-1", "Username": username}

    async def identity_user_roles(self, user_id):
        if self.roles_error:
            raise CyberArkError("sin permiso")
        return ["Soporte Linux"]

    async def list_users(self):
        return [{"username": USER, "groupsMembership": [{"groupName": "VaultOperadores"}]}]

    async def list_safes(self):
        return [{"safeName": name, "safeUrlId": name} for name in MEMBERS]

    async def list_accounts(self, search=None):
        return [{"name": "a1", "safeName": "SAFE-ROL", "userName": "root", "address": "srv1"}]

    async def safe_members(self, safe_id):
        return MEMBERS[safe_id]


def test_run_completo():
    result = asyncio.run(report.run(_FakeClient(), {"usuario": USER}))
    safes = {r["safeName"] for r in result.rows}
    assert safes == {"SAFE-DIRECTO", "SAFE-ROL", "SAFE-GRUPO", "SAFE-AMBOS"}
    assert result.meta["safes_asignados"] == 4
    assert result.meta["cuentas"] == 1
    assert result.meta["roles_de_identity"] == "Soporte Linux"
    assert "aviso" not in result.meta


def test_run_sin_roles_de_identity_sigue_con_directo_y_grupos():
    result = asyncio.run(report.run(_FakeClient(roles_error=True), {"usuario": USER}))
    safes = {r["safeName"] for r in result.rows}
    assert safes == {"SAFE-DIRECTO", "SAFE-GRUPO", "SAFE-AMBOS"}
    assert "roles de Identity" in result.meta["aviso"]


def test_registrado_heavy_con_usuario_obligatorio():
    assert REGISTRY["user-access"] is report
    cat = report.catalog()
    assert cat["heavy"] is True
    assert cat["params"][0]["key"] == "usuario" and cat["params"][0]["required"] is True
