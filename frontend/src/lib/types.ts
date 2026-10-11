export interface Operator {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: string;
  must_change_password: boolean;
}

export type OperatorRole =
  | "viewer"
  | "operator"
  | "approver"
  | "tenant_admin"
  | "platform_admin";

export const OPERATOR_ROLES: OperatorRole[] = [
  "viewer",
  "operator",
  "approver",
  "tenant_admin",
  "platform_admin",
];

export interface Assignment {
  tenant_id: string;
  role: OperatorRole;
}

export interface OperatorAdmin {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: OperatorRole;
  is_active: boolean;
  totp_enrolled: boolean;
  must_change_password: boolean;
  created_at: string | null;
}

export interface LoginOk {
  status: "ok";
  access_token: string;
  token_type: "bearer";
  operator: Operator;
}

export interface LoginEnroll {
  status: "enroll";
  otpauth_uri: string;
  qr: string;
  secret: string;
}

export interface LoginTotpRequired {
  status: "totp_required";
}

export type LoginResponse = LoginOk | LoginEnroll | LoginTotpRequired;

export type OperatorRoleName =
  | "viewer"
  | "operator"
  | "approver"
  | "tenant_admin"
  | "platform_admin";

export interface TenantSummary {
  id: string;
  name: string;
  client: string;
  deployment: string;
  enabled: boolean;
  pvwa_url: string;
  identity_tenant_url: string | null;
  /** días sin rotar (con gestión automática deshabilitada) para contar
   *  como no-compliant en el Dashboard — ver PUT .../compliance-settings */
  compliance_stale_days: number;
  /** si el tenant tiene un logo cargado (GET /tenants/{id}/logo) — para el
   *  header del reporte ITR, junto al logo del equipo. */
  has_logo: boolean;
  /** rol efectivo del operador actual sobre este tenant (null = sin acceso) */
  my_role: OperatorRoleName | null;
}

export interface DryRunResult {
  dry_run: true;
  /** ejecutar deja una solicitud pendiente de aprobación en vez de aplicar
   *  (Fase 3 sin platform_admin, ver docs/specs/dual-approval.md) */
  requires_approval?: boolean;
  /** aprobando: ¿el plan recalculado coincide con el solicitado? */
  approval_matches?: boolean | null;
  no_op: boolean;
  diff: Array<{ field: string; from: unknown; to: unknown }>;
  patch: Array<Record<string, unknown>>;
  /** ops de acción (change/verify/…): descripción de lo que va a pasar, sin diff */
  effect?: string | null;
  warnings: string[];
  /** Fase 3: si viene, ejecutar exige que `confirm` (en el body) sea
   *  exactamente este valor — confirmación reforzada. */
  confirm_phrase?: string | null;
  /** true SOLO para account.change/verify/reconcile — las únicas 3 ops
   *  que de verdad encolan un trabajo asíncrono en el CPM. No inferir esto
   *  de "es una acción sin diff": session.terminate/platform.activate/
   *  discovery.onboard/etc. tienen la misma forma pero terminan en el
   *  momento (bug real encontrado 2026-09-24). */
  is_cpm_action?: boolean;
}

export interface WriteResult {
  dry_run: false;
  no_op: boolean;
  result: "ok" | "pending_approval";
  /** la op quedó pendiente de aprobación de otro operador (no se ejecutó) */
  pending_approval?: boolean;
  approval_id?: string;
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
  warnings?: string[];
  /** lo que devolvió la operación; en lotes con resultado parcial trae
   *  `failed` (ej. `accounts/bulk-cpm`). */
  object?: { failed?: { account_id: string; error: string }[] } & Record<string, unknown>;
}

/** Onboarding masivo de cuentas por CSV — ver
 *  docs/specs/done/account-bulk-onboard.md. */
export interface BulkCreateRowResult {
  row: number;
  ok: boolean;
  diff?: Array<{ field: string; from: unknown; to: unknown }>;
  warnings?: string[];
  target?: string;
  error?: string;
}

export interface BulkCreatePreviewResponse {
  total: number;
  valid: number;
  invalid: number;
  rows: BulkCreateRowResult[];
}

export interface BulkCreateExecuteResponse {
  total: number;
  created: number;
  failed: Array<{ row: number; error: string }>;
}

/** Mover varias cuentas a la misma Safe destino (account.move en lote) —
 *  ver AccountBulkMoveDialog.tsx y app.api.writes.account_bulk_move. */
export interface BulkMoveRowResult {
  account_id: string;
  ok: boolean;
  name?: string;
  diff?: Array<{ field: string; from: unknown; to: unknown }>;
  warnings?: string[];
  error?: string;
}

export interface BulkMovePreviewResponse {
  total: number;
  valid: number;
  invalid: number;
  rows: BulkMoveRowResult[];
}

export interface BulkMoveExecuteResponse {
  total: number;
  moved: number;
  failed: Array<{ account_id: string; error: string }>;
}

/** Eliminar varias cuentas (account.delete en lote) — ver
 *  AccountBulkDeleteDialog.tsx y app.api.writes.account_bulk_delete. */
export interface BulkDeleteRowResult {
  account_id: string;
  ok: boolean;
  name?: string;
  safe_name?: string;
  error?: string;
}

export interface BulkDeletePreviewResponse {
  total: number;
  valid: number;
  invalid: number;
  rows: BulkDeleteRowResult[];
}

export interface BulkDeleteExecuteResponse {
  total: number;
  deleted: number;
  failed: Array<{ account_id: string; name?: string; error: string }>;
}

/** Reasignar el PSM de varias plataformas al mismo PSM destino
 *  (platform.psm.update en lote) — ver PlatformBulkPsmDialog.tsx y
 *  app.api.writes.platform_bulk_psm_update. */
export interface PlatformBulkPsmRowResult {
  platform_id: string;
  ok: boolean;
  diff?: Array<{ field: string; from: unknown; to: unknown }>;
  warnings?: string[];
  error?: string;
}

export interface PlatformBulkPsmPreviewResponse {
  total: number;
  valid: number;
  invalid: number;
  rows: PlatformBulkPsmRowResult[];
}

export interface PlatformBulkPsmExecuteResponse {
  total: number;
  updated: Array<{ platform_id: string }>;
  failed: Array<{ platform_id: string; error: string }>;
}

/** Fase 4 — cuenta pendiente descubierta por CyberArk Discovery (Gen2,
 *  Privilege Cloud/ISPSS), aún sin onboardear al Vault. Forma real
 *  confirmada contra la doc oficial de CyberArk (`Get all discovered
 *  accounts`) — NO son campos planos, `identifiers`/`customProperties`
 *  son diccionarios propios que varían según `type`/`subType`. Ver
 *  `app/api/discovery.py` y `app/cyberark/client.py`
 *  (`list_discovered_accounts`). */
export interface DiscoveredAccount {
  id: string;
  type: string;
  subType: string;
  identifiers: Record<string, unknown>;
  source?: string;
  isPrivileged?: boolean;
  customProperties?: Record<string, unknown>;
  tags?: string[];
  creationTime?: string;
  updateTime?: string;
  lastActivityStatus?: "ERROR" | "WARNING" | "INFO";
}

export interface DiscoveredAccountActivity {
  id: string;
  creationTime: string;
  activity:
    | "NO_RULE_SET_FOUND"
    | "TRIGGER_COLLISION"
    | "NO_ACTION_FOUND"
    | "ACTION_FOUND"
    | "ACTION_SUCCEEDED"
    | "ACTION_FAILED";
  action?: "ONBOARD";
  status: "SUCCESS" | "INFO" | "WARNING" | "ERROR";
  message?: string;
  ruleSets?: Array<{ id: string; name: string }>;
  source?: string;
  additionalErrorInfo?: string | null;
}

/** Campañas de rotación/remediación — ver
 *  docs/specs/done/rotation-campaigns.md. Avance calculado en vivo, sin
 *  historial persistido. */
export interface CampaignRow {
  id: number;
  name: string;
  description: string | null;
  reason: string;
  created_by: string;
  created_at: string;
  total: number;
  resolved: number;
  pending: number;
  not_found: number;
}

export interface CampaignsResponse {
  tenant: string;
  campaigns: CampaignRow[];
}

export interface CampaignMemberRow {
  account_id: string;
  account_name: string;
  safe_name: string;
  platform_id: string;
  status: "resolved" | "pending" | "not_found";
}

export interface CampaignDetailResponse extends CampaignRow {
  accounts: CampaignMemberRow[];
}

export interface CampaignCreateResponse {
  id: number;
  members: number;
  missing: string[];
}

/** Base de conocimiento de errores — ver docs/specs/done/knowledge-base.md.
 *  Global, sin tenant. */
export type KbCategory = "rotation" | "connection" | "verification" | "other";

export interface KbArticle {
  id: number;
  category: KbCategory | string;
  title: string;
  error_pattern: string;
  cause: string;
  solution: string;
  source_url: string | null;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
}

export interface KbArticlesResponse {
  articles: KbArticle[];
}

export interface KbArticleIn {
  category: string;
  title: string;
  error_pattern: string;
  cause: string;
  solution: string;
  source_url?: string | null;
}

export interface SecretManagement {
  automaticManagementEnabled?: boolean;
  manualManagementReason?: string;
  status?: string;
  lastModifiedTime?: number;
  lastModifiedDateTime?: number;
  lastVerifiedTime?: number;
  lastVerifiedDateTime?: number;
  lastReconciledTime?: number;
  [k: string]: unknown;
}

export interface Account {
  id: string;
  name?: string;
  address?: string;
  userName?: string;
  platformId?: string;
  safeName?: string;
  secretType?: string;
  secretManagement?: SecretManagement;
  [k: string]: unknown;
}

/** Conteos por estado de `GET /tenants/{id}/accounts/summary` (chips de la
 *  grilla de Cuentas). */
export interface AccountsSummary {
  tenant: string;
  total: number;
  failure: number;
  manual: number;
  cpm_disabled: number;
  synced_at: string | null;
}

export type AccountStatusFilter = "failure" | "manual" | "cpm_disabled";

export interface AccountsResponse {
  tenant: string;
  count: number;
  offset: number;
  limit: number;
  accounts: Account[];
  /** presente sólo cuando la respuesta viene de account_snapshots (filtro
   *  por columna server-side) — ver docs/specs/inventory-sync-postgres.md */
  synced_at?: string | null;
}

export interface Safe {
  safeUrlId?: string;
  safeName?: string;
  safeNumber?: number;
  description?: string;
  location?: string;
  managingCPM?: string;
  numberOfVersionsRetention?: number | null;
  numberOfDaysRetention?: number | null;
  olacEnabled?: boolean;
  autoPurgeEnabled?: boolean;
  creationTime?: number;
  lastModificationTime?: number;
  creator?: { name?: string } | string;
  [k: string]: unknown;
}

export interface SafesResponse {
  tenant: string;
  count: number;
  offset: number;
  limit: number;
  safes: Safe[];
}

export interface Platform {
  general?: {
    id?: string;
    name?: string;
    systemType?: string;
    active?: boolean;
    platformType?: string;
    description?: string;
  };
  /** PSM asignado — agregado por `platforms_rows_cached()` desde "Get
   *  target platforms", no viene en `GET /API/Platforms`. `null`/ausente
   *  si esa llamada falló o la plataforma no tiene PSM. */
  psmServerId?: string | null;
  /** viene tal cual de `GET /API/Platforms` — mismo campo que ya usa
   *  `app/itr/analysis.py:analyze_platforms` para la pestaña Platforms del
   *  reporte ITR, sin request extra. */
  credentialsManagement?: {
    allowManualChange?: boolean;
    performPeriodicChange?: boolean;
    requirePasswordChangeEveryXDays?: number;
    allowManualVerification?: boolean;
    performPeriodicVerification?: boolean;
    requirePasswordVerificationEveryXDays?: number;
    allowManualReconciliation?: boolean;
    automaticReconcileWhenUnsynched?: boolean;
  };
  /** ídem — "Access Workflow Policies" del PVWA. `requireReason` sin
   *  confirmar contra un tenant real todavía (a diferencia de los otros
   *  3 campos, que sí — ver `analyze_platforms`). */
  privilegedAccessWorkflows?: {
    requireDualControlPasswordAccessApproval?: boolean;
    requireUsersToSpecifyReasonForAccess?: boolean;
    enforceCheckinCheckoutExclusiveAccess?: boolean;
    enforceOnetimePasswordAccess?: boolean;
  };
  [k: string]: unknown;
}

export interface PlatformsResponse {
  tenant: string;
  count: number;
  platforms: Platform[];
}

export interface AccountDetailResponse {
  tenant: string;
  account: Account;
}

/** Usuario de Identity/SSO — distinto de `VaultUser`. Fila del listado
 *  (`GET /identity-users`, vía Redrock). */
export interface IdentityUserRow {
  id?: string;
  username?: string;
  displayName?: string;
  email?: string;
  status?: string;
  [k: string]: unknown;
}

export interface IdentityUsersResponse {
  tenant: string;
  count: number;
  users: IdentityUserRow[];
}

/** Detalle de un usuario de Identity (`GetUserInfo` + `Locked`, agregado
 *  por el backend). El esquema exacto de `GetUserInfo` no está confirmado
 *  contra un tenant real (ver docs/specs/identity-users.md) — por eso sólo
 *  se tipan los campos que el frontend efectivamente usa. */
export interface IdentityUserDetail {
  Locked?: boolean;
  Username?: string;
  UserName?: string;
  SystemName?: string;
  [k: string]: unknown;
}

export interface IdentityUserDetailResponse {
  tenant: string;
  user: IdentityUserDetail;
}

/** Rol de Identity — sólo lectura, sin miembros ni permisos
 *  administrativos (fuera de alcance del primer corte). */
export interface IdentityRoleRow {
  id?: string;
  name?: string;
  description?: string;
  /** No confirmado contra un tenant real (2026-09-25) -- puede venir `null`
   *  si Redrock no expone esta columna para el tenant, el backend degrada
   *  solo, ver `app/api/identity.py`. */
  roleType?: string | null;
  organization?: string | null;
  [k: string]: unknown;
}

export interface IdentityRolesResponse {
  tenant: string;
  count: number;
  roles: IdentityRoleRow[];
}

/** Miembro de un rol de Identity (`Roles/GetRoleMembers`). `type` es el
 *  valor crudo de CyberArk: "User", "Group" o "Role". */
export interface IdentityRoleMember {
  id?: string | null;
  name?: string | null;
  type?: string | null;
}

export interface IdentityRoleMembersResponse {
  tenant: string;
  role_id: string;
  count: number;
  members: IdentityRoleMember[];
}

/** Usuario o rol de Identity que se puede agregar a un rol
 *  (`GET .../identity-roles/{id}/candidates`). */
export interface IdentityRoleCandidate {
  id: string;
  name: string;
  displayName?: string | null;
  email?: string | null;
  type: "User" | "Role";
  alreadyMember: boolean;
}

export interface IdentityRoleCandidatesResponse {
  tenant: string;
  results: IdentityRoleCandidate[];
}

/** Vendor de CyberArk Remote Access (Alero) — acceso remoto de terceros/
 *  proveedores. Fila de lista — ver AleroVendorDetail para el detalle
 *  completo (`GET /tenants/{t}/alero-vendors/{id}`). */
export interface AleroVendorRow {
  id?: string;
  username?: string;
  fullName?: string;
  email?: string;
  companyName?: string;
  status?: string;
  expirationStatus?: string;
  roles?: string[];
  groups?: string[];
  [k: string]: unknown;
}

export interface AleroVendorsResponse {
  tenant: string;
  count: number;
  vendors: AleroVendorRow[];
}

export interface AleroApplicationRef {
  site?: string;
  application?: string;
}

/** Detalle completo de un vendor (`GET /tenants/{t}/alero-vendors/{id}`),
 *  más rico que `AleroVendorRow` de la lista — `roles` acá viene resuelto
 *  desde `groups` (nombres, no el string plano `idaptiveRoles` de la
 *  lista), y `applications` con site/application ya resueltos a nombre. */
export interface AleroVendorDetail {
  id?: string;
  username?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  phoneNumber?: string;
  phoneModel?: string;
  phoneOs?: string;
  companyName?: string;
  status?: string;
  appVersion?: string;
  canInvite?: boolean;
  comments?: string | null;
  lastLogin?: number;
  accessStartDate?: number;
  accessEndDate?: number;
  invitedBy?: string;
  provisioningType?: string;
  roles?: string[];
  applications?: AleroApplicationRef[];
  emailConfirmedAt?: number;
  phoneAndEmailAuth?: boolean;
  invitedVendorsInitialStatus?: "Activated" | "RequiresAdminConfirmation" | string;
  maxNumOfInvitedVendors?: number;
  [k: string]: unknown;
}

/** Usuario de la app móvil de Remote Access (Identities > Users en el
 *  portal de Alero) — distinto de un vendor (Identities > Vendors), ver
 *  docs/specs/done/alero-vendors.md. */
export interface AleroUserRow {
  id?: string;
  username?: string;
  fullName?: string;
  role?: "TenantAdmin" | "User" | "VendorManager" | string;
  status?: string;
  lastLogin?: number;
  appVersion?: string;
  [k: string]: unknown;
}

export interface AleroUsersResponse {
  tenant: string;
  count: number;
  users: AleroUserRow[];
}

/** Detalle completo de un usuario (`GET /tenants/{t}/alero-users/{id}`) --
 *  superset modesto de `AleroUserRow` de la lista. */
export interface AleroUserDetail {
  id?: string;
  username?: string;
  firstName?: string;
  lastName?: string;
  role?: string;
  status?: string;
  phoneNumber?: string;
  phoneModel?: string;
  phoneOS?: string;
  appVersion?: string;
  lastLogin?: number;
  invitedBy?: string;
  [k: string]: unknown;
}

/** "Identity role" del portal de Alero — role de CyberArk Identity usado
 *  para asignar acceso a vendors (distinto de un grupo nativo de Alero,
 *  ver docs/specs/done/alero-vendors.md). */
export interface AleroRoleRow {
  id?: string;
  name?: string;
  description?: string | null;
  vendors?: number;
  invitations?: number;
  [k: string]: unknown;
}

export interface AleroRolesResponse {
  tenant: string;
  count: number;
  roles: AleroRoleRow[];
}

/** Evento del feed "Activities" del portal de Alero (login, altas/bajas,
 *  cambios de config) — `label` ya viene traducido desde `activityType`
 *  (ver `_ACTIVITY_LABELS` en el backend), `detail` es el dato más
 *  relevante para ese tipo (aplicación, cuenta+safe, motivo, etc.). */
export interface AleroActivityRow {
  id?: string;
  time?: number;
  activityType?: string;
  label?: string;
  actor?: string;
  actorRole?: string;
  detail?: string | null;
  sourceIp?: string;
  [k: string]: unknown;
}

export interface AleroActivitiesResponse {
  tenant: string;
  count: number;
  activities: AleroActivityRow[];
}

/** Confirmado contra Globex (2026-09-24) — casing real de
 *  `GET /API/LiveSessions`, distinto del inferido originalmente de
 *  `psPAS` (`SessionId`/`AccountId`, nunca existieron). */
export interface LiveSession {
  SessionID?: string;
  User?: string;
  RemoteMachine?: string;
  AccountUsername?: string;
  AccountPlatformID?: string;
  AccountAddress?: string;
  Protocol?: string;
  Start?: number;
  Duration?: number | string;
  ConnectionComponentID?: string;
  CanTerminate?: boolean;
  [k: string]: unknown;
}

export interface SessionsResponse {
  tenant: string;
  count: number;
  sessions: LiveSession[];
}

/** Grabación de una sesión PSM YA TERMINADA (distinto de `LiveSession`).
 *  Ver docs/specs/session-recordings.md. */
export interface Recording {
  SessionID?: string;
  SessionGuid?: string;
  SafeName?: string;
  FolderName?: string;
  FileName?: string;
  Start?: number;
  End?: number;
  Duration?: number | string;
  User?: string;
  RemoteMachine?: string;
  AccountUsername?: string;
  AccountPlatformID?: string;
  AccountAddress?: string;
  Protocol?: string;
  Client?: string;
  RiskScore?: number;
  Severity?: string;
  RecordingFiles?: { FileName?: string; Format?: string; FileSize?: number }[];
  [k: string]: unknown;
}

export interface RecordingsResponse {
  tenant: string;
  count: number;
  recordings: Recording[];
}

export interface RecordingActivity {
  ActivityText?: string;
  ActivityType?: number;
  ActivityId?: string;
  [k: string]: unknown;
}

export interface RecordingActivitiesResponse {
  tenant: string;
  count: number;
  activities: RecordingActivity[];
}

export interface SystemHealthVault {
  available: boolean;
  connected?: boolean;
  ip?: string | null;
}

/** Una instancia de un componente (servidor CPM, PSM, PVWA...). */
export interface SystemHealthInstance {
  ip?: string | null;
  version?: string | null;
  user?: string | null;
  connected: boolean;
  last_logon?: number | string | null;
}

export interface SystemHealthComponent {
  id: string;
  name: string;
  available: boolean;
  connected: number;
  total: number;
  /** puede faltar en un snapshot cacheado antes del 2026-09-28 */
  instances?: SystemHealthInstance[];
}

export interface SystemHealthSnapshot {
  generated_at: string;
  cached: boolean;
  vault: SystemHealthVault;
  components: SystemHealthComponent[];
  accounts_total: number | null;
  sessions_active: number | null;
}

export interface TenantSystemHealth extends SystemHealthSnapshot {
  tenant: string;
}

export interface SystemHealthRow extends SystemHealthSnapshot {
  tenant: string;
  tenant_name: string;
}

export interface SystemHealthError {
  tenant: string;
  tenant_name: string;
  error: string;
}

export interface SystemHealthResponse {
  generated_at: string;
  tenants: SystemHealthRow[];
  errors: SystemHealthError[];
}

export interface SafeMember {
  memberName?: string;
  memberType?: string;
  membershipExpirationDate?: number | null;
  isPredefinedUser?: boolean;
  permissions?: Record<string, boolean>;
  [k: string]: unknown;
}

export interface SafeMembersResponse {
  tenant: string;
  safe_id: string;
  members: SafeMember[];
}

export interface MemberSearchResult {
  name: string;
  displayName: string;
  email: string;
  kind: "user" | "group";
  source: string;
  searchIn: string;
  alreadyMember: boolean;
}

export interface MemberSearchResponse {
  tenant: string;
  results: MemberSearchResult[];
}

/** Fase 3: copia de seguridad de una cuenta borrada (`account.delete`). Ver
 *  docs/specs/account-safe-delete.md. */
export interface AccountBackup {
  id: number;
  account_name: string;
  safe_name: string;
  user_name: string;
  address: string | null;
  platform_id: string;
  deleted_by: string;
  deleted_at: string;
}

export interface AccountBackupsResponse {
  backups: AccountBackup[];
}

/** Fase 3: paquete de "Export Platform" guardado antes de borrar una
 *  plataforma (`platform.delete`). Ver docs/specs/platform-lifecycle.md. */
export interface PlatformBackup {
  id: number;
  platform_id: string;
  numeric_id: string;
  size_bytes: number;
  deleted_by: string;
  deleted_at: string;
}

export interface PlatformBackupsResponse {
  backups: PlatformBackup[];
}

/** Cuenta en error de rotación/verificación del CPM (sección
 *  Troubleshooting). Ver docs/specs/troubleshooting-errores-cpm.md. */
export interface FailedAccountRow {
  id: string;
  name: string | null;
  userName: string | null;
  address: string | null;
  safeName: string | null;
  platformId: string | null;
  retrying: boolean;
  reason: string | null;
  lastModifiedTime: number | null;
}

export interface TroubleshootingResponse {
  tenant: string;
  cached: boolean;
  summary: {
    failed_total: number;
    active_retry: number;
    given_up: number;
    by_reason: ChartDatum[];
  };
  accounts: FailedAccountRow[];
}

export type ComplianceReason =
  | "PolicyFailures"
  | "DisableByUser"
  | "DisableByCPM"
  | "Locked"
  | "DisabledStale"
  | "ActiveOverdue";

export interface ComplianceAccountRow {
  id: string;
  name: string | null;
  userName: string | null;
  address: string | null;
  safeName: string | null;
  platformId: string | null;
  lastModifiedTime: number | null;
  /** sólo viene con reason=DisabledStale/ActiveOverdue — null si nunca rotó */
  ageDays?: number | null;
  /** ídem — el umbral real usado (de la plataforma de la cuenta, o el
   *  default del tenant si no define uno) */
  thresholdDays?: number;
}

export interface ComplianceAccountsResponse {
  tenant: string;
  reason: ComplianceReason;
  count: number;
  accounts: ComplianceAccountRow[];
}

/** GET .../compliance/search?q= — busca por término libre y devuelve, por
 *  cuenta, TODAS las categorías de compliance en las que cae a la vez
 *  (a diferencia de /accounts?reason=X, que es una categoría por vez). */
export interface ComplianceSearchRow {
  id: string;
  name: string | null;
  userName: string | null;
  address: string | null;
  safeName: string | null;
  platformId: string | null;
  compliant: boolean;
  reasons: ComplianceReason[];
  ageDays: number | null;
  thresholdDays: number;
}

export interface ComplianceSearchResponse {
  tenant: string;
  query: string;
  count: number;
  accounts: ComplianceSearchRow[];
}

/** GET .../compliance/accounts/{id}/status — compliance de UNA cuenta
 *  puntual, mismo criterio exacto que el % del Dashboard. `reasons` puede
 *  tener más de un valor (ej. PolicyFailures y ActiveOverdue a la vez). */
export interface AccountComplianceStatus {
  tenant: string;
  account_id: string;
  compliant: boolean;
  age_days: number | null;
  threshold_days: number;
  reasons: ("PolicyFailures" | "DisabledStale" | "ActiveOverdue")[];
  /** último cambio EXITOSO (epoch s): la versión más reciente de la
   *  contraseña; con `last_changed_source: "account"` es el
   *  `lastModifiedTime` de CyberArk (no se pudieron leer las versiones). */
  last_changed?: number | null;
  last_changed_source?: "versions" | "account";
  /** el último intento del CPM falló */
  cpm_failed?: boolean;
}

/** Excepción manual de compliance por cuenta — ver
 *  docs/specs/compliance-exemptions.md. Cuentas con la gestión automática
 *  deshabilitada a pedido del cliente, para siempre (ej. cuentas root sólo
 *  custodiadas), que no deben restar del % de Compliance. Vive en PAM Web Console, no
 *  en CyberArk — nada se excluye en silencio, siempre con motivo y
 *  auditada. */
export interface ComplianceExemption {
  account_id: string;
  reason: string;
  created_by: string;
  created_at: string;
}

export interface ComplianceExemptionsResponse {
  tenant: string;
  exemptions: ComplianceExemption[];
}

export interface RevealResponse {
  password: string;
}

export interface SafeNamesResponse {
  tenant: string;
  names: string[];
}

/** `PrivilegedSessionManagement` completo de una plataforma (Get target
 *  platforms). Sólo se usan `PSMServerId`/`PSMServerName` en la UI — el
 *  resto (`PSMConnectors`, etc.) viaja sin tocar. Ver
 *  docs/specs/platform-psm-migration.md. */
export interface PlatformPsm {
  PSMServerId?: string | null;
  PSMServerName?: string | null;
  [k: string]: unknown;
}

export interface PlatformPsmResponse {
  tenant: string;
  platform_id: string;
  psm: PlatformPsm;
}

export interface SafeDefaults {
  managing_cpm?: string;
  retention_type?: "days" | "versions";
  retention_value?: number;
}

export interface SafeDefaultsResponse {
  tenant: string;
  defaults: SafeDefaults;
}

export interface ReportParam {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "multiselect" | "user";
  required: boolean;
  options: string[] | null;
  help: string | null;
  /** etiqueta legible por valor de `options` (multiselect/select) */
  option_labels?: Record<string, string> | null;
}

export interface ReportInfo {
  name: string;
  label: string;
  description: string;
  category: string;
  heavy: boolean;
  /** formatos de export que acepta (`xlsx` sólo si el reporte tiene Excel propio) */
  formats: ("csv" | "json" | "xlsx")[];
  params: ReportParam[];
}

export interface ReportColumn {
  key: string;
  label: string;
  kind: "text" | "date" | "bool" | "number";
}

export interface ReportResult {
  columns: ReportColumn[];
  rows: Record<string, unknown>[];
  meta: Record<string, unknown>;
  cached?: boolean;
  generated_at?: string;
}

/** Reportes "heavy" (Batch 4: target-inactivity, account-activity) corren
 *  como job ARQ en vez de un GET síncrono. */
export interface ReportJobQueued {
  job_id: string;
}

export type ReportJobStatus =
  | { status: "deferred" | "queued" | "in_progress" }
  | ({ status: "complete" } & ReportResult);

export interface AccountActivity {
  Date?: number;
  Action?: string;
  Reason?: string;
  User?: string;
  ClientType?: string;
  Alias?: string;
  Safe?: string;
  [k: string]: unknown;
}

export interface AccountActivitiesResponse {
  tenant: string;
  account_id: string;
  activities: AccountActivity[];
}

/** Objeto crudo de CyberArk (GET .../Secret/Versions). `VersionID`/
 *  `ModificationTime`/`ModifiedBy` confirmados contra Globex
 *  (2026-09-18), ver docs/specs/done/account-password-versions.md. */
export interface AccountPasswordVersion {
  VersionID?: number;
  ModificationTime?: number | string;
  ModifiedBy?: string;
  [k: string]: unknown;
}

export interface AccountPasswordVersionsResponse {
  tenant: string;
  account_id: string;
  versions: AccountPasswordVersion[];
}

export type AuditKind = "consulta" | "cambio" | "sensible" | "acceso";

export interface AuditEntry {
  id: number;
  ts: string;
  operator: string;
  role: string;
  tenant_id: string | null;
  action: string;
  kind: AuditKind;
  category: string;
  label: string;
  target: string | null;
  target_label: string | null;
  result: string;
  dry_run: boolean;
  detail: Record<string, unknown>;
  request_id: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  next_before_id: number | null;
}

export interface AuditSummary {
  since: string;
  until: string;
  counts: {
    passwords_revealed: number;
    recordings_downloaded: number;
    credential_backups_accessed: number;
    access_denied: number;
    errors: number;
    rollbacks_failed: number;
    failed_logins: number;
    pending_approvals: number;
  };
}

export interface AuditVerifyResult {
  intact: boolean;
  first_broken_id: number | null;
  checked: number;
  first_id: number | null;
  last_id: number | null;
  verified_at: string;
}

export type SearchResource = "accounts" | "safes" | "platforms" | "users" | "groups";

export interface SearchGroup {
  tenant: string;
  tenant_name: string;
  count: number;
  returned: number;
  items: Record<string, unknown>[];
}

export interface SearchError {
  tenant: string;
  tenant_name: string;
  error: string;
}

export interface ResourceSearchResponse {
  query: string;
  resource: SearchResource;
  results: SearchGroup[];
  errors: SearchError[];
}

export interface ChartDatum {
  label: string;
  value: number;
}

export interface ComponentHealth {
  label: string;
  connected: number;
  total: number;
  available: boolean;
}

export interface DashboardResponse {
  tenant: string;
  generated_at: string;
  cached: boolean;
  compliance_partial: boolean;
  kpis: {
    accounts_total: number;
    accounts_failed: number;
    accounts_never_changed: number;
    accounts_no_platform: number;
    auto_managed_pct: number;
    compliance_pct: number;
    safes_total: number;
    safes_no_cpm: number;
    safes_olac: number;
    platforms_total: number;
    platforms_active: number;
    platforms_no_periodic_rotation: number;
    components_disconnected: number;
    /** conteo EN VIVO al momento del cálculo, sin tendencia histórica —
     *  ver docs/PLAN.md §7 Fase 1, "dashboard v3" */
    sessions_active: number;
    sessions_available: boolean;
  };
  charts: {
    accounts_by_platform: ChartDatum[];
    password_age: ChartDatum[];
    management_status: ChartDatum[];
    safe_retention: ChartDatum[];
    accounts_by_month: ChartDatum[];
    compliance_breakdown: ChartDatum[];
    cpm_errors: ChartDatum[];
    components: ComponentHealth[];
    sessions_by_protocol: ChartDatum[];
  };
}

/** Solicitud de aprobación de un segundo operador — ver
 *  docs/specs/dual-approval.md. */
export interface ApprovalRequest {
  id: string;
  tenant_id: string;
  feature: string;
  target: string;
  method: "POST" | "PUT" | "DELETE";
  path: string;
  body: Record<string, unknown>;
  changes: Record<string, unknown>;
  diff: Array<{ field: string; from: unknown; to: unknown }>;
  effect: string | null;
  warnings: string[];
  confirm_phrase: string | null;
  requested_by: string;
  request_reason: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "expired" | "stale";
  decided_by: string | null;
  decision_reason: string | null;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  can_approve: boolean;
  is_mine: boolean;
}

/** Una fila del visor de configuración completa de una plataforma. */
export interface PlatformConfigRow {
  name: string;
  value: string;
  /** descripción en español (PAM Web Console) o comentario del propio .ini */
  hint: string | null;
}

/** Un nodo del árbol de configuración (`GET .../platforms/{id}/config`),
 *  equivalente a una rama del editor de plataformas del PVWA. */
export interface PlatformConfigNode {
  id: string;
  label: string;
  /** nombre original en el PVWA / paquete de exportación */
  source_name: string;
  rows: PlatformConfigRow[];
  children: PlatformConfigNode[];
}

export interface PlatformConfigResponse {
  tenant: string;
  platform_id: string;
  files: string[];
  tree: PlatformConfigNode[];
  errors: string[];
}

/** Estado público de status.cyberark.com (el FABRICANTE, no un tenant de
 *  PAM Web Console) — ver backend/app/vendor_status.py. */
export type VendorComponentState =
  | "operational"
  | "degraded_performance"
  | "partial_outage"
  | "major_outage"
  | "under_maintenance"
  | string;

export interface VendorStatusComponent {
  id: number;
  name: string;
  state: VendorComponentState;
  url: string | null;
}

export interface VendorStatusNotice {
  id: number;
  type: "incident" | "planned" | string;
  state: string;
  timeline_state: string;
  subject: string;
  url: string;
  begins_at: string | null;
  ends_at: string | null;
  began_at: string | null;
  ended_at: string | null;
  latest_update: string | null;
  updated_at: string | null;
}

export interface VendorStatusSummary {
  page_state: VendorComponentState;
  page_state_text: string;
  page_url: string;
  updated_at: string;
  components: VendorStatusComponent[];
  active_notices: VendorStatusNotice[];
  upcoming_notices: VendorStatusNotice[];
  cached: boolean;
}

/** Setup > Connector management (ver docs/specs/setup-connector-management.md). */
export interface ConnectorComponent {
  name: string;
  acronym: string;
  installedAt: string | null;
  status: string;
  version: string;
  usedCpu: number | null;
  usedMemory: number | null;
  updatedAt: string | null;
  upgradeVersions: string[];
}

export interface SystemConnectorRow {
  connectorId: string;
  hostname: string;
  ip: string;
  os: string;
  platformType: string;
  status: string;
  version: string;
  componentsActive: number;
  componentsTotal: number;
  degraded: boolean;
  upgradeAvailable: boolean;
  upgradeVersions: string[];
  lastHeartbeat: string | null;
  poolId: string | null;
  pool: string | null;
  details: Record<string, string>;
  components: ConnectorComponent[];
}

export interface SystemConnectorsResponse {
  tenant: string;
  count: number;
  connectors: SystemConnectorRow[];
}

export interface SiaConnectorRow {
  connectorId: string;
  hostname: string;
  ip: string;
  os: string;
  status: string;
  version: string;
  isLatestVersion: boolean | null;
  region: string;
  poolName: string;
}

export interface SiaConnectorsResponse {
  tenant: string;
  count: number;
  connectors: SiaConnectorRow[];
}

export interface ConnectorPoolRow {
  poolId: string;
  name: string;
  description: string;
  connectorCount: number;
  connectors: { connectorId: string; hostname: string }[];
}

export interface ConnectorPoolsResponse {
  tenant: string;
  count: number;
  pools: ConnectorPoolRow[];
}
