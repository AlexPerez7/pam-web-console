"""Espacio SIA, sólo lectura. Ver docs/specs/sia-read-only.md."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import sia as sia_mod
from app.api.sia import router as sia_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark import Forbidden, NotFound, UpstreamError
from app.cyberark.client import isp_service_url
from app.db import get_session
from app.domain import sia
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

SECRET_VALUE = "S3cr3t-no-debe-salir"

UAP_VM = {
    "metadata": {
        "policyId": "u1",
        "name": "Linux prod",
        "status": {"status": "Active"},
        "timeFrame": {"fromTime": "2026-01-01T00:00:00", "toTime": None},
        "policyEntitlement": {"targetCategory": "VM", "locationType": "FQDN/IP"},
        "updatedOn": {"user": "admin@x", "time": "2026-09-01T10:00:00"},
        "timeZone": "America/Santiago",
    },
    "principals": [
        {"id": "1", "name": "ops", "type": "ROLE"},
        {"id": "2", "name": "ana@x", "type": "USER", "sourceDirectoryName": "CyberArk"},
    ],
    "conditions": {
        "accessWindow": {"daysOfTheWeek": [1, 2, 3], "fromHour": "08:00", "toHour": "18:00"},
        "maxSessionDuration": 4,
        "idleTime": 10,
    },
    "targets": {"FQDN/IP": {"fqdnRules": [{"operator": "WILDCARD", "computernamePattern": "*"}]}},
    "behavior": {"connectAs": {"ssh": {"username": "ec2-user"}}},
}

LEGACY_VM = {
    "policyId": "l1",
    "policyName": "Windows legado",
    "status": "Disabled",
    "ruleNames": ["r1"],
    "platforms": ["OnPrem"],
    "updatedOn": "2026-08-01",
}

LEGACY_VM_DETAIL = LEGACY_VM | {
    "providersData": {"OnPrem": {"fqdnRulesConjunction": "OR", "fqdnRules": []}},
    "userAccessRules": [
        {
            "ruleName": "r1",
            "userData": {"roles": [{"name": "Admins"}], "users": ["juan@x"], "groups": []},
            "connectionInformation": {
                "daysOfWeek": ["Mon", "Fri"],
                "fullDays": True,
                "grantAccess": 2,
                "connectAs": {
                    "OnPrem": {"rdp": {"localEphemeralUser": {"assignGroups": ["Administrators"]}}}
                },
            },
        }
    ],
}

DB_SECRET = {
    "secret_id": "s1",
    "secret_name": "pg-admin",
    "secret_type": "username_password",
    "secret_store": {"store_type": "managed"},
    "secret_exposed_data": {"username": "postgres"},
    "secret_data": {"username": "postgres", "password": SECRET_VALUE},
    "password": SECRET_VALUE,
    "created_by": "a",
    "creation_time": "2026-01-01T00:00:00",
    "last_updated_by": "a",
    "last_update_time": "2026-01-01T00:00:00",
    "is_active": True,
}

VM_SECRET = {
    "secret_id": "v1",
    "secret_name": "provisioner",
    "secret_type": "ProvisionerUser",
    "secret_details": {"username": "prov", "password": SECRET_VALUE, "accountDomain": "corp"},
    "secret": {"secret_data": SECRET_VALUE},
    "is_active": False,
}

# Como la strong account de Globex en el portal (Setup > Strong accounts).
VM_DOMAIN_SECRET = {
    "secret_id": "v2",
    "secret_name": "Operating System-TIV-WIN-DOM-GCP-globex.ccf-ca_sia_zsp_CyberArk-Services-Accounts",
    "secret_type": "PCloudAccount",
    "secret_details": json.dumps(
        {
            "account_domain": "globex.ccf",
            "ephemeral_domain_user_data": {
                "domain_controller": {
                    "domain_controller_name": "chvaldc01.globex.ccf",
                    "domain_controller_use_ldaps": True,
                },
                "ephemeral_domain_user_location": "",
                "winrm_info": {"use_winrm_for_https": True},
            },
        }
    ),
    "secret": {
        "secret_data": {
            "safe": "CyberArk-Services-Accounts",
            "account_name": "Operating System-TIV-WIN-DOM-GCP-globex.ccf-ca_sia_zsp",
            "password": SECRET_VALUE,
        }
    },
    "is_active": True,
    "creation_time": "2026-09-01T00:00:00",
}


# ------------------------------------------------------------------ #
# Dominio
# ------------------------------------------------------------------ #
def test_url_por_servicio_se_deriva_del_host_de_sia():
    url = "https://globex.dpa.cyberark.cloud/"
    assert isp_service_url(url, "uap") == "https://globex.uap.cyberark.cloud"
    assert (
        isp_service_url(url, "sessionmonitoring")
        == "https://globex.sessionmonitoring.cyberark.cloud"
    )
    assert isp_service_url("https://globex", "dpa") == "https://globex.dpa.cyberark.cloud"


def test_politica_uap_normalizada():
    row = sia.uap_policy_row(UAP_VM, "vm")
    assert row["source"] == "uap" and row["status"] == "active"
    assert (row["principalsCount"], row["locations"]) == (2, ["FQDN/IP"])
    d = sia.uap_policy_detail(UAP_VM, "vm")
    rule = d["rules"][0]
    assert rule["days"] == ["Lun", "Mar", "Mié"]
    assert (rule["fromHour"], rule["maxSessionHours"], rule["idleMinutes"]) == ("08:00", 4, 10)
    assert any("ec2-user" in line for line in rule["connectAs"])
    assert d["principals"][0] == {"type": "role", "name": "ops", "source": None}
    assert d["targets"]


UAP_RDP_DB = {
    "metadata": {
        "policyId": "u2",
        "name": "Windows_ZSP",
        "status": {"status": "Active"},
        "policyEntitlement": {"targetCategory": "VM", "locationType": "FQDN/IP"},
        "createdBy": {"user": "jgonzalez@x", "time": "2026-09-08T12:00:00"},
        "updatedOn": {"user": "soporte@x", "time": "2026-09-30T12:00:00"},
        "policyTags": ["zsp"],
    },
    "principals": [{"id": "1", "name": "ops", "type": "ROLE"}],
    "targets": {
        "FQDN/IP": {
            "ipRules": [{"operator": "EXACTLY", "ipAddresses": ["10.0.0.5"], "logicalName": "lab"}]
        }
    },
    "behavior": {
        "connectAs": {
            "rdp": {
                "localEphemeralUser": {
                    "assignGroups": ["Administrators"],
                    "enableEphemeralUserReconnect": True,
                }
            }
        }
    },
}


def test_politica_uap_detalle_estructurado():
    d = sia.uap_policy_detail(UAP_RDP_DB, "vm")
    assert (d["createdBy"], d["modifiedBy"], d["tags"]) == ("jgonzalez@x", "soporte@x", ["zsp"])
    assert d["targetGroups"] == [
        {
            "location": "FQDN/IP",
            "type": "fqdn_ip",
            "fqdnRules": [],
            "ipRules": [{"operator": "EXACTLY", "ips": ["10.0.0.5"], "logicalName": "lab"}],
        }
    ]
    assert d["permissions"] == {
        "ssh": None,
        "rdp": {
            "mode": "local",
            "groups": ["Administrators"],
            "domainGroups": [],
            "reconnect": True,
        },
    }
    # sin conditions en la respuesta: días vacíos, no inventados
    assert d["conditions"]["days"] == [] and d["conditions"]["maxSessionHours"] is None


def test_politica_uap_targets_de_nube_y_db():
    vm = {
        "metadata": {},
        "targets": {"AWS": {"regions": ["us-east-1"], "tags": [{"key": "env", "value": ["prod"]}]}},
    }
    g = sia.uap_policy_detail(vm, "vm")["targetGroups"][0]
    assert g["type"] == "cloud" and g["fields"][0] == {"label": "Regiones", "values": ["us-east-1"]}
    assert g["tags"] == [{"key": "env", "values": ["prod"]}]
    db = {
        "metadata": {},
        "targets": {
            "FQDN/IP": {
                "instances": [
                    {
                        "instanceName": "pg1",
                        "instanceType": "Postgres",
                        "authenticationMethod": "db_auth",
                        "profile": {"roles": ["reader"]},
                    }
                ]
            }
        },
    }
    inst = sia.uap_policy_detail(db, "db")["targetGroups"][0]["instances"][0]
    assert (inst["name"], inst["authMethod"]) == ("pg1", "db_auth")
    assert inst["permissions"] == ["roles: reader"]


def test_politica_legado_normalizada():
    row = sia.legacy_policy_row(LEGACY_VM, "vm")
    assert (row["status"], row["rulesCount"], row["principalsCount"]) == ("suspended", 1, None)
    d = sia.legacy_policy_detail(LEGACY_VM_DETAIL, "vm")
    assert (d["rulesCount"], d["principalsCount"]) == (1, 2)
    rule = d["rules"][0]
    assert rule["days"] == ["Lun", "Vie"] and rule["fromHour"] is None
    assert any("Administrators" in line for line in rule["connectAs"])


def test_secretos_salen_por_lista_blanca():
    db = sia.db_secret_row(DB_SECRET)
    vm = sia.vm_secret_row(VM_SECRET)
    assert db["username"] == "postgres" and db["store"] == "managed"
    assert vm["username"] == "prov" and vm["active"] is False and vm["domain"] == "corp"
    dumped = json.dumps([db, vm])
    assert SECRET_VALUE not in dumped
    keys = {k for row in (db, vm) for k in row}
    assert not {"password", "secret_data", "secretData", "secret"} & keys


def test_strong_account_de_dominio_como_en_el_portal():
    r = sia.vm_secret_row(VM_DOMAIN_SECRET)
    assert (r["accountType"], r["domain"]) == ("domain", "globex.ccf")
    assert r["linkedSafe"] == "CyberArk-Services-Accounts"
    assert r["linkedAccount"] == "Operating System-TIV-WIN-DOM-GCP-globex.ccf-ca_sia_zsp"
    assert r["ephemeralDomainUsers"] is True
    assert r["domainController"] == "chvaldc01.globex.ccf"
    assert r["ephemeralUserLocation"] is None  # vacío en el portal
    assert (r["domainControllerLdaps"], r["winrmHttps"]) == (True, True)
    assert SECRET_VALUE not in json.dumps(r)
    local = sia.vm_secret_row({"secret_id": "x", "secret_type": "PCloudAccount"})
    assert (local["accountType"], local["domain"], local["ephemeralDomainUsers"]) == (
        "local",
        "local",
        False,
    )


# Respuesta real de `retrieve_pcloud_account_by_admin` en Globex (2026-10-07).
PCLOUD_ACCOUNT = {
    "safe": "CyberArk-Services-Accounts",
    "account_name": "Operating System-TIV-WIN-DOM-GCP-globex.ccf-ca_sia_zsp",
    "account_id": "378_3",
    "username": "ca_sia_zsp",
}


def test_strong_account_pcloud_resuelta_aparte():
    # En Globex la lista de api/secrets no trae `secret`: el safe y la
    # cuenta salen de la llamada aparte, como en el portal.
    sin_secret = {k: v for k, v in VM_DOMAIN_SECRET.items() if k != "secret"}
    data = sia.strong_accounts([sin_secret], [], [], [], {"v2": PCLOUD_ACCOUNT})
    r = data["vm"][0]
    assert r["linkedSafe"] == "CyberArk-Services-Accounts"
    assert r["linkedAccount"] == "Operating System-TIV-WIN-DOM-GCP-globex.ccf-ca_sia_zsp"
    assert r["username"] == "ca_sia_zsp"
    assert sia.strong_accounts([sin_secret], [], [], [])["vm"][0]["linkedSafe"] is None


def test_strong_accounts_agrupa_target_sets_y_bases():
    data = sia.strong_accounts(
        [VM_DOMAIN_SECRET],
        [
            {"name": "globex.ccf", "type": "Domain", "secret_id": "v2"},
            {"name": "huerfano.local", "type": "Domain", "secret_id": "nope"},
        ],
        [DB_SECRET],
        [{"id": 7, "name": "pg", "secret_id": "s1"}, {"id": 8, "name": "otra"}],
    )
    assert [t["name"] for t in data["vm"][0]["targetSets"]] == ["globex.ccf"]
    assert [d["name"] for d in data["db"][0]["databases"]] == ["pg"]
    assert [t["name"] for t in data["targetSetsWithoutAccount"]] == ["huerfano.local"]
    assert [d["name"] for d in data["databasesWithoutAccount"]] == ["otra"]


def test_strip_sensitive_quita_claves_de_secreto_del_json_crudo():
    raw = {"a": 1, "password": "x", "nested": [{"secretData": "y", "secretId": "keep"}]}
    assert sia.strip_sensitive(raw) == {"a": 1, "nested": [{"secretId": "keep"}]}


def test_sesiones_duracion_y_filtro_sia():
    assert sia.duration_seconds("01:02:03") == 3723
    assert sia.duration_seconds("1 day, 00:00:10") == 86410
    assert sia.duration_seconds(42) == 42
    assert sia.duration_seconds("PT1M5S") == 65
    assert sia.duration_seconds("raro") is None
    assert sia.is_sia_session({"applicationCode": "DPA"})
    assert not sia.is_sia_session({"applicationCode": "SCA"})
    assert sia.is_sia_session({})  # sin código: se muestra


def test_busqueda_de_sesiones():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    assert sia_mod._sessions_search("active") == "sessionStatus IN Active"
    assert sia_mod._sessions_search("recent", now) == "startTime GE 2026-10-05T12:00:00Z"


def test_settings_agrupados_con_etiquetas():
    groups = sia.settings_groups(
        {
            "rdpRecording": {"enabled": True},
            "SSH_MFA_CACHING": {"is_mfa_caching_enabled": False, "key_expiration_time_sec": 3600},
            "nuevoSetting": {"x": 1, "complejo": {"no": "se muestra"}},
        }
    )
    by_key = {g["key"]: g for g in groups}
    assert by_key["rdpRecording"]["label"] == "RDP — grabación"
    ssh = by_key["sshMfaCaching"]
    assert ssh["label"] == "Caché de MFA — SSH"
    assert ssh["fields"][0] == {
        "key": "isMfaCachingEnabled",
        "label": "Caché de MFA habilitada",
        "value": False,
    }
    assert by_key["nuevoSetting"]["fields"] == [{"key": "x", "label": "x", "value": 1}]


def test_settings_de_globex_traducidos():
    groups = sia.settings_groups(
        {"httpsRelay": {"isHttpsRelayEnabled": False, "relayHost": "", "sshRelayPort": 2222}}
    )
    assert groups[0]["label"] == "Relay HTTPS"
    assert [f["label"] for f in groups[0]["fields"]] == [
        "Relay HTTPS habilitado",
        "Host del relay",
        "Puerto SSH del relay",
    ]


# ------------------------------------------------------------------ #
# Router
# ------------------------------------------------------------------ #
class FakeClient:
    def __init__(self, *, sia_url=True, uap: list | Exception | None = None, **errors):
        self.sia_connector_url = "https://t.dpa.cyberark.cloud" if sia_url else None
        self._uap = uap if uap is not None else NotFound("404")
        self._errors = errors

    def _maybe(self, name: str, value):
        err = self._errors.get(name)
        if isinstance(err, Exception):
            raise err
        return value

    async def list_uap_policies(self, category):
        if isinstance(self._uap, Exception):
            raise self._uap
        return self._uap if category == "VM" else []

    async def list_sia_vm_policies(self):
        return self._maybe("vm_policies", [LEGACY_VM])

    async def list_sia_db_policies(self):
        return self._maybe("db_policies", [])

    async def get_uap_policy(self, pid):
        return UAP_VM

    async def get_sia_policy(self, kind, pid):
        return LEGACY_VM_DETAIL

    async def list_sm_sessions(self, search):
        self.last_search = search
        return self._maybe(
            "sessions",
            (
                [
                    {"sessionId": "a", "applicationCode": "DPA", "sessionStatus": "Active"},
                    {"sessionId": "b", "applicationCode": "SCA", "sessionStatus": "Active"},
                ],
                2,
            ),
        )

    async def get_sm_session(self, sid):
        return {"sessionId": sid, "applicationCode": "DPA", "sessionDuration": "00:01:00"}

    async def list_sm_session_activities(self, sid):
        return self._maybe("activities", [{"uuid": "x", "command": "ls"}])

    async def list_sia_db_resources(self):
        return self._maybe(
            "db_resources", [{"id": 1, "name": "pg", "provider_info": {"engine": "postgres"}}]
        )

    async def list_sia_target_sets(self):
        return self._maybe("target_sets", [{"name": "corp.local", "type": "Domain"}])

    async def list_sia_db_secrets(self):
        return self._maybe("db_secrets", [DB_SECRET])

    async def list_sia_vm_secrets(self):
        return self._maybe("vm_secrets", self._errors.get("vm_secrets_list") or [VM_SECRET])

    async def get_sia_pcloud_account(self, secret_id):
        return PCLOUD_ACCOUNT if secret_id == "v2" else None

    async def get_sia_settings(self):
        return self._maybe("settings", {"rdpRecording": {"enabled": True}})


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    holder: dict = {"client": FakeClient(), "cache_sets": []}

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, holder["client"])

    monkeypatch.setattr(sia_mod, "tenant_client", fake_tc)

    async def get_json(key: str):
        return None

    async def set_json(key: str, value: dict, ttl: int) -> None:
        holder["cache_sets"].append((key, json.dumps(value)))

    monkeypatch.setattr(sia_mod.cache, "get_json", get_json)
    monkeypatch.setattr(sia_mod.cache, "set_json", set_json)

    session.add(
        Tenant(
            id="t1",
            name="T",
            client="A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="t1",
        )
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add(viewer)
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="t1", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(sia_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {"client": TestClient(app), "holder": holder, "session": session}


async def _audit(env) -> list[tuple]:
    rows = (await env["session"].execute(select(AuditLog))).scalars().all()
    return [(a.action, a.result, a.detail) for a in rows]


async def test_politicas_caen_a_legado_si_no_hay_uap_y_se_audita(env):
    r = env["client"].get("/tenants/t1/sia/policies")
    assert r.status_code == 200
    body = r.json()
    assert [(p["id"], p["source"]) for p in body["policies"]] == [("l1", "legacy")]
    assert await _audit(env) == [("sia.policies.list", "ok", {"count": 1})]


async def test_politicas_uap_tienen_prioridad(env):
    env["holder"]["client"] = FakeClient(uap=[UAP_VM])
    body = env["client"].get("/tenants/t1/sia/policies").json()
    assert [(p["id"], p["source"]) for p in body["policies"]] == [("u1", "uap")]


async def test_uap_sin_politicas_de_db_y_legado_403_no_es_falta_de_licencia(env):
    # Globex (2026-10-07): UAP con una política de VM y ninguna de DB; la
    # ruta legado de DB responde 403. No debe aparecer el aviso de licencia.
    env["holder"]["client"] = FakeClient(uap=[UAP_VM], db_policies=Forbidden("403"))
    body = env["client"].get("/tenants/t1/sia/policies").json()
    assert [(p["id"], p["source"]) for p in body["policies"]] == [("u1", "uap")]
    assert body["warnings"] == []


async def test_sin_uap_el_403_de_legado_sigue_avisando(env):
    env["holder"]["client"] = FakeClient(db_policies=Forbidden("403"))
    body = env["client"].get("/tenants/t1/sia/policies").json()
    assert [p["id"] for p in body["policies"]] == ["l1"]
    assert body["warnings"] == [f"Bases de datos: {sia_mod.SIA_FORBIDDEN}"]


async def test_detalle_de_politica(env):
    r = env["client"].get("/tenants/t1/sia/policies/l1?source=legacy&kind=vm")
    assert r.status_code == 200
    assert r.json()["policy"]["rules"][0]["name"] == "r1"
    log = await _audit(env)
    assert log[0][0] == "sia.policy.get" and log[0][2]["source"] == "legacy"


def test_tenant_sin_sia_responde_409(env):
    env["holder"]["client"] = FakeClient(sia_url=False)
    for path in ("policies", "sessions", "strong-accounts", "settings"):
        r = env["client"].get(f"/tenants/t1/sia/{path}")
        assert r.status_code == 409, path
        assert r.json()["detail"] == sia_mod.NO_SIA_URL


async def test_403_se_traduce_y_se_audita_como_error(env):
    env["holder"]["client"] = FakeClient(settings=Forbidden("403"))
    r = env["client"].get("/tenants/t1/sia/settings")
    assert r.status_code == 403
    assert r.json()["detail"] == sia_mod.SIA_FORBIDDEN
    assert (await _audit(env))[0][:2] == ("sia.settings.get", "error")


async def test_una_parte_caida_no_rompe_la_otra(env):
    env["holder"]["client"] = FakeClient(vm_secrets=UpstreamError("503"))
    body = env["client"].get("/tenants/t1/sia/strong-accounts").json()
    assert body["vm"] == [] and [s["id"] for s in body["db"]] == ["s1"]
    assert body["warnings"] and body["warnings"][0].startswith("Strong accounts de VM:")


def test_si_fallan_todas_las_partes_responde_error(env):
    env["holder"]["client"] = FakeClient(
        db_resources=Forbidden("403"),
        target_sets=Forbidden("403"),
        db_secrets=Forbidden("403"),
        vm_secrets=Forbidden("403"),
    )
    r = env["client"].get("/tenants/t1/sia/strong-accounts")
    assert r.status_code == 403 and r.json()["detail"] == sia_mod.SIA_FORBIDDEN


async def test_secretos_nunca_salen_ni_al_audit_ni_a_la_cache(env):
    env["holder"]["client"] = FakeClient(vm_secrets_list=[VM_SECRET, VM_DOMAIN_SECRET])
    r = env["client"].get("/tenants/t1/sia/strong-accounts")
    assert r.status_code == 200
    assert len(r.json()["vm"]) == 2
    assert SECRET_VALUE not in r.text
    assert env["holder"]["cache_sets"] == []  # los secretos no pasan por Redis
    assert SECRET_VALUE not in json.dumps([a[2] for a in await _audit(env)])


def test_sesiones_activas_solo_de_sia(env):
    body = env["client"].get("/tenants/t1/sia/sessions?status=active").json()
    assert [s["id"] for s in body["sessions"]] == ["a"]
    assert env["holder"]["client"].last_search == "sessionStatus IN Active"


def test_detalle_de_sesion_con_actividades_caidas(env):
    env["holder"]["client"] = FakeClient(activities=UpstreamError("503"))
    body = env["client"].get("/tenants/t1/sia/sessions/a").json()
    assert body["session"]["durationSeconds"] == 60
    assert body["activities"] == [] and body["warnings"]


async def test_strong_accounts_pcloud_traen_el_safe(env):
    sin_secret = {k: v for k, v in VM_DOMAIN_SECRET.items() if k != "secret"}
    env["holder"]["client"] = FakeClient(vm_secrets_list=[sin_secret, VM_SECRET])
    body = env["client"].get("/tenants/t1/sia/strong-accounts").json()
    by_id = {r["id"]: r for r in body["vm"]}
    assert by_id["v2"]["linkedSafe"] == "CyberArk-Services-Accounts"
    assert by_id["v1"]["linkedSafe"] is None  # ProvisionerUser: no se consulta
    assert SECRET_VALUE not in json.dumps(body)


async def test_strong_accounts_se_auditan_con_conteo(env):
    body = env["client"].get("/tenants/t1/sia/strong-accounts").json()
    assert body["databases"][0]["engine"] == "postgres"
    assert body["targetSetsWithoutAccount"][0]["type"] == "Domain"
    assert await _audit(env) == [("sia.strong_accounts.list", "ok", {"count": 2})]


def test_horas_sin_zona_se_marcan_utc():
    # Globex (2026-10-07): la sesión de las 15:07 de Chile salía a las
    # 18:07 porque `startTime` venía en UTC sin la `Z`.
    r = sia.session_row({"startTime": "2026-10-07T18:07:12", "endTime": "2026-10-07T18:07:12Z"})
    assert r["startTime"] == "2026-10-07T18:07:12Z"
    assert r["endTime"] == "2026-10-07T18:07:12Z"  # ya tenía zona: no cambia
    assert sia.session_row({"startTime": "2026-10-07T18:07:12+00:00"})["startTime"].endswith(
        "+00:00"
    )
    assert sia.session_row({})["startTime"] is None
