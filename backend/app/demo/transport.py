"""
`httpx.MockTransport` que responde la API de CyberArk en modo demo.

Un transport por cliente, atado al `id` del tenant: enruta por path (sin
mirar el host, cada servicio de Privilege Cloud vive en su propio dominio)
contra el estado en memoria de `app/demo/data.py`. Lo que no reconoce
responde una colección vacía (GET) o un 200 vacío (escrituras), así una
pantalla nueva nunca rompe la demo: simplemente aparece sin datos.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

import httpx

from app.demo.data import DAY, TenantData, tenant_data

Handler = Callable[[TenantData, httpx.Request, re.Match[str]], httpx.Response]
_ROUTES: list[tuple[str, re.Pattern[str], Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _ROUTES.append((method, re.compile(pattern + r"/?$", re.I), fn))
        return fn

    return deco


def ok(body: Any = None, status: int = 200) -> httpx.Response:
    if body is None:
        return httpx.Response(status, content=b"")
    return httpx.Response(status, json=body)


def not_found() -> httpx.Response:
    return httpx.Response(404, json={"ErrorCode": "PASWS013E", "ErrorMessage": "not found"})


def _body(req: httpx.Request) -> Any:
    if not req.content:
        return None
    try:
        return json.loads(req.content)
    except ValueError:
        return None


def _page(
    rows: list, req: httpx.Request, key: str = "value", total_key: str = "count"
) -> httpx.Response:
    p = req.url.params
    offset = int(p.get("offset", 0))
    limit = int(p.get("limit", 1000))
    return ok({key: rows[offset : offset + limit], total_key: len(rows)})


def _public(acc: dict) -> dict:
    return {k: v for k, v in acc.items() if not k.startswith("_")}


# ------------------------------------------------------------------ auth
@route("POST", r"/oauth2/platformtoken")
def _token(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"access_token": f"demo-token-{d.key}", "token_type": "Bearer", "expires_in": 3600})


@route("GET", r"/\.well-known/openid-configuration")
def _alero_discovery(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"issuer": "https://auth.alero.io", "token_endpoint": "https://auth.alero.io/token"})


@route("POST", r"https://auth\.alero\.io/token")
def _alero_token(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"access_token": "demo-alero-token", "expires_in": 3600})


# ------------------------------------------------------------------ server / componentes
@route("GET", r"/API/Server")
def _server(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(
        {"ServerName": "Vault", "ServerID": f"demo-{d.key}", "ApplicationName": "PasswordManager"}
    )


@route("GET", r"/API/ComponentsMonitoringSummary")
def _components_summary(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    names = {
        "PVWA": "Password Vault Web Access",
        "SessionManagement": "Privileged Session Manager",
        "CPM": "Central Policy Manager",
        "AIM": "Application Identity Manager",
    }
    comps = [
        {
            "ComponentID": cid,
            "ComponentName": names[cid],
            "Description": names[cid],
            "ConnectedComponentCount": sum(1 for i in inst if i["IsLoggedOn"]),
            "ComponentTotalCount": len(inst),
            "ComponentSpecificStat": "",
        }
        for cid, inst in d.components.items()
    ]
    return ok(
        {
            "Components": comps,
            "Vaults": [{"Role": "Primary", "IP": "10.0.0.10", "IsLoggedOn": True}],
        }
    )


@route("GET", r"/API/ComponentsMonitoringDetails/(?P<cid>[^/]+)")
def _components_details(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    inst = d.components.get(m["cid"])
    return not_found() if inst is None else ok({"ComponentsDetails": inst})


@route("GET", r"/API/licenses/pcloud")
def _licenses(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(
        {
            "licenses": [
                {"name": "Privileged Users", "used": 14, "total": 25},
                {"name": "Target Accounts", "used": len(d.accounts), "total": 500},
            ]
        }
    )


# ------------------------------------------------------------------ cuentas
_SAVED_FILTERS: dict[str, Callable[[dict], bool]] = {
    "PolicyFailures": lambda a: a["secretManagement"].get("status") == "failure",
    "DisabledPasswordByUser": lambda a: (
        not a["secretManagement"].get("automaticManagementEnabled", True)
    ),
    "DisabledPasswordByCPM": lambda a: False,
    "Locked": lambda a: False,
}


def _account_rows(d: TenantData, req: httpx.Request) -> list[dict]:
    p = req.url.params
    rows = list(d.accounts.values())
    if p.get("savedFilter"):
        fn = _SAVED_FILTERS.get(p["savedFilter"], lambda a: False)
        rows = [a for a in rows if fn(a)]
    flt = p.get("filter") or ""
    if (sm := re.search(r"safeName eq ([^,)]+)", flt)) is not None:
        rows = [a for a in rows if a["safeName"].lower() == sm[1].strip().lower()]
    if (mm := re.search(r"modificationTime gt (\d+)", flt)) is not None:
        rows = [a for a in rows if a["categoryModificationTime"] > int(mm[1])]
    if p.get("search"):
        terms = p["search"].lower().split()
        rows = [
            a
            for a in rows
            if all(
                t in f"{a['name']} {a['userName']} {a['address']} {a['safeName']}".lower()
                for t in terms
            )
        ]
    if p.get("sort"):
        fld, _, direction = p["sort"].partition(" ")
        rows.sort(key=lambda a: str(a.get(fld, "")).lower(), reverse=direction.lower() == "desc")
    return [_public(a) for a in rows]


@route("GET", r"/API/Accounts")
def _accounts(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return _page(_account_rows(d, req), req)


@route("POST", r"/API/Accounts")
def _account_create(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    body = _body(req) or {}
    d.next_id += 1
    acc_id = f"900_{d.next_id}"
    now = int(time.time())
    acc = {
        "id": acc_id,
        "name": body.get("name") or f"{body.get('platformId')}-{body.get('address')}",
        "address": body.get("address", ""),
        "userName": body.get("userName", ""),
        "platformId": body.get("platformId", ""),
        "safeName": body.get("safeName", ""),
        "secretType": body.get("secretType", "password"),
        "platformAccountProperties": body.get("platformAccountProperties") or {},
        "secretManagement": {
            "automaticManagementEnabled": True,
            "status": "success",
            "lastModifiedTime": now,
        },
        "remoteMachinesAccess": body.get("remoteMachinesAccess")
        or {"remoteMachines": "", "accessRestrictedToRemoteMachines": False},
        "createdTime": now,
        "categoryModificationTime": now,
    }
    d.accounts[acc_id] = acc
    return ok(_public(acc), 201)


@route("GET", r"/API/Accounts/(?P<id>[^/]+)")
def _account_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    acc = d.accounts.get(m["id"])
    return not_found() if acc is None else ok(_public(acc))


def _apply_patch(doc: dict, ops: list[dict]) -> None:
    for op in ops:
        parts = [unquote(p) for p in op.get("path", "").strip("/").split("/") if p]
        if not parts:
            continue
        target = doc
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        if op.get("op") == "remove":
            target.pop(parts[-1], None)
        else:
            target[parts[-1]] = op.get("value")


@route("PATCH", r"/API/Accounts/(?P<id>[^/]+)")
def _account_patch(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    acc = d.accounts.get(m["id"])
    if acc is None:
        return not_found()
    _apply_patch(acc, _body(req) or [])
    acc["categoryModificationTime"] = int(time.time())
    return ok(_public(acc))


@route("DELETE", r"/API/Accounts/(?P<id>[^/]+)")
def _account_delete(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(None, 204) if d.accounts.pop(m["id"], None) else not_found()


@route("POST", r"/API/Accounts/(?P<id>[^/]+)/(?P<action>Change|Verify|Reconcile)")
def _account_cpm(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    acc = d.accounts.get(m["id"])
    if acc is None:
        return not_found()
    acc["secretManagement"]["status"] = "inProcess"
    return ok({})


@route("POST", r"/API/Accounts/(?P<id>[^/]+)/Password/Retrieve")
def _account_retrieve(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    if m["id"] not in d.accounts:
        return not_found()
    return ok(f"Demo-{m['id'].replace('_', '')}-Xk9!pQ")


@route("POST", r"/API/Accounts/(?P<id>[^/]+)/(Password/Update|SetNextPassword|LinkAccount)")
def _account_secret_ops(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(None) if m["id"] in d.accounts else not_found()


@route("DELETE", r"/API/Accounts/(?P<id>[^/]+)/LinkAccount/\d+")
def _account_unlink(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(None)


@route("GET", r"/API/Accounts/(?P<id>[^/]+)/Activities")
def _account_activities(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    acc = d.accounts.get(m["id"])
    if acc is None:
        return not_found()
    now = int(time.time())
    rows = [
        {
            "Date": now - 2 * 3600,
            "User": "PasswordManager",
            "Action": "CPM Verify Password",
            "ActionID": 58,
            "ClientID": "CPM",
            "Alert": False,
            "Reason": "",
        },
        {
            "Date": now - 3 * DAY,
            "User": "ana.rojas",
            "Action": "Retrieve Password",
            "ActionID": 295,
            "ClientID": "PVWA",
            "Alert": False,
            "Reason": "Mantención programada CHG-1042",
        },
        {
            "Date": now - 9 * DAY,
            "User": "PasswordManager",
            "Action": "CPM Change Password",
            "ActionID": 38,
            "ClientID": "CPM",
            "Alert": False,
            "Reason": "",
        },
    ]
    if acc.get("_cpm_error"):
        rows.insert(
            0,
            {
                "Date": now - 600,
                "User": "PasswordManager",
                "Action": "CPM Change Password Failed",
                "ActionID": 39,
                "ClientID": "CPM",
                "Alert": True,
                "Reason": acc["_cpm_error"],
            },
        )
    return ok({"Activities": rows})


@route("GET", r"/API/Accounts/(?P<id>[^/]+)/Secret/Versions")
def _account_versions(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    if m["id"] not in d.accounts:
        return not_found()
    now = int(time.time())
    return ok(
        {
            "Versions": [
                {
                    "versionID": v,
                    "modificationDate": now - (5 - v) * 30 * DAY,
                    "modifiedBy": "PasswordManager",
                    "isTemporary": False,
                }
                for v in range(1, 6)
            ]
        }
    )


# ------------------------------------------------------------------ safes
@route("GET", r"/API/Safes")
def _safes(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    rows = list(d.safes.values())
    if q := req.url.params.get("search"):
        rows = [s for s in rows if q.lower() in f"{s['safeName']} {s['description']}".lower()]
    return _page(rows, req)


@route("POST", r"/API/Safes")
def _safe_create(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    body = _body(req) or {}
    name = body.get("safeName", "NUEVO")
    d.safes[name] = {
        "safeUrlId": name,
        "safeName": name,
        "safeNumber": 500 + len(d.safes),
        "description": body.get("description", ""),
        "location": "\\",
        "managingCPM": body.get("managingCPM", ""),
        "olacEnabled": body.get("olacEnabled", False),
        "numberOfDaysRetention": body.get("numberOfDaysRetention", 7),
        "numberOfVersionsRetention": body.get("numberOfVersionsRetention"),
        "creator": {"name": "demo"},
        "creationTime": int(time.time()),
        "lastModificationTime": int(time.time()) * 1_000_000,
    }
    d.members[name] = []
    return ok(d.safes[name], 201)


@route("GET", r"/API/Safes/(?P<id>[^/]+)")
def _safe_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    safe = d.safes.get(unquote(m["id"]))
    return not_found() if safe is None else ok(safe)


@route("PUT", r"/API/Safes/(?P<id>[^/]+)")
def _safe_update(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    safe = d.safes.get(unquote(m["id"]))
    if safe is None:
        return not_found()
    safe.update({k: v for k, v in (_body(req) or {}).items() if k != "safeName"})
    return ok(safe)


@route("DELETE", r"/API/Safes/(?P<id>[^/]+)")
def _safe_delete(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(None, 204) if d.safes.pop(unquote(m["id"]), None) else not_found()


@route("GET", r"/API/Safes/(?P<id>[^/]+)/Members")
def _safe_members(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    rows = d.members.get(unquote(m["id"]))
    return not_found() if rows is None else _page(rows, req)


@route("POST", r"/API/Safes/(?P<id>[^/]+)/Members")
def _safe_member_add(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    safe = unquote(m["id"])
    body = _body(req) or {}
    member = {
        "safeUrlId": safe,
        "safeName": safe,
        "memberName": body.get("memberName"),
        "memberType": body.get("memberType", "User"),
        "isPredefinedUser": False,
        "membershipExpirationDate": body.get("membershipExpirationDate"),
        "permissions": body.get("permissions") or {},
    }
    d.members.setdefault(safe, []).append(member)
    return ok(member, 201)


@route("PUT", r"/API/Safes/(?P<id>[^/]+)/Members/(?P<member>[^/]+)")
def _safe_member_update(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    for mem in d.members.get(unquote(m["id"]), []):
        if mem["memberName"].lower() == unquote(m["member"]).lower():
            mem["permissions"] = (_body(req) or {}).get("permissions", mem["permissions"])
            return ok(mem)
    return not_found()


@route("DELETE", r"/API/Safes/(?P<id>[^/]+)/Members/(?P<member>[^/]+)")
def _safe_member_remove(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    rows = d.members.get(unquote(m["id"]), [])
    keep = [x for x in rows if x["memberName"].lower() != unquote(m["member"]).lower()]
    d.members[unquote(m["id"])] = keep
    return ok(None, 204)


# ------------------------------------------------------------------ plataformas
@route("GET", r"/API/Platforms")
def _platforms(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"Platforms": d.platforms, "Total": len(d.platforms)})


@route("GET", r"/API/Platforms/Targets")
def _target_platforms(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"Platforms": d.target_platforms, "Total": len(d.target_platforms)})


def _target(d: TenantData, numeric_id: str) -> dict | None:
    return next((p for p in d.target_platforms if str(p["ID"]) == numeric_id), None)


@route("GET", r"/API/Platforms/Targets/(?P<nid>\d+)/PrivilegedSessionManagement")
def _platform_psm(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    t = _target(d, m["nid"])
    if t is None:
        return not_found()
    psm = t["PrivilegedSessionManagement"]
    return ok(
        {
            "PSMServerId": psm["PSMServerId"],
            "PSMConnectors": [{"PSMConnectorID": "PSM-RDP", "Enabled": True}],
        }
    )


@route("PUT", r"/API/Platforms/Targets/(?P<nid>\d+)/PrivilegedSessionManagement")
def _platform_psm_update(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    t = _target(d, m["nid"])
    if t is None:
        return not_found()
    body = _body(req) or {}
    if body.get("PSMServerId"):
        t["PrivilegedSessionManagement"]["PSMServerId"] = body["PSMServerId"]
    return ok(body)


@route("POST", r"/API/Platforms/Targets/(?P<nid>\d+)/(?P<action>Activate|Deactivate)")
def _platform_toggle(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    t = _target(d, m["nid"])
    if t is None:
        return not_found()
    t["Active"] = m["action"].lower() == "activate"
    return ok(None)


@route("POST", r"/API/Platforms/Targets/(?P<nid>\d+)/Duplicate")
def _platform_duplicate(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    t = _target(d, m["nid"])
    if t is None:
        return not_found()
    body = _body(req) or {}
    new = {
        **t,
        "ID": max(p["ID"] for p in d.target_platforms) + 1,
        "PlatformID": body.get("Name", "Copia").replace(" ", ""),
        "Name": body.get("Name", "Copia"),
    }
    d.target_platforms.append(new)
    return ok({"ID": new["ID"], "PlatformID": new["PlatformID"], "Name": new["Name"]}, 201)


@route("DELETE", r"/API/Platforms/Targets/(?P<nid>\d+)")
def _platform_delete(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    d.target_platforms = [p for p in d.target_platforms if str(p["ID"]) != m["nid"]]
    return ok(None, 204)


@route("GET", r"/API/Platforms/(?P<pid>[^/]+)")
def _platform_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    p = next((x for x in d.platforms if x["general"]["id"].lower() == m["pid"].lower()), None)
    return (
        not_found()
        if p is None
        else ok(
            {
                "PlatformID": p["general"]["id"],
                "Active": True,
                "Details": p["credentialsManagement"],
                **p,
            }
        )
    )


def _platform_package(pid: str) -> bytes:
    """Paquete de "Export Platform": `Policy-<id>.ini` (CPM) + `.xml` (UI y flujos)."""
    ini = "\r\n".join(
        [
            f"PolicyID={pid}",
            f"PolicyName={pid}",
            "SearchForUsages=Yes",
            "AllowManualChange=Yes",
            "PerformPeriodicChange=Yes",
            "HeadStartInterval=5",
            "FromHour=-1",
            "ToHour=-1",
            "ImmediateInterval=5",
            "Interval=1440",
            "MinValidityPeriod=60",
            "PasswordLength=16",
            "MinUpperCase=2",
            "MinLowerCase=2",
            "MinDigit=1",
            "MinSpecial=1",
            "ExpirationPeriod=90",
            "VFPerformPeriodicVerification=Yes",
            "VFVerificationPeriod=7",
            "RCAllowManualReconciliation=Yes",
            "RCReconcileReasons=2114,2115,2106,2101",
        ]
    )
    xml = (
        f'<?xml version="1.0" encoding="utf-8"?><Device Name="Operating System"><Policies>'
        f'<Policy ID="{pid}" PlatformBaseID="{pid}" PlatformBaseType="{pid}">'
        '<Properties><Required><Property Name="Username" /><Property Name="Address" /></Required>'
        '<Optional><Property Name="LogonDomain" /><Property Name="Port" /></Optional></Properties>'
        '<LinkedPasswords><Link Name="LogonAccount" PropertyIndex="1" />'
        '<Link Name="ReconcileAccount" PropertyIndex="3" /></LinkedPasswords>'
        '<PrivilegedSessionManagement ID="PSMServer_A" Name="PSM-01" />'
        '<ConnectionComponents><ConnectionComponent Id="PSM-RDP" /><ConnectionComponent Id="PSM-SSH" />'
        "</ConnectionComponents></Policy></Policies></Device>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"Policy-{pid}.ini", ini)
        z.writestr(f"Policy-{pid}.xml", xml)
    return buf.getvalue()


@route("POST", r"/API/Platforms/(?P<pid>[^/]+)/Export")
def _platform_export(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    if not any(p["general"]["id"].lower() == m["pid"].lower() for p in d.platforms):
        return not_found()
    return httpx.Response(
        200, content=_platform_package(m["pid"]), headers={"content-type": "application/zip"}
    )


@route("GET", r"/API/PSM/Servers")
def _psm_servers(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"PSMServers": d.psm_servers})


# ------------------------------------------------------------------ usuarios y grupos del Vault
@route("GET", r"/API/Users")
def _users(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    rows = list(d.users.values())
    if q := req.url.params.get("search"):
        rows = [u for u in rows if q.lower() in u["username"].lower()]
    return ok({"Users": rows, "Total": len(rows)})


@route("GET", r"/API/Users/(?P<id>[^/]+)")
def _user_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    u = d.users.get(m["id"])
    return not_found() if u is None else ok(u)


@route("POST", r"/API/Users/(?P<id>[^/]+)/activate")
def _user_activate(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    u = d.users.get(m["id"])
    if u is None:
        return not_found()
    u["suspended"] = False
    return ok(u)


@route("GET", r"/API/UserGroups")
def _groups(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return _page(list(d.groups.values()), req)


@route("GET", r"/API/UserGroups/(?P<id>[^/]+)")
def _group_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    g = d.groups.get(m["id"])
    return not_found() if g is None else ok(g)


# ------------------------------------------------------------------ sesiones y grabaciones
@route("GET", r"/API/LiveSessions")
def _live(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"LiveSessions": d.live_sessions, "Total": len(d.live_sessions)})


@route("GET", r"/API/LiveSessions/(?P<id>[^/]+)")
def _live_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    s = next((x for x in d.live_sessions if x["SessionID"] == m["id"]), None)
    return not_found() if s is None else ok(s)


@route("POST", r"/API/LiveSessions/(?P<id>[^/]+)/Terminate")
def _live_terminate(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    d.live_sessions = [
        x for x in d.live_sessions if m["id"] not in (x["SessionID"], x["SessionGuid"])
    ]
    return ok(None)


@route("GET", r"/API/Recordings")
def _recordings(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    rows = d.recordings
    if (limit := req.url.params.get("Limit") or req.url.params.get("limit")) is not None:
        rows = rows[: int(limit)]
    return ok({"Recordings": rows, "Total": len(d.recordings)})


@route("GET", r"/API/Recordings/(?P<id>[^/]+)/activities")
def _recording_activities(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(
        {
            "Activities": [
                {
                    "ActivityText": "Window title: Server Manager",
                    "ActivityType": 1,
                    "ActivityId": "a1",
                },
                {
                    "ActivityText": "Window title: Administrator: Windows PowerShell",
                    "ActivityType": 1,
                    "ActivityId": "a2",
                },
                {
                    "ActivityText": "Keystroke: Get-Service | Where Status -eq 'Stopped'",
                    "ActivityType": 2,
                    "ActivityId": "a3",
                },
            ]
        }
    )


# ------------------------------------------------------------------ Identity
def _redrock_rows(d: TenantData, script: str) -> list[dict]:
    rows: list[dict]
    cols: tuple[str, ...]
    if re.search(r"\bFROM\s+Role\b", script, re.I):
        rows = d.identity_roles
        cols = ("ID", "Name", "Description", "RoleType", "Organization")
    else:
        rows = d.identity_users
        cols = (
            "ID",
            "Username",
            "DisplayName",
            "Email",
            "UserType",
            "Status",
            "StatusEnum",
            "SourceDs",
            "SourceDsLocalized",
            "SourceDsType",
            "LastLogin",
            "LastInvite",
        )
    like = re.findall(r"LIKE\s+'%?([^'%]+)%?'", script, re.I)
    if like:
        term = like[0].lower()
        rows = [r for r in rows if any(term in str(v).lower() for v in r.values())]
    if (eq := re.search(r"Name\s*=\s*'([^']+)'", script, re.I)) is not None:
        rows = [r for r in rows if str(r.get("Name", "")).lower() == eq[1].lower()]
    lim = re.search(r"LIMIT\s+(\d+)(?:\s+OFFSET\s+(\d+))?", script, re.I)
    if lim:
        off = int(lim[2] or 0)
        rows = rows[off : off + int(lim[1])]
    return [{k: r.get(k) for k in cols} for r in rows]


@route("POST", r"/Redrock/query")
def _redrock(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    script = (_body(req) or {}).get("Script", "")
    rows = _redrock_rows(d, script)
    return ok(
        {"success": True, "Result": {"Count": len(rows), "Results": [{"Row": r} for r in rows]}}
    )


@route("POST", r"/UserMgmt/GetUserInfo")
def _identity_user(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    uid = req.url.params.get("ID", "")
    u = next((x for x in d.identity_users if x["ID"] == uid), None)
    if u is None:
        return ok({"success": False, "Message": "usuario no encontrado"})
    return ok(
        {
            "success": True,
            "Result": {
                "Uuid": u["ID"],
                "Name": u["Username"],
                "DisplayName": u["DisplayName"],
                "Mail": u["Email"],
                "Status": u["Status"],
                "LastLogin": u["LastLogin"],
                "SourceDsLocalized": u["SourceDsLocalized"],
            },
        }
    )


@route("POST", r"/UserMgmt/GetUsersRolesAndAdministrativeRights")
def _identity_user_roles(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    uid = req.url.params.get("id", "") or req.url.params.get("ID", "")
    roles = [r for r in d.identity_roles if any(x["Guid"] == uid for x in d.role_members[r["ID"]])]
    return ok(
        {
            "success": True,
            "Result": {"Results": [{"Row": {"ID": r["ID"], "Name": r["Name"]}} for r in roles]},
        }
    )


@route("POST", r"/UserMgmt/IsUserCloudLocked")
def _identity_locked(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"success": True, "Result": False})


@route("POST", r"/UserMgmt/SetCloudLock")
def _identity_lock(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"success": True, "Result": True})


@route("POST", r"/Roles/GetRoleMembers")
def _role_members(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    rid = req.url.params.get("name", "") or (_body(req) or {}).get("Name", "")
    rows = d.role_members.get(rid, [])
    return ok({"success": True, "Result": {"Results": [{"Row": r} for r in rows]}})


@route("POST", r"/Roles/(StoreRole|UpdateRole)")
def _role_store(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    body = _body(req) or {}
    if m[1] == "StoreRole":
        rid = f"role-{len(d.identity_roles) + 1}"
        d.identity_roles.append(
            {
                "ID": rid,
                "Name": body.get("Name"),
                "Description": body.get("Description", ""),
                "RoleType": "PrincipalList",
                "Organization": None,
            }
        )
        d.role_members[rid] = []
        return ok({"success": True, "Result": {"_RowKey": rid}})
    return ok({"success": True, "Result": None})


# ------------------------------------------------------------------ Connector Management / SIA / Discovery
@route("GET", r"/api/connectors")
def _connectors(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    if "connectormanagement" in req.url.host:
        return ok({"connectors": d.connectors})
    items = [
        {
            "id": c["connectorId"],
            "hostName": c["hostName"],
            "hostIp": c["hostIp"],
            "os": "windows",
            "status": c["status"],
            "isLatestVersion": True,
            "version": c["version"],
        }
        for c in d.connectors[:2]
    ]
    return ok({"items": items})


@route("GET", r"/api/connectors/(?P<id>[^/]+)")
def _sia_connector(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"id": m["id"], "version": "1.5.0", "upgradeVersion": "1.5.0"})


@route("GET", r"/api/connector-pools")
def _pools(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok({"connectorPools": d.connector_pools})


@route("GET", r"/api/discovered-accounts")
def _discovered(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    return ok(
        {
            "items": d.discovered,
            "value": d.discovered,
            "count": len(d.discovered),
            "total": len(d.discovered),
        }
    )


@route("GET", r"/api/discovered-accounts/(?P<id>[^/]+)")
def _discovered_get(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    x = next((a for a in d.discovered if a["id"] == m["id"]), None)
    return not_found() if x is None else ok(x)


# ------------------------------------------------------------------ Alero (Remote Access)
@route("GET", r"/v2-edge/vendors")
def _vendors(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    off = int(req.url.params.get("offset", 0))
    return ok({"vendors": d.vendors[off:] if off < len(d.vendors) else []})


@route("GET", r"/v2-edge/vendors/(?P<id>[^/]+)")
def _vendor(d: TenantData, req: httpx.Request, m: re.Match[str]) -> httpx.Response:
    v = next((x for x in d.vendors if x["id"] == m["id"]), None)
    return not_found() if v is None else ok(v)


def demo_transport(tenant_id: str) -> httpx.MockTransport:
    """Transport mock para el tenant `tenant_id` (ver docstring del módulo)."""

    def handler(req: httpx.Request) -> httpx.Response:
        d = tenant_data(tenant_id)
        full = f"{req.url.scheme}://{req.url.host}{req.url.path}"
        for method, rx, fn in _ROUTES:
            if method != req.method:
                continue
            target = full if rx.pattern.startswith("https") else req.url.path
            # el path puede llevar el prefijo /PasswordVault; las rutas se anclan al final
            mt = rx.search(target)
            if mt is not None and (target.endswith(mt.group(0)) or mt.end() == len(target)):
                return fn(d, req, mt)
        if req.method == "GET":
            return ok({"value": [], "count": 0, "items": [], "Total": 0})
        return ok({})

    return httpx.MockTransport(handler)
