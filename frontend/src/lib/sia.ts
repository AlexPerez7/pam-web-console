import { api } from "@/lib/api";

// Tipos y etiquetas del espacio SIA (sólo lectura). Ver
// docs/specs/done/sia-read-only.md y backend/app/domain/sia.py.

export type SiaPolicyStatus =
  | "active"
  | "suspended"
  | "expired"
  | "draft"
  | "validating"
  | "error"
  | "warning"
  | "other";

export interface SiaPolicyRow {
  id: string;
  source: "uap" | "legacy";
  kind: "vm" | "db";
  name: string;
  description: string;
  status: SiaPolicyStatus;
  statusDetail: string | null;
  policyType: string | null;
  validFrom: string | null;
  validTo: string | null;
  timeZone: string | null;
  locations: string[];
  tags: string[];
  principalsCount: number | null;
  rulesCount: number;
  maxSessionHours: number | null;
  createdAt: string | null;
  createdBy: string | null;
  modifiedAt: string | null;
  modifiedBy: string | null;
}

export interface SiaPrincipal {
  type: string;
  name: string;
  source: string | null;
}

export interface SiaPolicyRule {
  name: string;
  principals: SiaPrincipal[];
  days: string[];
  fromHour: string | null;
  toHour: string | null;
  timeZone: string | null;
  maxSessionHours: number | null;
  idleMinutes: number | null;
  connectAs: string[];
}

export type SiaTargetGroup =
  | {
      location: string;
      type: "fqdn_ip";
      fqdnRules: { operator: string | null; pattern: string | null; domain: string | null }[];
      ipRules: { operator: string | null; ips: string[]; logicalName: string | null }[];
    }
  | {
      location: string;
      type: "cloud";
      fields: { label: string; values: string[] }[];
      tags: { key: string; values: string[] }[];
    }
  | {
      location: string;
      type: "db";
      instances: {
        name: string;
        type: string | null;
        id: string | null;
        authMethod: string | null;
        permissions: string[];
      }[];
    }
  | { location: string; type: "other"; lines: string[] };

export interface SiaVmPermissions {
  ssh: { username: string | null } | null;
  rdp: {
    mode: "local" | "domain" | null;
    groups: string[];
    domainGroups: string[];
    reconnect: boolean | null;
  } | null;
}

export interface SiaConditions {
  days: string[];
  fromHour: string | null;
  toHour: string | null;
  maxSessionHours: number | null;
  idleMinutes: number | null;
}

export interface SiaPolicyDetail extends SiaPolicyRow {
  principals: SiaPrincipal[];
  rules: SiaPolicyRule[];
  targets: string[];
  targetGroups: SiaTargetGroup[];
  permissions: SiaVmPermissions | null;
  conditions: SiaConditions | null;
  raw: unknown;
}

export interface SiaPoliciesResponse {
  tenant: string;
  count: number;
  policies: SiaPolicyRow[];
  warnings: string[];
}

export interface SiaPolicyResponse {
  tenant: string;
  policy: SiaPolicyDetail;
}

export interface SiaSessionRow {
  id: string;
  status: string;
  user: string | null;
  source: string | null;
  target: string | null;
  targetUsername: string | null;
  protocol: string | null;
  platform: string | null;
  applicationCode: string | null;
  accessMethod: string | null;
  startTime: string | null;
  endTime: string | null;
  durationSeconds: number | null;
  endReason: string | null;
  errorCode: string | null;
  isRecording: boolean | null;
}

export interface SiaSessionsResponse {
  tenant: string;
  status: "active" | "recent";
  count: number;
  sessions: SiaSessionRow[];
}

export interface SiaActivityRow {
  id: string;
  timestamp: string | null;
  username: string | null;
  action: string | null;
  actionType: string | null;
  command: string | null;
  target: string | null;
  message: string | null;
}

export interface SiaSessionResponse {
  tenant: string;
  session: SiaSessionRow;
  activities: SiaActivityRow[];
  warnings: string[];
}

export interface SiaDbTargetRow {
  kind: "db";
  id: string;
  name: string;
  engine: string | null;
  family: string | null;
  platform: string | null;
  endpoint: string | null;
  port: number | null;
  services: string[];
  authMethod: string | null;
  secretId: string | null;
  certificateValidation: boolean | null;
}

export interface SiaTargetSetRow {
  kind: "vm";
  name: string;
  type: string | null;
  description: string;
  provisionFormat: string | null;
  secretType: string | null;
  secretId: string | null;
  certificateValidation: boolean | null;
}

export interface SiaDbStrongAccount {
  id: string;
  kind: "db";
  name: string;
  type: string | null;
  description: string;
  store: string | null;
  username: string | null;
  account: string | null;
  linkedSafe: string | null;
  linkedAccount: string | null;
  active: boolean;
  createdBy: string | null;
  createdAt: string | null;
  updatedBy: string | null;
  updatedAt: string | null;
  databases: SiaDbTargetRow[];
}

export interface SiaVmStrongAccount {
  id: string;
  kind: "vm";
  name: string;
  type: string | null;
  store: string | null;
  domain: string;
  accountType: "local" | "domain";
  username: string | null;
  linkedSafe: string | null;
  linkedAccount: string | null;
  active: boolean;
  rotatable: boolean | null;
  ephemeralDomainUsers: boolean;
  ephemeralUserLocation: string | null;
  domainController: string | null;
  domainControllerNetbios: string | null;
  domainControllerLdaps: boolean | null;
  domainControllerCertValidation: boolean | null;
  winrmHttps: boolean | null;
  winrmCertValidation: boolean | null;
  createdAt: string | null;
  updatedAt: string | null;
  targetSets: SiaTargetSetRow[];
}

export interface SiaStrongAccountsResponse {
  tenant: string;
  count: number;
  vm: SiaVmStrongAccount[];
  db: SiaDbStrongAccount[];
  databases: SiaDbTargetRow[];
  targetSetsWithoutAccount: SiaTargetSetRow[];
  databasesWithoutAccount: SiaDbTargetRow[];
  warnings: string[];
}

export const SECRET_TYPE_LABEL: Record<string, string> = {
  username_password: "Usuario y contraseña",
  iam_user: "Usuario IAM",
  cyberark_pam: "Cuenta de Privilege Cloud",
  atlas_access_keys: "Claves de acceso de Atlas",
  ProvisionerUser: "Usuario aprovisionador",
  PCloudAccount: "Cuenta de Privilege Cloud",
};

export const TARGET_SET_TYPE_LABEL: Record<string, string> = {
  Domain: "Dominio",
  Suffix: "Sufijo",
  Target: "Target puntual",
};

export interface SiaSettingField {
  key: string;
  label: string;
  value: string | number | boolean;
}

export interface SiaSettingsGroup {
  key: string;
  label: string;
  fields: SiaSettingField[];
}

export interface SiaSettingsResponse {
  tenant: string;
  count: number;
  groups: SiaSettingsGroup[];
}

export const POLICY_STATUS: Record<
  SiaPolicyStatus,
  { label: string; variant: "success" | "warning" | "danger" | "neutral" | "info" }
> = {
  active: { label: "Activa", variant: "success" },
  suspended: { label: "Suspendida", variant: "neutral" },
  expired: { label: "Vencida", variant: "warning" },
  draft: { label: "Borrador", variant: "neutral" },
  validating: { label: "Validando", variant: "info" },
  error: { label: "Con error", variant: "danger" },
  warning: { label: "Con advertencias", variant: "warning" },
  other: { label: "Desconocido", variant: "neutral" },
};

const LOCATION_LABEL: Record<string, string> = {
  "FQDN/IP": "IP o FQDN",
  OnPrem: "On-premise",
  onprem: "On-premise",
};

export function locationLabel(v: string): string {
  return LOCATION_LABEL[v] ?? v;
}

export const OPERATOR_LABEL: Record<string, string> = {
  EXACTLY: "Exactamente",
  WILDCARD: "Comodín",
  PREFIX: "Empieza con",
  SUFFIX: "Termina con",
  CONTAINS: "Contiene",
};

export const DB_AUTH_LABEL: Record<string, string> = {
  ldap_auth: "LDAP",
  db_auth: "Usuario local de la base",
  oracle_auth: "Oracle",
  mongo_auth: "MongoDB",
  sqlserver_auth: "SQL Server",
  rds_iam_user_auth: "Usuario IAM de RDS",
};

const ALL_DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

/** ["Lun","Mar","Mié","Jue","Vie"] -> "Lun a Vie"; los 7 o ninguno -> "Todos los días". */
export function daysLabel(days: string[]): string {
  if (days.length === 0 || days.length === 7) return "Todos los días";
  const idx = days.map((d) => ALL_DAYS.indexOf(d)).sort((a, b) => a - b);
  const contiguous = idx.every((v, i) => i === 0 || v === idx[i - 1] + 1) && !idx.includes(-1);
  if (contiguous && idx.length > 2) return `${ALL_DAYS[idx[0]]} a ${ALL_DAYS[idx[idx.length - 1]]}`;
  return idx.map((i) => (i >= 0 ? ALL_DAYS[i] : "?")).join(", ");
}

export function hoursLabel(c: { fromHour: string | null; toHour: string | null }): string {
  return c.fromHour && c.toHour ? `de ${c.fromHour} a ${c.toHour}` : "a cualquier hora";
}

export const KIND_LABEL: Record<"vm" | "db", string> = { vm: "VM", db: "Base de datos" };

export const PRINCIPAL_LABEL: Record<string, string> = {
  user: "Usuario",
  group: "Grupo",
  role: "Rol",
};

/** 3725 -> "1 h 2 min". "—" si no hay dato. */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${sec} s`;
}

/** Duración de una sesión: la que informa CyberArk o, si sigue activa,
 *  el tiempo transcurrido desde el inicio. */
export function sessionDuration(s: SiaSessionRow): number | null {
  if (s.durationSeconds != null) return s.durationSeconds;
  if (!s.startTime) return null;
  const start = new Date(s.startTime).getTime();
  if (Number.isNaN(start)) return null;
  return Math.max(0, Math.round((Date.now() - start) / 1000));
}

/** Misma query en la lista y en el detalle de una strong account: al
 *  abrir el detalle desde la lista, los datos ya están en caché. */
export function strongAccountsQuery(tenant: string) {
  return {
    queryKey: ["sia-strong-accounts", tenant],
    queryFn: () => api<SiaStrongAccountsResponse>(`/tenants/${tenant}/sia/strong-accounts`),
  };
}
