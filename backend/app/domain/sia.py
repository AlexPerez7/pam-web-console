"""SIA (Secure Infrastructure Access), sólo lectura: funciones puras que
normalizan las respuestas de CyberArk a una forma común para la grilla y el
detalle. Ver docs/specs/done/sia-read-only.md.

Las formas salen del SDK oficial (`cyberark/ark-sdk-python`) y todavía no se
confirmaron contra un tenant real, así que todo se lee con `_g`, que acepta la
clave en camelCase o en snake_case (el SDK usa ambas según el servicio).

Secretos: se arman campo por campo desde una **lista blanca**. Nunca se copia
el dict crudo, así un campo nuevo de CyberArk con el valor del secreto no
puede llegar al frontend, al audit log ni a la caché.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

SIA_APP_CODES = frozenset({"DPA", "SIA"})

_STATUS = {
    "active": "active",
    "enabled": "active",
    "suspended": "suspended",
    "disabled": "suspended",
    "expired": "expired",
    "draft": "draft",
    "validating": "validating",
    "error": "error",
    "warning": "warning",
}


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _camel(key: str) -> str:
    if "_" not in key:
        return key.lower() if key.isupper() else key[:1].lower() + key[1:]
    head, *rest = key.lower().split("_")
    return head + "".join(p.capitalize() for p in rest)


def _g(d: Any, key: str, default: Any = None) -> Any:
    """`d[key]` aceptando camelCase o snake_case (`key` va en camelCase)."""
    if not isinstance(d, dict):
        return default
    for k in (key, _snake(key)):
        if k in d and d[k] is not None:
            return d[k]
    return default


_NAIVE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")


def _utc(v: Any) -> Any:
    """Las fechas de CyberArk vienen en UTC, a veces SIN zona
    (`2026-10-07T18:07:00`): el navegador las tomaba como hora local y en
    Chile las sesiones salían 3 h corridas (Globex, 2026-10-07). Se les
    agrega la `Z`; una fecha con zona o que no es ISO pasa tal cual."""
    if isinstance(v, str) and _NAIVE_ISO.match(v.strip()):
        return v.strip().replace(" ", "T") + "Z"
    return v


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _name(v: Any) -> str:
    """Un principal puede venir como string o como objeto con `name`."""
    if isinstance(v, dict):
        return str(_g(v, "name") or _g(v, "id") or "")
    return str(v)


def _status(raw: Any) -> str:
    if isinstance(raw, dict):  # UAP: {"status": "Active", "statusCode": ...}
        raw = _g(raw, "status")
    return _STATUS.get(str(raw or "").lower(), "other")


# ------------------------------------------------------------------ #
# Datos sensibles en el JSON crudo de políticas (que se muestra en el
# detalle mientras no se confirme la forma real). Las políticas no deberían
# traer secretos; esto es una red de seguridad.
# ------------------------------------------------------------------ #
_SENSITIVE_KEY = re.compile(
    r"password|secretdata|secret_data|privatekey|private_key|token$|^secret$"
)


def strip_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: strip_sensitive(v)
            for k, v in value.items()
            if not _SENSITIVE_KEY.search(str(k).lower())
        }
    if isinstance(value, list):
        return [strip_sensitive(v) for v in value]
    return value


# ------------------------------------------------------------------ #
# Políticas
# ------------------------------------------------------------------ #
def _uap_principals(p: dict) -> list[dict]:
    return [
        {
            "type": str(_g(x, "type") or "").lower() or "user",
            "name": _name(x),
            "source": _g(x, "sourceDirectoryName"),
        }
        for x in _list(_g(p, "principals"))
    ]


def _change(meta: dict, key: str) -> tuple[Any, Any]:
    info = _g(meta, key) or {}
    return _g(info, "time"), _g(info, "user")


def uap_policy_row(p: dict, kind: str) -> dict:
    meta = _g(p, "metadata") or {}
    tf = _g(meta, "timeFrame") or {}
    ent = _g(meta, "policyEntitlement") or {}
    cond = _g(p, "conditions") or {}
    location = _g(ent, "locationType")
    created_at, created_by = _change(meta, "createdBy")
    updated_at, updated_by = _change(meta, "updatedOn")
    return {
        "id": str(_g(meta, "policyId") or ""),
        "source": "uap",
        "kind": kind,
        "name": _g(meta, "name") or "",
        "description": _g(meta, "description") or "",
        "status": _status(_g(meta, "status")),
        "statusDetail": _g(_g(meta, "status"), "statusDescription"),
        "policyType": _g(ent, "policyType"),
        "validFrom": _g(tf, "fromTime"),
        "validTo": _g(tf, "toTime"),
        "timeZone": _g(meta, "timeZone"),
        "locations": [location] if location else [],
        "tags": [str(t) for t in _list(_g(meta, "policyTags"))],
        "principalsCount": len(_list(_g(p, "principals"))) if "principals" in p else None,
        "rulesCount": 1,
        "maxSessionHours": _g(cond, "maxSessionDuration"),
        "createdAt": _utc(created_at),
        "createdBy": created_by,
        "modifiedAt": _utc(updated_at or created_at),
        "modifiedBy": updated_by or created_by,
    }


def _legacy_principals(user_data: Any) -> list[dict]:
    out: list[dict] = []
    for kind, key in (("user", "users"), ("group", "groups"), ("role", "roles")):
        for x in _list(_g(user_data, key)):
            out.append(
                {
                    "type": kind,
                    "name": _name(x),
                    "source": _g(x, "source") if isinstance(x, dict) else None,
                }
            )
    return out


def legacy_policy_row(p: dict, kind: str) -> dict:
    locations = _list(_g(p, "platforms")) or _list(_g(p, "providers"))
    rules = _list(_g(p, "userAccessRules"))
    rule_names = _list(_g(p, "ruleNames"))
    principals = sum(len(_legacy_principals(_g(r, "userData"))) for r in rules)
    return {
        "id": str(_g(p, "policyId") or ""),
        "source": "legacy",
        "kind": kind,
        "name": _g(p, "policyName") or "",
        "description": _g(p, "description") or "",
        "status": _status(_g(p, "status")),
        "validFrom": _g(p, "startDate"),
        "validTo": _g(p, "endDate"),
        "locations": [str(x) for x in locations],
        "statusDetail": None,
        "policyType": None,
        "timeZone": None,
        "tags": [str(t) for t in _list(_g(p, "providersTags"))],
        "principalsCount": principals if rules else None,
        "rulesCount": len(rules) if rules else len(rule_names),
        "maxSessionHours": None,
        "createdAt": _utc(_g(p, "createdOn")),
        "createdBy": _g(p, "createdBy"),
        "modifiedAt": _utc(_g(p, "updatedOn")),
        "modifiedBy": _g(p, "updatedBy"),
    }


_DAYS_NUM = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
_DAYS_EN = {
    "Mon": "Lun",
    "Tue": "Mar",
    "Wed": "Mié",
    "Thu": "Jue",
    "Fri": "Vie",
    "Sat": "Sáb",
    "Sun": "Dom",
}


def _days(values: list) -> list[str]:
    out = []
    for d in values:
        if isinstance(d, int) and 0 <= d <= 6:
            out.append(_DAYS_NUM[d])
        else:
            out.append(_DAYS_EN.get(str(d), str(d)))
    return out


def _describe(value: Any, prefix: str = "") -> list[str]:
    """Aplana un bloque de targets/conexión a líneas `ruta: valor` legibles
    (la forma exacta varía por proveedor y no está confirmada)."""
    lines: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            lines.extend(_describe(v, f"{prefix}{k} > "))
    elif isinstance(value, list):
        if all(not isinstance(v, dict | list) for v in value):
            if value:
                lines.append(f"{prefix.rstrip(' >')}: {', '.join(str(v) for v in value)}")
        else:
            for v in value:
                lines.extend(_describe(v, prefix))
    elif value not in (None, "", {}):
        lines.append(f"{prefix.rstrip(' >')}: {value}")
    return lines


def _key_values(items: Any) -> list[dict]:
    """Tags/labels de nube: `[{key, value: [..]}]`."""
    return [
        {"key": str(_g(t, "key") or ""), "values": [str(v) for v in _list(_g(t, "value"))]}
        for t in _list(items)
        if isinstance(t, dict)
    ]


_CLOUD_FIELDS = {
    "AWS": (("regions", "Regiones"), ("vpcIds", "VPC"), ("accountIds", "Cuentas")),
    "Azure": (
        ("regions", "Regiones"),
        ("resourceGroups", "Grupos de recursos"),
        ("vnetIds", "VNet"),
        ("subscriptions", "Suscripciones"),
    ),
    "GCP": (("regions", "Regiones"), ("vpcIds", "VPC"), ("projects", "Proyectos")),
}


def _uap_targets(p: dict, kind: str) -> list[dict]:
    """Targets de una política UAP, por ubicación. Formas del SDK: VM
    `{"FQDN/IP": {fqdnRules, ipRules}}` o `{"AWS"|"Azure"|"GCP": {...}}`; DB
    `{"FQDN/IP": {instances: [...]}}`. Una forma desconocida va como `other`
    (líneas legibles), nunca se descarta."""
    out: list[dict] = []
    targets = strip_sensitive(_g(p, "targets") or {})
    if not isinstance(targets, dict):
        return out
    for location, block in targets.items():
        if not isinstance(block, dict):
            continue
        if kind == "db" or "instances" in block:
            out.append(
                {
                    "location": location,
                    "type": "db",
                    "instances": [
                        {
                            "name": _g(i, "instanceName") or "",
                            "type": _g(i, "instanceType"),
                            "id": _g(i, "instanceId"),
                            "authMethod": _g(i, "authenticationMethod"),
                            "permissions": _describe(_g(i, "profile") or {}),
                        }
                        for i in _list(_g(block, "instances"))
                    ],
                }
            )
        elif location in _CLOUD_FIELDS:
            out.append(
                {
                    "location": location,
                    "type": "cloud",
                    "fields": [
                        {"label": label, "values": [str(v) for v in _list(_g(block, key))]}
                        for key, label in _CLOUD_FIELDS[location]
                    ],
                    "tags": _key_values(_g(block, "tags") or _g(block, "labels")),
                }
            )
        elif _g(block, "fqdnRules") is not None or _g(block, "ipRules") is not None:
            out.append(
                {
                    "location": location,
                    "type": "fqdn_ip",
                    "fqdnRules": [
                        {
                            "operator": _g(r, "operator"),
                            "pattern": _g(r, "computernamePattern"),
                            "domain": _g(r, "domain"),
                        }
                        for r in _list(_g(block, "fqdnRules"))
                    ],
                    "ipRules": [
                        {
                            "operator": _g(r, "operator"),
                            "ips": [str(x) for x in _list(_g(r, "ipAddresses"))],
                            "logicalName": _g(r, "logicalName"),
                        }
                        for r in _list(_g(block, "ipRules"))
                    ],
                }
            )
        else:
            out.append({"location": location, "type": "other", "lines": _describe(block)})
    return out


def _uap_vm_permissions(p: dict) -> dict:
    """`behavior.connectAs.{ssh, rdp}` de una política de VM."""
    connect = _g(_g(p, "behavior") or {}, "connectAs") or {}
    ssh = _g(connect, "ssh")
    rdp = _g(connect, "rdp")
    out: dict = {"ssh": None, "rdp": None}
    if isinstance(ssh, dict):
        out["ssh"] = {"username": _g(ssh, "username")}
    if isinstance(rdp, dict):
        local = _g(rdp, "localEphemeralUser")
        domain = _g(rdp, "domainEphemeralUser")
        mode = (
            "domain" if isinstance(domain, dict) else "local" if isinstance(local, dict) else None
        )
        user = domain if mode == "domain" else local if mode == "local" else {}
        out["rdp"] = {
            "mode": mode,
            "groups": [str(g) for g in _list(_g(user, "assignGroups"))],
            "domainGroups": [str(g) for g in _list(_g(user, "assignDomainGroups"))],
            "reconnect": _g(user, "enableEphemeralUserReconnect"),
        }
    return out


def uap_policy_detail(p: dict, kind: str) -> dict:
    row = uap_policy_row(p, kind)
    cond = _g(p, "conditions") or {}
    window = _g(cond, "accessWindow") or {}
    principals = _uap_principals(p)
    conditions = {
        "days": _days(_list(_g(window, "daysOfTheWeek"))),
        "fromHour": _g(window, "fromHour"),
        "toHour": _g(window, "toHour"),
        "maxSessionHours": _g(cond, "maxSessionDuration"),
        "idleMinutes": _g(cond, "idleTime"),
    }
    rule = conditions | {
        "name": row["name"],
        "principals": principals,
        "timeZone": row["timeZone"],
        "connectAs": _describe(strip_sensitive(_g(p, "behavior") or {})),
    }
    return row | {
        "principalsCount": len(principals),
        "principals": principals,
        "rules": [rule],
        "targets": _describe(strip_sensitive(_g(p, "targets") or {})),
        "targetGroups": _uap_targets(p, kind),
        "permissions": _uap_vm_permissions(p) if kind == "vm" else None,
        "conditions": conditions,
        "raw": strip_sensitive(p),
    }


def legacy_policy_detail(p: dict, kind: str) -> dict:
    row = legacy_policy_row(p, kind)
    rules = []
    principals: list[dict] = []
    for r in _list(_g(p, "userAccessRules")):
        ci = _g(r, "connectionInformation") or {}
        rp = _legacy_principals(_g(r, "userData"))
        principals.extend(rp)
        tz = _g(ci, "timeZone")
        rules.append(
            {
                "name": _g(r, "ruleName") or "",
                "principals": rp,
                "days": _days(_list(_g(ci, "daysOfWeek"))),
                "fromHour": None if _g(ci, "fullDays") else _g(ci, "hoursFrom"),
                "toHour": None if _g(ci, "fullDays") else _g(ci, "hoursTo"),
                "timeZone": tz if isinstance(tz, str) else (_g(tz, "name") if tz else None),
                "maxSessionHours": _g(ci, "grantAccess"),
                "idleMinutes": _g(ci, "idleTime"),
                "connectAs": _describe(strip_sensitive(_g(ci, "connectAs") or {})),
            }
        )
    targets = _describe(strip_sensitive(_g(p, "providersData") or {}))
    return row | {
        "principalsCount": len(principals),
        "rulesCount": len(rules),
        "principals": principals,
        "rules": rules,
        "targets": targets,
        "targetGroups": [],
        "permissions": None,
        "conditions": None,
        "raw": strip_sensitive(p),
    }


# ------------------------------------------------------------------ #
# Sesiones (Session Monitoring)
# ------------------------------------------------------------------ #
_HMS = re.compile(r"^(?:(\d+)\s+days?,\s*)?(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?$")


def duration_seconds(v: Any) -> int | None:
    """`sessionDuration` puede venir en segundos, como `HH:MM:SS`
    (`timedelta` serializado) o en ISO 8601 (`PT1H2M3S`)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        return int(v)
    s = str(v).strip()
    m = _HMS.match(s)
    if m:
        d, h, mi, se = (int(x) if x else 0 for x in m.groups())
        return int(timedelta(days=d, hours=h, minutes=mi, seconds=se).total_seconds())
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", s)
    if m and any(m.groups()):
        fd, fh, fm, fs = (float(x) if x else 0.0 for x in m.groups())
        return int(fd * 86400 + fh * 3600 + fm * 60 + fs)
    return None


def is_sia_session(s: dict) -> bool:
    """Session Monitoring es compartido: se queda sólo lo de SIA. Una sesión
    sin `applicationCode` se muestra (nunca se oculta algo por no conocerlo)."""
    code = _g(s, "applicationCode")
    return code is None or str(code).upper() in SIA_APP_CODES


def session_row(s: dict) -> dict:
    return {
        "id": str(_g(s, "sessionId") or ""),
        "status": str(_g(s, "sessionStatus") or ""),
        "user": _g(s, "user"),
        "source": _g(s, "source"),
        "target": _g(s, "target"),
        "targetUsername": _g(s, "targetUsername"),
        "protocol": _g(s, "protocol"),
        "platform": _g(s, "platform"),
        "applicationCode": _g(s, "applicationCode"),
        "accessMethod": _g(s, "accessMethod"),
        "startTime": _utc(_g(s, "startTime")),
        "endTime": _utc(_g(s, "endTime")),
        "durationSeconds": duration_seconds(_g(s, "sessionDuration")),
        "endReason": _g(s, "endReason"),
        "errorCode": _g(s, "errorCode"),
        "isRecording": _g(s, "isRecording"),
    }


def activity_row(a: dict) -> dict:
    return {
        "id": str(_g(a, "uuid") or ""),
        "timestamp": _utc(_g(a, "timestamp")),
        "username": _g(a, "username"),
        "action": _g(a, "action"),
        "actionType": _g(a, "actionType"),
        "command": _g(a, "command"),
        "target": _g(a, "target"),
        "message": _g(a, "message"),
    }


# ------------------------------------------------------------------ #
# Targets
# ------------------------------------------------------------------ #
def db_target_row(d: dict) -> dict:
    prov = _g(d, "providerInfo") or {}
    services = [_name(x) for x in _list(_g(d, "services"))]
    return {
        "id": str(_g(d, "id") or ""),
        "name": _g(d, "name") or "",
        "engine": _g(prov, "engine") or _g(prov, "family") or _g(prov, "name"),
        "family": _g(prov, "family"),
        "platform": _g(d, "platform"),
        "endpoint": _g(d, "readWriteEndpoint"),
        "port": _g(d, "port"),
        "services": [s for s in services if s],
        "authMethod": _g(d, "configuredAuthMethodType"),
        "secretId": _g(d, "secretId"),
        "certificateValidation": _g(d, "enableCertificateValidation"),
    }


def target_set_row(t: dict) -> dict:
    return {
        "name": _g(t, "name") or "",
        "type": _g(t, "type"),
        "description": _g(t, "description") or "",
        "provisionFormat": _g(t, "provisionFormat"),
        "secretType": _g(t, "secretType"),
        "secretId": _g(t, "secretId"),
        "certificateValidation": _g(t, "enableCertificateValidation"),
    }


# ------------------------------------------------------------------ #
# Secretos — LISTA BLANCA, campo por campo
# ------------------------------------------------------------------ #
def db_secret_row(s: dict) -> dict:
    store = _g(s, "secretStore") or {}
    exposed = _g(s, "secretExposedData") or {}
    link = _g(s, "secretLink") or {}
    return {
        "id": str(_g(s, "secretId") or ""),
        "kind": "db",
        "name": _g(s, "secretName") or "",
        "type": _g(s, "secretType"),
        "description": _g(s, "description") or "",
        "store": _g(store, "storeType"),
        "username": _g(exposed, "username"),
        "account": _g(exposed, "account"),
        "linkedSafe": _g(link, "safe"),
        "linkedAccount": _g(link, "accountName"),
        "active": bool(_g(s, "isActive", True)),
        "createdBy": _g(s, "createdBy"),
        "createdAt": _utc(_g(s, "creationTime")),
        "updatedBy": _g(s, "lastUpdatedBy"),
        "updatedAt": _utc(_g(s, "lastUpdateTime")),
    }


def _details(raw: Any) -> dict:
    """`secret_details` viene como objeto o como JSON en un string (el SDK
    en Go contempla las dos formas)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def secret_id(s: dict) -> str:
    return str(_g(s, "secretId") or "")


def secret_type(s: dict) -> str:
    return str(_g(s, "secretType") or "")


def _pcloud_ref(s: dict, resolved: dict | None = None) -> dict:
    """Safe y nombre de la cuenta de Privilege Cloud a la que apunta una
    strong account `PCloudAccount`. En Globex la lista no los trae: vienen
    de `retrieve_pcloud_account_by_admin` (`resolved`). Si no, pueden venir
    en `secret.secret_data` (que en un `ProvisionerUser` trae la
    CONTRASEÑA): de cualquiera de los dos se leen SÓLO estas tres claves."""
    data = resolved or _g(_g(s, "secret") or {}, "secretData")
    if not isinstance(data, dict):
        return {}
    return {
        "safe": _g(data, "safe"),
        "accountName": _g(data, "accountName"),
        "username": _g(data, "username"),
    }


def vm_secret_row(s: dict, pcloud: dict | None = None) -> dict:
    """Strong account de VM. Formas de `cyberark/idsec-sdk-golang`
    (`IdsecSIAVMSecret`): `secret_details = {account_domain,
    ephemeral_domain_user_data: {domain_controller: {...},
    ephemeral_domain_user_location, winrm_info: {...}}}`."""
    details = _details(_g(s, "secretDetails"))
    eph = _g(details, "ephemeralDomainUserData") or {}
    dc = _g(eph, "domainController") or {}
    winrm = _g(eph, "winrmInfo") or {}
    ref = _pcloud_ref(s, pcloud)
    stype = _g(s, "secretType")
    domain = _g(details, "accountDomain") or "local"
    return {
        "id": str(_g(s, "secretId") or ""),
        "kind": "vm",
        "name": _g(s, "secretName") or "",
        "type": stype,
        "description": "",
        "store": "PCloud" if stype == "PCloudAccount" else "SIA",
        "domain": domain,
        "accountType": "local" if str(domain).lower() == "local" else "domain",
        "username": ref.get("username") or _g(details, "username"),
        "account": None,
        "linkedSafe": ref.get("safe") or _g(details, "safe"),
        "linkedAccount": ref.get("accountName") or _g(details, "accountName"),
        "active": bool(_g(s, "isActive", True)),
        "rotatable": _g(s, "isRotatable"),
        "ephemeralDomainUsers": bool(eph),
        "ephemeralUserLocation": _g(eph, "ephemeralDomainUserLocation") or None,
        "domainController": _g(dc, "domainControllerName") or None,
        "domainControllerNetbios": _g(dc, "domainControllerNetbios") or None,
        "domainControllerLdaps": _g(dc, "domainControllerUseLdaps"),
        "domainControllerCertValidation": _g(dc, "domainControllerEnableCertificateValidation"),
        "winrmHttps": _g(winrm, "useWinrmForHttps"),
        "winrmCertValidation": _g(winrm, "winrmEnableCertificateValidation"),
        "createdBy": None,
        "createdAt": _utc(_g(s, "creationTime")),
        "updatedBy": None,
        "updatedAt": _utc(_g(s, "lastModified")),
    }


def strong_accounts(
    vm_secrets: list[dict],
    target_sets: list[dict],
    db_secrets: list[dict],
    db_resources: list[dict],
    pcloud_accounts: dict[str, dict | None] | None = None,
) -> dict:
    """La página "Strong accounts" del portal: cuentas fuertes de VM con los
    target sets que las usan ("Manage target set items") y de DB con las
    bases que las usan. Lo que apunta a un secreto que no está en la lista
    va aparte, para no esconderlo."""
    pcloud_accounts = pcloud_accounts or {}
    vm = [vm_secret_row(x, pcloud_accounts.get(secret_id(x))) for x in vm_secrets]
    db = [db_secret_row(x) for x in db_secrets]
    sets = [target_set_row(t) for t in target_sets]
    dbs = [db_target_row(d) for d in db_resources]
    vm_ids = {r["id"] for r in vm}
    db_ids = {r["id"] for r in db}
    for r in vm:
        r["targetSets"] = [t for t in sets if t["secretId"] == r["id"]]
    for r in db:
        r["databases"] = [d for d in dbs if d["secretId"] == r["id"]]
    vm.sort(key=lambda r: str(r["name"]).lower())
    db.sort(key=lambda r: str(r["name"]).lower())
    return {
        "vm": vm,
        "db": db,
        "databases": dbs,
        "targetSetsWithoutAccount": [t for t in sets if t["secretId"] not in vm_ids],
        "databasesWithoutAccount": [d for d in dbs if d["secretId"] not in db_ids],
    }


# ------------------------------------------------------------------ #
# Configuración del servicio
# ------------------------------------------------------------------ #
_GROUP_LABELS = {
    "adbMfaCaching": "Caché de MFA — bases de datos",
    "certificateValidation": "Validación de certificados",
    "k8sMfaCaching": "Caché de MFA — Kubernetes",
    "rdpFileTransfer": "RDP — transferencia de archivos",
    "rdpKeyboardLayout": "RDP — distribución de teclado",
    "rdpMfaCaching": "Caché de MFA — RDP",
    "rdpTokenMfaCaching": "Caché de MFA por token — RDP",
    "rdpRecording": "RDP — grabación",
    "sshMfaCaching": "Caché de MFA — SSH",
    "sshCommandAudit": "SSH — auditoría de comandos",
    "standingAccess": "Acceso permanente",
    "logonSequence": "Secuencia de inicio de sesión",
    # Claves vistas en Globex (2026-10-07) que no estaban en el SDK.
    "connectViaBrowser": "Conexión desde el navegador",
    "granularEnabled": "Permisos granulares",
    "httpsRelay": "Relay HTTPS",
    "mfaCaching": "Caché de MFA — general",
    "oracleConnectionProtocol": "Oracle — protocolo de conexión",
    "oracleOud": "Oracle — OUD",
}

_FIELD_LABELS = {
    "isMfaCachingEnabled": "Caché de MFA habilitada",
    "keyExpirationTimeSec": "Vigencia de la clave (segundos)",
    "clientIpEnforced": "Exige la misma IP de cliente",
    "tokenUsageCount": "Usos por token",
    "enabled": "Habilitado",
    "layout": "Distribución",
    "isCommandParsingForAuditEnabled": "Auditoría de comandos habilitada",
    "shellPromptForAudit": "Prompt del shell para la auditoría",
    "standingAccessAvailable": "Acceso permanente disponible",
    "sessionMaxDuration": "Duración máxima de la sesión",
    "sessionIdleTime": "Tiempo máximo de inactividad",
    "fingerprintValidation": "Valida la huella del host",
    "sshStandingAccessAvailable": "Acceso permanente por SSH",
    "rdpStandingAccessAvailable": "Acceso permanente por RDP",
    "adbStandingAccessAvailable": "Acceso permanente a bases de datos",
    "logonSequence": "Secuencia",
    "alwaysUseSia": "Usar siempre SIA",
    "isGranularEnabled": "Permisos granulares habilitados",
    "isHttpsRelayEnabled": "Relay HTTPS habilitado",
    "relayHost": "Host del relay",
    "sshRelayPort": "Puerto SSH del relay",
    "targetProtocolType": "Protocolo",
    "oudPort": "Puerto de OUD",
}


def _scalar(v: Any) -> bool:
    return isinstance(v, str | int | float | bool)


def settings_groups(raw: dict) -> list[dict]:
    """Settings agrupados por función, con etiquetas en español. Una clave
    desconocida se muestra con su nombre original (nunca se oculta)."""
    groups = []
    for key, value in strip_sensitive(raw).items():
        gkey = _camel(str(key))
        if isinstance(value, dict):
            fields = [
                {"key": _camel(fk), "label": _FIELD_LABELS.get(_camel(fk), fk), "value": fv}
                for fk, fv in value.items()
                if _scalar(fv)
            ]
        elif _scalar(value):
            fields = [{"key": gkey, "label": _FIELD_LABELS.get(gkey, str(key)), "value": value}]
        else:
            continue
        if fields:
            groups.append(
                {"key": gkey, "label": _GROUP_LABELS.get(gkey, str(key)), "fields": fields}
            )
    groups.sort(key=lambda g: str(g["label"]))
    return groups
