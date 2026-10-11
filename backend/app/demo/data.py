"""
Datos ficticios de un tenant de Privilege Cloud para el modo demo.

Todo se genera de forma determinista a partir del nombre del tenant (el
`id`), así cada tenant tiene su propio inventario
estable entre reinicios. Los campos siguen la forma de la API REST de
CyberArk que consume `app/cyberark/client.py`.
"""

from __future__ import annotations

import functools
import hashlib
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

DAY = 86_400

# perfil de cada tenant del seed: (cuentas, % de fallas de CPM, sesiones activas)
_PROFILES: dict[str, tuple[int, float, int]] = {
    "globex": (180, 0.08, 6),
    "acme": (120, 0.15, 4),
    "initech": (90, 0.22, 3),
    "umbrella": (140, 0.05, 5),
    "lab": (40, 0.10, 1),
}

_FIRST = [
    "ana",
    "bruno",
    "carla",
    "diego",
    "elena",
    "felipe",
    "gabriela",
    "hugo",
    "irene",
    "javier",
    "karen",
    "luis",
    "marta",
    "nicolas",
    "olga",
    "pablo",
    "rocio",
    "sergio",
    "tamara",
    "victor",
]
_LAST = [
    "rojas",
    "munoz",
    "soto",
    "silva",
    "torres",
    "araya",
    "flores",
    "castro",
    "vega",
    "reyes",
    "morales",
    "herrera",
    "lagos",
    "fuentes",
    "pizarro",
]

# (platformId, nombre, systemType, prefijo de host, usuarios típicos)
_PLATFORMS = [
    ("WinDomain", "Windows Domain Account", "Windows", "srv", ["svc-sql", "svc-backup", "adm-{u}"]),
    (
        "WinServerLocal",
        "Windows Server Local Accounts",
        "Windows",
        "win",
        ["Administrator", "localadmin"],
    ),
    ("UnixSSH", "Unix via SSH", "*NIX", "lnx", ["root", "oracle", "deploy"]),
    ("Oracle", "Oracle Database", "Database", "ora", ["system", "sys", "app_owner"]),
    ("MSSql", "Microsoft SQL Server", "Database", "sql", ["sa", "svc-reporting"]),
    ("CiscoSSH", "Cisco IOS via SSH", "Network Device", "fw", ["admin", "netops"]),
    (
        "AWSAccessKeys",
        "Amazon Web Services Access Keys",
        "Cloud Service",
        "aws",
        ["ci-deployer", "terraform"],
    ),
    ("AzureWebApp", "Azure Web Application", "Cloud Service", "az", ["app-registration"]),
]

_SAFES = [
    ("WIN-Prod", "Servidores Windows de producción"),
    ("WIN-Dev", "Servidores Windows de desarrollo"),
    ("LNX-Prod", "Servidores Linux de producción"),
    ("DB-Prod", "Bases de datos de producción"),
    ("DB-QA", "Bases de datos de QA"),
    ("NET-Core", "Equipos de red del core"),
    ("CLOUD-AWS", "Cuentas y llaves de AWS"),
    ("CLOUD-Azure", "Aplicaciones de Azure"),
    ("SVC-Apps", "Cuentas de servicio de aplicaciones"),
    ("BREAK-GLASS", "Cuentas de emergencia, acceso con doble control"),
]

_CPM_ERRORS = [
    "CACPM410E Failed to verify password. Reason: The password is incorrect (Code: 2114)",
    "CACPM341E Failed to change password. Reason: Network path not found (Code: 53)",
    "CACPM315E Failed to connect to remote machine. Reason: Timeout (Code: 1460)",
    "CACPM117E Logon account is missing or not configured",
    "CACPM504E Password does not meet the complexity policy of the target",
]

_MANUAL_REASONS = [
    "Cuenta de aplicación: la rotación rompe el servicio",
    "Pendiente de validar con el dueño del sistema",
    "Excepción aprobada por el comité de seguridad",
]

_DOMAINS = {
    "globex": "globex.local",
    "acme": "acme.corp",
    "initech": "initech.net",
    "umbrella": "umbrella.lan",
    "lab": "lab.local",
}


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


def _ms(epoch_s: float) -> int:
    return int(epoch_s * 1000)


@dataclass
class TenantData:
    key: str
    accounts: dict[str, dict] = field(default_factory=dict)
    safes: dict[str, dict] = field(default_factory=dict)
    members: dict[str, list[dict]] = field(default_factory=dict)
    platforms: list[dict] = field(default_factory=list)
    target_platforms: list[dict] = field(default_factory=list)
    psm_servers: list[dict] = field(default_factory=list)
    users: dict[str, dict] = field(default_factory=dict)
    groups: dict[str, dict] = field(default_factory=dict)
    live_sessions: list[dict] = field(default_factory=list)
    recordings: list[dict] = field(default_factory=list)
    components: dict[str, list[dict]] = field(default_factory=dict)
    identity_users: list[dict] = field(default_factory=list)
    identity_roles: list[dict] = field(default_factory=list)
    role_members: dict[str, list[dict]] = field(default_factory=dict)
    connectors: list[dict] = field(default_factory=list)
    connector_pools: list[dict] = field(default_factory=list)
    discovered: list[dict] = field(default_factory=list)
    vendors: list[dict] = field(default_factory=list)
    next_id: int = 1000


def _build(key: str) -> TenantData:
    rnd = random.Random(_seed(key))  # noqa: S311 — datos ficticios, no criptografía
    now = time.time()
    n_accounts, fail_rate, n_live = _PROFILES.get(key, (60, 0.1, 2))
    domain = _DOMAINS.get(key, f"{key}.local")
    d = TenantData(key=key)

    # --- safes + miembros
    for i, (name, desc) in enumerate(_SAFES, start=1):
        created = now - rnd.randint(200, 900) * DAY
        d.safes[name] = {
            "safeUrlId": name,
            "safeName": name,
            "safeNumber": 100 + i,
            "description": desc,
            "location": "\\",
            "creator": {"id": "2", "name": "Administrator"},
            "olacEnabled": name == "BREAK-GLASS",
            "managingCPM": "PasswordManager",
            "numberOfVersionsRetention": None,
            "numberOfDaysRetention": 7,
            "autoPurgeEnabled": False,
            "creationTime": int(created),
            "lastModificationTime": _ms(created + rnd.randint(1, 150) * DAY) * 1000,
        }
        full = dict.fromkeys(
            (
                "useAccounts",
                "retrieveAccounts",
                "listAccounts",
                "addAccounts",
                "updateAccountContent",
                "updateAccountProperties",
                "initiateCPMAccountManagementOperations",
                "deleteAccounts",
                "manageSafe",
                "manageSafeMembers",
                "viewAuditLog",
                "viewSafeMembers",
                "accessWithoutConfirmation",
            ),
            True,
        )
        read = dict.fromkeys(("useAccounts", "listAccounts", "viewSafeMembers"), True)
        d.members[name] = [
            {
                "safeUrlId": name,
                "safeName": name,
                "memberId": "2",
                "memberName": "Administrator",
                "memberType": "User",
                "isPredefinedUser": True,
                "membershipExpirationDate": None,
                "permissions": full,
            },
            {
                "safeUrlId": name,
                "safeName": name,
                "memberId": "g1",
                "memberName": "PAM-Admins",
                "memberType": "Group",
                "isPredefinedUser": False,
                "membershipExpirationDate": None,
                "permissions": full,
            },
            {
                "safeUrlId": name,
                "safeName": name,
                "memberId": "g2",
                "memberName": f"Operadores-{name.split('-')[0]}",
                "memberType": "Group",
                "isPredefinedUser": False,
                "membershipExpirationDate": None,
                "permissions": read,
            },
        ]

    # --- plataformas (Get Platforms + Get target platforms) + servidores PSM
    d.psm_servers = [
        {"ID": "PSMServer_A", "Name": "PSM-01"},
        {"ID": "PSMServer_B", "Name": "PSM-02"},
    ]
    for i, (pid, pname, stype, _, _) in enumerate(_PLATFORMS, start=1):
        days = rnd.choice([30, 60, 90])
        d.platforms.append(
            {
                "general": {
                    "id": pid,
                    "name": pname,
                    "systemType": stype,
                    "active": True,
                    "platformType": "Regular",
                    "description": f"Plataforma {pname}",
                    "platformBaseID": pid,
                    "platformBaseType": stype,
                },
                "properties": {
                    "required": [{"name": "Address"}, {"name": "Username"}],
                    "optional": [],
                },
                "credentialsManagement": {
                    "allowManualChange": True,
                    "performPeriodicChange": pid != "AzureWebApp",
                    "requirePasswordChangeEveryXDays": days,
                    "allowManualVerification": True,
                    "performPeriodicVerification": True,
                    "requirePasswordVerificationEveryXDays": 7,
                    "allowManualReconciliation": True,
                    "automaticReconcileWhenUnsynched": pid != "CiscoSSH",
                },
                "sessionManagement": {
                    "requirePrivilegedSessionMonitoringAndIsolation": stype != "Cloud Service",
                    "recordAndSaveSessionActivity": True,
                    "PSMServerID": "PSMServer_A",
                },
                "privilegedAccessWorkflows": {
                    "requireDualControlPasswordAccessApproval": pid == "WinDomain",
                    "requireUsersToSpecifyReasonForAccess": True,
                    "enforceCheckinCheckoutExclusiveAccess": False,
                    "enforceOnetimePasswordAccess": False,
                },
            }
        )
        d.target_platforms.append(
            {
                "ID": i,
                "PlatformID": pid,
                "Name": pname,
                "Active": True,
                "SystemType": stype,
                "AllowedSafes": ".*",
                "PrivilegedSessionManagement": {
                    "PSMServerId": "PSMServer_A" if i % 3 else "PSMServer_B",
                    "PSMServerName": "PSM-01" if i % 3 else "PSM-02",
                },
                "CredentialsManagementPolicy": {
                    "Change": {"PerformAutomatic": True, "RequirePasswordEveryXDays": days}
                },
            }
        )

    # --- cuentas
    safe_by_type = {
        "Windows": ["WIN-Prod", "WIN-Dev", "SVC-Apps"],
        "*NIX": ["LNX-Prod"],
        "Database": ["DB-Prod", "DB-QA"],
        "Network Device": ["NET-Core"],
        "Cloud Service": ["CLOUD-AWS", "CLOUD-Azure"],
    }
    for n in range(1, n_accounts + 1):
        pid, _, stype, prefix, users = rnd.choice(_PLATFORMS)
        safe = rnd.choice(safe_by_type[stype])
        if n % 37 == 0:
            safe = "BREAK-GLASS"
        host = f"{prefix}-{rnd.choice(['prd', 'dev', 'qa'])}-{rnd.randint(1, 40):02d}"
        address = host if stype == "Cloud Service" else f"{host}.{domain}"
        user = rnd.choice(users).format(u=rnd.choice(_FIRST))
        modified = now - rnd.randint(0, 120) * DAY - rnd.randint(0, DAY)
        auto = rnd.random() > 0.07
        failed = auto and rnd.random() < fail_rate
        sm: dict[str, Any] = {
            "automaticManagementEnabled": auto,
            "lastModifiedTime": int(modified),
            "lastVerifiedTime": int(now - rnd.randint(0, 9) * DAY),
        }
        if not auto:
            sm["manualManagementReason"] = rnd.choice(_MANUAL_REASONS)
        if failed:
            sm["status"] = "failure"
            sm["lastReconciledTime"] = int(modified)
        else:
            sm["status"] = "success"
        acc_id = f"{d.safes[safe]['safeNumber']}_{n}"  # mismo formato que CyberArk: <safe>_<n>
        d.accounts[acc_id] = {
            "id": acc_id,
            "name": f"{pid}-{address}-{user}",
            "address": address,
            "userName": user,
            "platformId": pid,
            "safeName": safe,
            "secretType": "key" if pid == "AWSAccessKeys" else "password",
            "platformAccountProperties": {"LogonDomain": domain.split(".")[0].upper()}
            if pid == "WinDomain"
            else {},
            "secretManagement": sm,
            "remoteMachinesAccess": {
                "remoteMachines": "",
                "accessRestrictedToRemoteMachines": False,
            },
            "createdTime": int(modified - rnd.randint(30, 400) * DAY),
            "categoryModificationTime": int(modified),
            "_cpm_error": rnd.choice(_CPM_ERRORS) if failed else None,
        }

    # --- usuarios y grupos del Vault
    for i in range(1, 16):
        fn, ln = rnd.choice(_FIRST), rnd.choice(_LAST)
        uid = str(10 + i)
        d.users[uid] = {
            "id": int(uid),
            "username": f"{fn}.{ln}@{domain}",
            "source": "CyberArkPAM",
            "userType": "EPVUser",
            "componentUser": False,
            "location": "\\",
            "suspended": i % 11 == 0,
            "enableUser": i % 13 != 0,
            "personalDetails": {"firstName": fn.title(), "lastName": ln.title()},
            "lastSuccessfulLoginDate": int(now - rnd.randint(0, 60) * DAY),
            "vaultAuthorization": ["AddSafes", "AuditUsers"] if i < 4 else [],
        }
    for uid, name in (("1", "Administrator"), ("3", "PasswordManager"), ("4", "PSMApp_PSM01")):
        d.users[uid] = {
            "id": int(uid),
            "username": name,
            "source": "CyberArkPAM",
            "userType": "Built-InAdmins" if uid == "1" else "CPM",
            "componentUser": uid != "1",
            "location": "\\",
            "suspended": False,
            "enableUser": True,
            "lastSuccessfulLoginDate": int(now - 3600),
        }
    for gid, gname, desc in (
        ("g1", "PAM-Admins", "Administradores de la bóveda"),
        ("g2", "Operadores", "Operación diaria"),
        ("g3", "Auditores", "Sólo lectura de auditoría"),
    ):
        d.groups[gid] = {
            "id": gid,
            "groupName": gname,
            "description": desc,
            "directory": domain,
            "groupType": "Vault",
            "location": "\\",
            "members": [
                {"id": u["id"], "username": u["username"]} for u in list(d.users.values())[:3]
            ],
        }

    # --- sesiones activas y grabaciones
    accs = list(d.accounts.values())
    for i in range(n_live):
        a = rnd.choice(accs)
        u = rnd.choice(list(d.users.values()))
        start = now - rnd.randint(60, 7200)
        d.live_sessions.append(
            {
                "SessionID": str(uuid.UUID(int=rnd.getrandbits(128))),
                "SessionGuid": f"live-{i}",
                "User": u["username"],
                "RemoteMachine": a["address"],
                "AccountUsername": a["userName"],
                "AccountPlatformID": a["platformId"],
                "AccountAddress": a["address"],
                "Protocol": "RDP" if "Win" in a["platformId"] else "SSH",
                "Start": int(start),
                "Duration": int(now - start),
                "ConnectionComponentID": "PSM-RDP",
                "CanTerminate": True,
                "Client": "PSM",
                "FromIP": f"10.20.{rnd.randint(1, 9)}.{rnd.randint(2, 250)}",
            }
        )
    for i in range(25):
        a = rnd.choice(accs)
        u = rnd.choice(list(d.users.values()))
        start = now - rnd.randint(1, 30) * DAY - rnd.randint(0, DAY)
        dur = rnd.randint(120, 5400)
        d.recordings.append(
            {
                "SessionID": f"rec-{key}-{i}",
                "SessionGuid": f"rec-{key}-{i}",
                "SafeName": "PSMRecordings",
                "FolderName": "Root",
                "FileName": f"{i}.avi",
                "Start": int(start),
                "End": int(start + dur),
                "Duration": dur,
                "User": u["username"],
                "RemoteMachine": a["address"],
                "AccountUsername": a["userName"],
                "AccountPlatformID": a["platformId"],
                "AccountAddress": a["address"],
                "Protocol": "RDP" if "Win" in a["platformId"] else "SSH",
                "Client": "PSM",
                "RiskScore": round(rnd.random() * 100, 1),
                "Severity": rnd.choice(["Low", "Low", "Medium", "High"]),
                "RecordingFiles": [
                    {
                        "FileName": f"{i}.avi",
                        "Format": "AVI",
                        "FileSize": rnd.randint(1, 90) * 10**6,
                    }
                ],
            }
        )

    # --- componentes (System Health): una instancia caída en los tenants con más fallas
    def inst(ip: str, user: str, up: bool = True) -> dict:
        return {
            "ComponentIP": ip,
            "ComponentUserName": user,
            "ComponentVersion": "14.4.0",
            "IsLoggedOn": up,
            "LastLogonDate": int(now - (60 if up else 3 * DAY)),
        }

    degraded = fail_rate > 0.2
    d.components = {
        "PVWA": [inst("10.0.1.11", "PVWAAppUser"), inst("10.0.1.12", "PVWAAppUser")],
        "SessionManagement": [
            inst("10.0.2.21", "PSMApp_PSM01"),
            inst("10.0.2.22", "PSMApp_PSM02", up=not degraded),
        ],
        "CPM": [inst("10.0.3.31", "PasswordManager"), inst("10.0.3.32", "PasswordManager1")],
        "AIM": [],
    }

    # --- Identity
    for i in range(1, 21):
        fn, ln = rnd.choice(_FIRST), rnd.choice(_LAST)
        d.identity_users.append(
            {
                "ID": str(uuid.UUID(int=rnd.getrandbits(128))),
                "Username": f"{fn}.{ln}@{domain}",
                "DisplayName": f"{fn.title()} {ln.title()}",
                "Email": f"{fn}.{ln}@{domain}",
                "UserType": "User",
                "Status": "Active" if i % 9 else "Suspended",
                "StatusEnum": "Active" if i % 9 else "Suspended",
                "SourceDs": "CDS",
                "SourceDsLocalized": "CyberArk Cloud Directory",
                "SourceDsType": "CDS",
                "LastLogin": f"/Date({_ms(now - rnd.randint(0, 45) * DAY)})/",
                "LastInvite": None,
            }
        )
    for name, desc in (
        ("System Administrator", "Administración completa de Identity"),
        ("Privilege Cloud Administrators", "Administradores de Privilege Cloud"),
        ("Privilege Cloud Users", "Usuarios finales de Privilege Cloud"),
        ("PAM-Operadores", "Equipo de operación PAM"),
        ("Acceso GCP Producción", "Acceso a la consola de GCP"),
    ):
        rid = str(uuid.UUID(int=rnd.getrandbits(128)))
        d.identity_roles.append(
            {
                "ID": rid,
                "Name": name,
                "Description": desc,
                "RoleType": "PrincipalList",
                "Organization": None,
            }
        )
        d.role_members[rid] = [
            {"Guid": u["ID"], "Name": u["Username"], "Type": "User"}
            for u in rnd.sample(d.identity_users, 4)
        ]

    # --- Connector Management + Discovery + Alero
    d.connector_pools = [
        {"poolId": "pool-1", "name": f"{key.title()}_DC", "description": "Datacenter principal"},
        {"poolId": "pool-2", "name": f"{key.title()}_Cloud", "description": "Nube pública"},
    ]
    for i in range(1, 4):
        d.connectors.append(
            {
                "connectorId": f"conn-{i}",
                "connectorPoolId": "pool-1" if i < 3 else "pool-2",
                "hostName": f"CONNECTOR-{i:02d}",
                "hostIp": f"10.0.9.{10 + i}",
                "os": "Windows",
                "version": "14.4.0",
                "status": "Active" if i != 3 or not degraded else "Inactive",
                "updatedAt": _ms(now - 120),
                "components": [
                    {"type": t, "version": "14.4.0", "status": "Active"}
                    for t in ("ManagementAgent", "CPM", "PSM")
                ],
            }
        )
    for i in range(1, 9):
        host = f"srv-new-{i:02d}.{domain}"
        d.discovered.append(
            {
                "id": f"disc-{i}",
                "username": rnd.choice(["Administrator", "svc-legacy", "backup"]),
                "address": host,
                "platformType": "Windows Server Local",
                "accountType": "privileged",
                "source": "Windows Server",
                "status": "pending",
                "discoveryDate": _ms(now - i * DAY),
                "lastLogonDate": _ms(now - rnd.randint(1, 90) * DAY),
                "privileged": True,
            }
        )
    for i in range(1, 7):
        fn, ln = rnd.choice(_FIRST), rnd.choice(_LAST)
        d.vendors.append(
            {
                "id": f"vendor-{i}",
                "firstName": fn.title(),
                "lastName": ln.title(),
                "email": f"{fn}.{ln}@proveedor{i}.example",
                "phoneNumber": None,
                "companyName": f"Proveedor {i} S.A.",
                "status": "Activated" if i % 4 else "Suspended",
                "invitedBy": "Administrator",
                "createdAt": _ms(now - rnd.randint(30, 300) * DAY),
                "lastLogin": _ms(now - rnd.randint(0, 40) * DAY),
                "applications": [],
            }
        )
    return d


_STATE: dict[str, TenantData] = {}


def tenant_data(key: str) -> TenantData:
    """Estado (mutable, en memoria) del tenant demo `key`."""
    if key not in _STATE:
        _STATE[key] = _build(key)
    return _STATE[key]


@functools.lru_cache(maxsize=1)
def alero_private_key_pem() -> str:
    """Clave RSA efímera para firmar el JWT de Alero en modo demo (el
    transport mock no la verifica, pero `AleroClient` sí necesita una válida
    para armar el assertion)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
