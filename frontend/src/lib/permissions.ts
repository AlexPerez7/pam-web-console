// Copia del catálogo de permisos de safe del backend
// (backend/app/domain/safe_permissions.py). El backend es la fuente de
// verdad — resuelve y valida el objeto `permissions` final. El front usa
// esto para el form (labels + composición de los presets).
//
// Los presets son EXACTAMENTE los de PVWA / psPAS.

export const PERMISSION_LABELS: Record<string, string> = {
  useAccounts: "Usar cuentas (conectar)",
  retrieveAccounts: "Ver / copiar contraseña",
  listAccounts: "Listar cuentas",
  addAccounts: "Agregar cuentas",
  updateAccountContent: "Actualizar contenido",
  updateAccountProperties: "Actualizar propiedades",
  initiateCPMAccountManagementOperations: "Iniciar operaciones del CPM",
  specifyNextAccountContent: "Definir próxima contraseña",
  renameAccounts: "Renombrar cuentas",
  deleteAccounts: "Borrar cuentas",
  unlockAccounts: "Desbloquear cuentas",
  manageSafe: "Gestionar la safe",
  manageSafeMembers: "Gestionar miembros",
  backupSafe: "Backup de la safe",
  viewAuditLog: "Ver auditoría",
  viewSafeMembers: "Ver miembros",
  accessWithoutConfirmation: "Acceder sin confirmación",
  createFolders: "Crear carpetas",
  deleteFolders: "Borrar carpetas",
  moveAccountsAndFolders: "Mover cuentas y carpetas",
  requestsAuthorizationLevel1: "Autorizar solicitudes (nivel 1)",
  requestsAuthorizationLevel2: "Autorizar solicitudes (nivel 2)",
};

// grupos como los muestra PVWA en el diálogo de miembro
export const PERMISSION_GROUPS: Array<{ title: string; keys: string[] }> = [
  { title: "Acceso", keys: ["useAccounts", "retrieveAccounts", "listAccounts"] },
  {
    title: "Gestión de cuentas",
    keys: [
      "addAccounts",
      "updateAccountContent",
      "updateAccountProperties",
      "initiateCPMAccountManagementOperations",
      "specifyNextAccountContent",
      "renameAccounts",
      "deleteAccounts",
      "unlockAccounts",
    ],
  },
  {
    title: "Gestión de la safe",
    keys: [
      "manageSafe",
      "manageSafeMembers",
      "viewSafeMembers",
      "viewAuditLog",
      "backupSafe",
      "accessWithoutConfirmation",
    ],
  },
  {
    title: "Carpetas",
    keys: ["createFolders", "deleteFolders", "moveAccountsAndFolders"],
  },
  {
    title: "Workflow",
    keys: ["requestsAuthorizationLevel1", "requestsAuthorizationLevel2"],
  },
];

export const ALL_PERMISSION_KEYS: string[] = PERMISSION_GROUPS.flatMap((g) => g.keys);

function expand(trueKeys: string[]): Record<string, boolean> {
  const set = new Set(trueKeys);
  return Object.fromEntries(ALL_PERMISSION_KEYS.map((k) => [k, set.has(k)]));
}

const READ_ONLY = ["useAccounts", "retrieveAccounts", "listAccounts"];
const ACCOUNTS_MANAGER = [
  ...READ_ONLY,
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
];
const FULL = [
  ...ACCOUNTS_MANAGER,
  "manageSafe",
  "backupSafe",
  "createFolders",
  "deleteFolders",
  "moveAccountsAndFolders",
  "requestsAuthorizationLevel1",
];

export const PRESETS: Record<string, Record<string, boolean>> = {
  connect_only: expand(["useAccounts", "listAccounts"]),
  read_only: expand(READ_ONLY),
  approver: expand(["listAccounts", "manageSafeMembers", "viewSafeMembers", "requestsAuthorizationLevel1"]),
  accounts_manager: expand(ACCOUNTS_MANAGER),
  full: expand(FULL),
};

export const PRESET_OPTIONS: Array<{ value: string; label: string; hint: string }> = [
  { value: "connect_only", label: "Connect Only", hint: "usar cuentas y listar; sin ver la contraseña" },
  { value: "read_only", label: "Read Only", hint: "usar, ver la contraseña y listar cuentas" },
  { value: "approver", label: "Approver", hint: "autorizar solicitudes de acceso (nivel 1) y ver/gestionar miembros" },
  {
    value: "accounts_manager",
    label: "Account Manager",
    hint: "todo sobre cuentas (alta/baja/edición, CPM) + ver auditoría y miembros",
  },
  { value: "full", label: "Full", hint: "todos los permisos, incluida la gestión de la safe" },
  { value: "custom", label: "Custom", hint: "elegir permiso por permiso" },
];

export const PRESET_LABELS: Record<string, string> = Object.fromEntries(
  PRESET_OPTIONS.map((o) => [o.value, o.label]),
);

export const ADMIN_PERMISSION_KEYS = ["manageSafe", "manageSafeMembers"];

/** ¿El set de permisos coincide exactamente con un preset? Devuelve su key. */
export function matchPreset(perms: Record<string, boolean>): string {
  for (const [key, def] of Object.entries(PRESETS)) {
    if (ALL_PERMISSION_KEYS.every((k) => !!def[k] === !!perms[k])) return key;
  }
  return "custom";
}
