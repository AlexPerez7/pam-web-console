"""Permisos de miembro de safe: catálogo canónico, agrupación para la UI y
presets de onboarding.

Fuente de verdad de los flags de `permissions` de la API Gen2. Lo consumen
`app.domain.safes` (armar el body de AddSafeMember) y
`app.reports.safe_permissions` (columnas del reporte). El frontend tiene una
copia chica de los labels/presets para la UI (ver `lib/permissions.ts`).
"""

from __future__ import annotations

# (key de la API Gen2, label corto). Orden = orden de columnas del reporte.
PERMISSION_KEYS: list[tuple[str, str]] = [
    ("useAccounts", "Use"),
    ("retrieveAccounts", "Retrieve"),
    ("listAccounts", "List"),
    ("addAccounts", "Add"),
    ("updateAccountContent", "Update contenido"),
    ("updateAccountProperties", "Update props"),
    ("initiateCPMAccountManagementOperations", "CPM on-demand"),
    ("specifyNextAccountContent", "Set next"),
    ("renameAccounts", "Rename"),
    ("deleteAccounts", "Delete"),
    ("unlockAccounts", "Unlock"),
    ("manageSafe", "Manage safe"),
    ("manageSafeMembers", "Manage members"),
    ("backupSafe", "Backup"),
    ("viewAuditLog", "Ver audit"),
    ("viewSafeMembers", "Ver members"),
    ("accessWithoutConfirmation", "Sin confirmación"),
    ("createFolders", "Crear carpetas"),
    ("deleteFolders", "Borrar carpetas"),
    ("moveAccountsAndFolders", "Mover"),
    ("requestsAuthorizationLevel1", "Aprobar N1"),
    ("requestsAuthorizationLevel2", "Aprobar N2"),
]

ALL_KEYS: tuple[str, ...] = tuple(k for k, _ in PERMISSION_KEYS)

# agrupación para los checkboxes del form (mismo orden que PERMISSION_KEYS)
PERMISSION_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Acceso",
        ["useAccounts", "retrieveAccounts", "listAccounts", "accessWithoutConfirmation"],
    ),
    (
        "Gestión de cuentas",
        [
            "addAccounts",
            "updateAccountContent",
            "updateAccountProperties",
            "initiateCPMAccountManagementOperations",
            "specifyNextAccountContent",
            "renameAccounts",
            "deleteAccounts",
            "unlockAccounts",
        ],
    ),
    (
        "Gestión de la safe",
        ["manageSafe", "manageSafeMembers", "viewSafeMembers", "viewAuditLog", "backupSafe"],
    ),
    ("Workflow", ["requestsAuthorizationLevel1", "requestsAuthorizationLevel2"]),
    ("Carpetas", ["createFolders", "deleteFolders", "moveAccountsAndFolders"]),
]

# permisos que dejan al miembro administrar la safe (warning en el preview)
ADMIN_KEYS: frozenset[str] = frozenset({"manageSafe", "manageSafeMembers"})


def _expand(*true_keys: str) -> dict[str, bool]:
    """Todas las keys presentes; `true_keys` en True, el resto en False."""
    granted = set(true_keys)
    unknown = granted - set(ALL_KEYS)
    if unknown:  # guardarraíl de desarrollo
        raise ValueError(f"permisos desconocidos en un preset: {sorted(unknown)}")
    return {k: (k in granted) for k in ALL_KEYS}


# Perfiles predefinidos — EXACTAMENTE los mismos que PVWA / psPAS
# (ConvertTo-SortedPermission de github.com/pspete/psPAS). Se envía siempre
# el objeto `permissions` expandido (como hace psPAS), no un `permissionSet`.
_READ_ONLY = ("useAccounts", "retrieveAccounts", "listAccounts")
_ACCOUNTS_MANAGER = (
    *_READ_ONLY,
    "addAccounts",
    "updateAccountContent",
    "updateAccountProperties",
    "initiateCPMAccountManagementOperations",
    "specifyNextAccountContent",
    "renameAccounts",
    "deleteAccounts",
    "unlockAccounts",
    "manageSafeMembers",
    "viewAuditLog",
    "viewSafeMembers",
    "accessWithoutConfirmation",
)
_FULL = (
    *_ACCOUNTS_MANAGER,
    "manageSafe",
    "backupSafe",
    "createFolders",
    "deleteFolders",
    "moveAccountsAndFolders",
    "requestsAuthorizationLevel1",
)

PRESETS: dict[str, dict[str, bool]] = {
    "connect_only": _expand("useAccounts", "listAccounts"),
    "read_only": _expand(*_READ_ONLY),
    "approver": _expand(
        "listAccounts", "manageSafeMembers", "viewSafeMembers", "requestsAuthorizationLevel1"
    ),
    "accounts_manager": _expand(*_ACCOUNTS_MANAGER),
    "full": _expand(*_FULL),
}

PRESET_LABELS: dict[str, str] = {
    "connect_only": "Connect Only",
    "read_only": "Read Only",
    "approver": "Approver",
    "accounts_manager": "Account Manager",
    "full": "Full",
    "custom": "Custom",
}

# orden de presentación en la UI
PRESET_ORDER: tuple[str, ...] = (
    "connect_only",
    "read_only",
    "approver",
    "accounts_manager",
    "full",
    "custom",
)

# lo que aceptan `SafeMemberChanges.preset` y la UI
PRESET_KEYS: tuple[str, ...] = (*PRESETS.keys(), "custom")


def resolve_permissions(preset: str, overrides: dict[str, bool] | None) -> dict[str, bool]:
    """Objeto `permissions` final: arranca del preset (o de `overrides` si el
    preset es 'custom') y deja todas las keys presentes."""
    if preset == "custom":
        base = dict(overrides or {})
    else:
        if preset not in PRESETS:
            raise ValueError(f"preset desconocido: {preset}")
        base = dict(PRESETS[preset])
        base.update(overrides or {})
    return {k: bool(base.get(k, False)) for k in ALL_KEYS}


def granted_keys(perms: dict[str, bool]) -> list[str]:
    """Keys en True, en el orden canónico — para el diff del preview."""
    return [k for k in ALL_KEYS if perms.get(k)]
