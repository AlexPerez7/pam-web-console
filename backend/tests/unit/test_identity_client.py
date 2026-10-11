"""Cliente CyberArk — métodos de Identity/SSO (UserMgmt)."""

from __future__ import annotations

import pytest

from app.cyberark.client import CyberArkClient
from app.cyberark.errors import CyberArkError


class _Auth:
    identity_tenant_url = "https://tenant.id.cyberark.cloud"

    async def login(self, client):  # pragma: no cover
        return "t"

    async def logout(self, client):  # pragma: no cover
        return None


class _AuthSinIdentity:
    async def login(self, client):  # pragma: no cover
        return "t"

    async def logout(self, client):  # pragma: no cover
        return None


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._body


def _client(auth=None) -> CyberArkClient:
    c = CyberArkClient("https://pvwa", auth or _Auth(), max_concurrency=2)
    c._authed = True
    return c


async def test_get_user_info_devuelve_result(monkeypatch):
    c = _client()
    seen = {}

    async def fake_request(method, url, **kw):
        seen["method"] = method
        seen["url"] = url
        seen["params"] = kw.get("params")
        return _Resp(200, {"success": True, "Result": {"Username": "jdoe"}})

    monkeypatch.setattr(c._http, "request", fake_request)
    out = await c.identity_get_user_info("u-1")
    assert out == {"Username": "jdoe"}
    assert seen["method"] == "POST"
    assert seen["url"] == "https://tenant.id.cyberark.cloud/UserMgmt/GetUserInfo"
    assert seen["params"] == {"ID": "u-1"}


async def test_get_user_info_success_false_levanta_error(monkeypatch):
    c = _client()

    async def fake_request(method, url, **kw):
        return _Resp(200, {"success": False, "Message": "User not found"})

    monkeypatch.setattr(c._http, "request", fake_request)
    with pytest.raises(CyberArkError, match="User not found"):
        await c.identity_get_user_info("u-1")


async def test_is_user_locked_parsea_string_bool(monkeypatch):
    c = _client()

    async def fake_request(method, url, **kw):
        return _Resp(200, {"success": True, "Result": "True"})

    monkeypatch.setattr(c._http, "request", fake_request)
    assert await c.identity_is_user_locked("jdoe") is True


async def test_set_cloud_lock_manda_params_correctos(monkeypatch):
    c = _client()
    seen = {}

    async def fake_request(method, url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        return _Resp(200, {"success": True, "Result": None})

    monkeypatch.setattr(c._http, "request", fake_request)
    await c.identity_set_cloud_lock("u-1", True)
    assert seen["url"] == "https://tenant.id.cyberark.cloud/UserMgmt/SetCloudLock"
    assert seen["params"] == {"lockUser": "true", "user": "u-1"}


async def test_sin_identity_configurado_levanta_error():
    c = _client(_AuthSinIdentity())
    with pytest.raises(CyberArkError, match="Identity configurado"):
        await c.identity_get_user_info("u-1")


async def test_role_members_lee_filas(monkeypatch):
    c = _client()
    seen = {}

    async def fake_request(method, url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        return _Resp(
            200,
            {
                "success": True,
                "Result": {
                    "Results": [
                        {"Row": {"Guid": "u1", "Name": "ana@x", "Type": "User"}},
                        {"Row": {"Guid": "g1", "Name": "Grupo", "Type": "Group"}},
                        {"Entities": []},
                    ]
                },
            },
        )

    monkeypatch.setattr(c._http, "request", fake_request)
    out = await c.identity_role_members("r-1")
    assert [m["Name"] for m in out] == ["ana@x", "Grupo"]
    assert seen["url"] == "https://tenant.id.cyberark.cloud/Roles/GetRoleMembers"
    assert seen["params"] == {"name": "r-1"}


async def test_role_members_success_false_levanta_error(monkeypatch):
    c = _client()

    async def fake_request(method, url, **kw):
        return _Resp(200, {"success": False, "Message": "Role not found"})

    monkeypatch.setattr(c._http, "request", fake_request)
    with pytest.raises(CyberArkError, match="Role not found"):
        await c.identity_role_members("r-1")


async def test_role_add_members_manda_solo_add(monkeypatch):
    c = _client()
    seen = {}

    async def fake_request(method, url, **kw):
        seen["url"] = url
        seen["json"] = kw.get("json")
        return _Resp(200, {"success": True, "Result": None})

    monkeypatch.setattr(c._http, "request", fake_request)
    await c.identity_role_add_members("r-1", users=["u-2"], roles=[])
    assert seen["url"] == "https://tenant.id.cyberark.cloud/Roles/UpdateRole"
    assert seen["json"] == {"Name": "r-1", "Users": {"Add": ["u-2"]}}
