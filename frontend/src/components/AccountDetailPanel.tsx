import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BookOpen,
  CalendarClock,
  ChevronDown,
  Eye,
  History,
  KeyRound,
  Loader2,
  Pencil,
  RefreshCcw,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { AccountDeleteDialog } from "@/components/AccountDeleteDialog";
import { AccountEditDialog } from "@/components/AccountEditDialog";
import { AccountLinkDialog } from "@/components/AccountLinkDialog";
import { AccountPasswordDialog } from "@/components/AccountPasswordDialog";
import { AccountActivityFeed, AuditTrailFeed } from "@/components/ActivityFeed";
import {
  ComplianceExemptionCreateDialog,
  ComplianceExemptionDeleteDialog,
} from "@/components/ComplianceExemptionDialogs";
import { DryRunDialog } from "@/components/DryRunDialog";
import { RevealPasswordDialog } from "@/components/RevealPasswordDialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { classify, cleanActivityNote } from "@/lib/activityClassify";
import { CPM_LABEL, isInProcess, type CpmAction } from "@/lib/cpmActions";
import { cpmFailures } from "@/lib/cpmFailures";
import { dash, fmtDate, fmtDateTime, relTime } from "@/lib/format";
import { invalidateAccount } from "@/lib/invalidate";
import { matchKbArticle } from "@/lib/kbMatch";
import { pendingCpmTask, type PendingCpmTask } from "@/lib/pendingCpmTask";
import { roleAtLeast } from "@/lib/roles";
import { cn } from "@/lib/utils";
import type {
  Account,
  AccountActivitiesResponse,
  AccountActivity,
  AccountComplianceStatus,
  AccountDetailResponse,
  AccountPasswordVersionsResponse,
  AuditEntry,
  ComplianceExemption,
  ComplianceExemptionsResponse,
  KbArticle,
  KbArticlesResponse,
  TenantSummary,
} from "@/lib/types";

type Tab = "resumen" | "detalles" | "actividad" | "versiones";
type ActivityFilter = "all" | "cyberark" | "pam";
type Obj = Record<string, unknown>;

function asObj(v: unknown): Obj | undefined {
  return v != null && typeof v === "object" ? (v as Obj) : undefined;
}

export function StatusPill({ status }: { status?: string }) {
  const kind = (() => {
    const s = (status ?? "").toLowerCase().replace(/\s/g, "");
    if (["success", "succeeded"].includes(s)) return "ok" as const;
    if (["failure", "failed"].includes(s)) return "fail" as const;
    if (s === "inprocess") return "inprocess" as const;
    return "unknown" as const;
  })();
  const LABEL = {
    ok: "Gestión OK",
    fail: "Falla de gestión",
    inprocess: "CPM en curso",
    unknown: "Sin estado",
  }[kind];
  const dotClass = {
    ok: "bg-line-identity",
    fail: "bg-line-cyberdefense",
    inprocess: "bg-sky-500",
    unknown: "bg-muted-foreground",
  }[kind];
  const textClass = {
    ok: "bg-[#eaf4de] text-success-ink",
    fail: "bg-[#fbe2e2] text-danger-ink",
    inprocess: "bg-sky-100 text-sky-600",
    unknown: "bg-muted text-muted-foreground",
  }[kind];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold",
        textClass,
      )}
    >
      {kind === "inprocess" ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <span className={cn("size-1.5 rounded-full", dotClass)} />
      )}
      {LABEL}
    </span>
  );
}

const COMPLIANCE_REASON_LABEL: Record<string, string> = {
  PolicyFailures: "CyberArk la marca fuera de la política de rotación de su plataforma.",
  ActiveOverdue: "Gestión automática, pero sin rotar dentro del plazo de su plataforma.",
  DisabledStale: "Gestión automática deshabilitada, sin rotar dentro del plazo de su plataforma.",
};

/** Motivo `PolicyFailures` cuando el último intento del CPM falló: CyberArk
 *  incluye la cuenta en ese filtro por la falla, aunque la contraseña esté
 *  dentro del plazo (el círculo "Days ago" del PVWA sólo mira la
 *  antigüedad y la muestra "Compliant"). */
const POLICY_FAILURE_CPM =
  "El último intento de cambio del CPM falló: CyberArk la marca fuera de política aunque la contraseña esté dentro del plazo.";

export function CompliancePill({ compliant }: { compliant: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold",
        compliant ? "bg-[#eaf4de] text-success-ink" : "bg-[#fbe2e2] text-danger-ink",
      )}
    >
      <span
        className={cn("size-1.5 rounded-full", compliant ? "bg-line-identity" : "bg-line-cyberdefense")}
      />
      {compliant ? "Compliant" : "No compliant"}
    </span>
  );
}

type SecretManagement = NonNullable<Account["secretManagement"]>;

/** Tarjeta "Estado de la contraseña" del Resumen: une lo que antes eran dos
 *  tarjetas (Compliance + Gestión del secreto). A la izquierda la
 *  antigüedad de la contraseña con el veredicto y sus motivos (inspirado en
 *  el círculo "Days ago" del PVWA, pero explicando el porqué); abajo los
 *  tres indicadores de cambio/verificación/reconciliación.
 *
 *  La antigüedad y el "último cambio" salen de `compliance.last_changed`
 *  (la versión más reciente de la contraseña, calculada en el backend), NO
 *  de `lastModifiedTime`: ese campo se mueve también cuando falla un
 *  intento del CPM -- caso real de Globex (2026-09-29), PAM Web Console mostraba "0
 *  días / hace 11 min" en una contraseña de 21 días. */
function PasswordStatusCard({
  sm,
  compliance,
  canChange,
  canVerify,
  onAction,
  exemption,
  canOperator,
  onExclude,
  onRemoveExemption,
}: {
  sm: SecretManagement;
  compliance?: AccountComplianceStatus;
  canChange: boolean;
  canVerify: boolean;
  onAction: (a: CpmAction) => void;
  /** excepción manual de compliance activa para esta cuenta, si la hay —
   *  ver docs/specs/compliance-exemptions.md */
  exemption?: ComplianceExemption;
  canOperator: boolean;
  onExclude: () => void;
  onRemoveExemption: () => void;
}) {
  const accountModified = sm.lastModifiedTime ?? sm.lastModifiedDateTime;
  const lastChanged =
    compliance && compliance.last_changed_source === "versions"
      ? compliance.last_changed
      : accountModified;
  const cpmFailed =
    compliance?.cpm_failed ?? ["failure", "failed"].includes(String(sm.status ?? "").toLowerCase());
  // hora del intento fallido: sólo si difiere del último cambio real
  const failedAttempt =
    cpmFailed && accountModified && accountModified !== lastChanged ? accountModified : undefined;

  const mgmtBadge =
    sm.automaticManagementEnabled === false ? (
      String(sm.manualManagementReason ?? "").startsWith("(CPM)") ? (
        <Badge variant="danger" title={`Motivo: ${sm.manualManagementReason}`}>
          Deshabilitada por el CPM
        </Badge>
      ) : (
        <Badge variant="warning">Gestión automática deshabilitada</Badge>
      )
    ) : (
      <Badge variant="success">Automática</Badge>
    );

  return (
    <Card
      icon={
        !compliance || compliance.compliant ? (
          <ShieldCheck className="size-3.5 text-sky-600" />
        ) : (
          <ShieldAlert className="size-3.5 text-danger-ink" />
        )
      }
      title="Estado de la contraseña"
      headerRight={
        <div className="flex items-center gap-2">
          {mgmtBadge}
          {compliance && <CompliancePill compliant={compliance.compliant} />}
          {exemption ? (
            <Badge variant="info" title={`Motivo: ${exemption.reason}`}>
              Excluida de compliance
            </Badge>
          ) : null}
          {canOperator &&
            (exemption ? (
              <button
                onClick={onRemoveExemption}
                className="text-xs font-semibold text-sky-600 hover:underline"
              >
                Quitar excepción
              </button>
            ) : (
              <button
                onClick={onExclude}
                className="text-xs font-semibold text-sky-600 hover:underline"
              >
                Excluir de compliance
              </button>
            ))}
        </div>
      }
    >
      {compliance && <ComplianceSummary status={compliance} cpmFailed={cpmFailed} />}
      <div
        className={cn(
          "grid grid-cols-1 divide-y divide-border sm:grid-cols-3 sm:divide-x sm:divide-y-0",
          compliance && "border-t border-border",
        )}
      >
        <Stat
          label="Último cambio"
          when={lastChanged}
          note={
            failedAttempt ? (
              <span className="font-semibold text-danger-ink" title={fmtDateTime(failedAttempt)}>
                Último intento: falló {relTime(failedAttempt)}
              </span>
            ) : undefined
          }
          action={canChange ? { label: "Cambiar ahora", onClick: () => onAction("change") } : undefined}
        />
        <Stat
          label="Última verificación"
          when={sm.lastVerifiedTime ?? sm.lastVerifiedDateTime}
          warnIfNever="Nunca se verificó: no hay constancia de que la contraseña guardada funcione en el target."
          action={canVerify ? { label: "Verificar", onClick: () => onAction("verify") } : undefined}
        />
        <Stat
          label="Última reconciliación"
          when={sm.lastReconciledTime}
          action={
            canChange ? { label: "Reconciliar", onClick: () => onAction("reconcile") } : undefined
          }
        />
      </div>
    </Card>
  );
}

function ComplianceSummary({
  status,
  cpmFailed,
}: {
  status: AccountComplianceStatus;
  cpmFailed: boolean;
}) {
  const { compliant, age_days: ageDays, threshold_days: thresholdDays, reasons } = status;
  return (
    <div className="flex items-center gap-4 p-4">
      <div
        className={cn(
          "flex size-[72px] shrink-0 flex-col items-center justify-center rounded-full border-[3px]",
          compliant ? "border-line-identity/40" : "border-line-cyberdefense/40",
        )}
      >
        <span
          className={cn(
            "text-xl font-bold leading-none tabular-nums",
            compliant ? "text-success-ink" : "text-danger-ink",
          )}
        >
          {ageDays ?? "—"}
        </span>
        <span className="mt-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          {ageDays === null ? "sin rotar" : ageDays === 1 ? "día" : "días"}
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-1.5 text-sm text-muted-foreground">
        <span className="text-xs">
          Antigüedad de la contraseña vigente · umbral: {thresholdDays} días
        </span>
        {compliant ? (
          <p>Dentro de la política de rotación.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {reasons.map((r) => (
              <li key={r} className="flex gap-1.5">
                <span className="mt-1.5 size-1 shrink-0 rounded-full bg-line-cyberdefense" />
                {r === "PolicyFailures" && cpmFailed
                  ? POLICY_FAILURE_CPM
                  : (COMPLIANCE_REASON_LABEL[r] ?? r)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Aviso rojo arriba del panel cuando el último intento del CPM falló --
 *  equivalente al "CPM failed to change this password" del PVWA, más la
 *  solución conocida de la base de conocimiento si el error coincide. */
function CpmFailureBanner({
  failure,
  kb,
  canChange,
  onAction,
}: {
  failure?: AccountActivity;
  kb?: KbArticle;
  canChange: boolean;
  onAction: (a: CpmAction) => void;
}) {
  const [showKb, setShowKb] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const raw = failure?.Reason ? String(failure.Reason) : "";
  const reason = cleanActivityNote(raw);
  const label = failure ? classify(failure.Action, raw).label.toLowerCase() : "gestión";
  return (
    <div
      role="alert"
      className="mb-4 rounded-lg border border-line-cyberdefense/30 bg-[#fdf1f1] px-4 py-3 text-sm text-navy-900"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger-ink" />
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-danger-ink">
            Falló el último intento del CPM ({label})
          </div>
          {failure && (
            <div className="text-xs text-muted-foreground">
              <span title={fmtDateTime(failure.Date)}>{relTime(failure.Date)}</span>
              {failure.User && <> · {String(failure.User)}</>}
            </div>
          )}
          {reason ? (
            <p className="mt-1 break-words text-xs">
              <span className={cn(!showRaw && "line-clamp-2")}>{showRaw ? raw : reason}</span>
              {raw.length > 140 ? (
                <button
                  type="button"
                  onClick={() => setShowRaw((v) => !v)}
                  className="ml-1 font-semibold text-sky-600 hover:underline"
                >
                  {showRaw ? "ver menos" : "ver completo"}
                </button>
              ) : null}
            </p>
          ) : (
            !failure && (
              <p className="mt-1 text-xs text-muted-foreground">
                CyberArk marca la cuenta con falla de gestión; el motivo no está en la actividad
                reciente.
              </p>
            )
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {canChange && (
              <>
                <Button size="sm" variant="outline" onClick={() => onAction("change")}>
                  <RefreshCw className="size-3.5" />
                  Reintentar cambio
                </Button>
                <Button size="sm" variant="outline" onClick={() => onAction("reconcile")}>
                  <RefreshCcw className="size-3.5" />
                  Reconciliar
                </Button>
              </>
            )}
            {kb && (
              <button
                type="button"
                onClick={() => setShowKb((v) => !v)}
                className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:underline"
                aria-expanded={showKb}
              >
                <BookOpen className="size-3.5" />
                {showKb ? "Ocultar solución conocida" : "Ver solución conocida"}
              </button>
            )}
          </div>
          {kb && showKb && (
            <div className="mt-2 rounded-md border border-border bg-card p-3 text-xs">
              <div className="mb-1 font-semibold text-navy-900">{kb.title}</div>
              {kb.cause && (
                <p className="mb-1 whitespace-pre-line">
                  <span className="font-semibold">Causa: </span>
                  {kb.cause}
                </p>
              )}
              {kb.solution && (
                <p className="whitespace-pre-line">
                  <span className="font-semibold">Solución: </span>
                  {kb.solution}
                </p>
              )}
              {kb.source_url && (
                <a
                  href={kb.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-block font-semibold text-sky-600 hover:underline"
                >
                  Ver artículo
                </a>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const PENDING_LABEL: Record<PendingCpmTask["action"], string> = {
  change: "un cambio de contraseña",
  verify: "una verificación",
  reconcile: "una reconciliación",
};

/** Aviso "tarea del CPM programada" -- equivalente al banner del PVWA. */
function PendingTaskBanner({ task }: { task: PendingCpmTask }) {
  return (
    <div
      role="status"
      className="mb-4 flex items-start gap-2.5 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-navy-900"
    >
      <CalendarClock className="mt-0.5 size-4 shrink-0 text-sky-600" />
      <div className="min-w-0">
        <div className="font-semibold">
          Se programó {PENDING_LABEL[task.action]} para esta cuenta
        </div>
        <div className="text-xs text-muted-foreground">
          <span title={fmtDateTime(task.since)}>{relTime(task.since)}</span>
          {task.by && <> · por {task.by}</>} · el CPM la procesará en su próximo ciclo (puede
          tardar unos minutos). Este aviso se actualiza solo.
        </div>
      </div>
    </div>
  );
}

export function Chip({ tone, children }: { tone: "platform" | "safe"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold",
        tone === "platform"
          ? "border-navy-100 bg-navy-50 text-navy-800"
          : "border-sky-100 bg-sky-50 text-sky-600",
      )}
    >
      {children}
    </span>
  );
}

export function Card({
  icon,
  title,
  headerRight,
  children,
}: {
  icon?: React.ReactNode;
  title: string;
  headerRight?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-muted-foreground">
          {icon}
          {title}
        </h2>
        {headerRight}
      </div>
      {children}
    </div>
  );
}

/** "hace 21 días" en vez de la fecha (que ya va en la línea de abajo);
 *  menos de un día, como `relTime` ("hace 11 min"). */
function daysAgo(when: unknown): string {
  const n = typeof when === "number" ? (when < 1e12 ? when * 1000 : when) : Date.parse(String(when));
  if (!Number.isFinite(n)) return relTime(when);
  const days = Math.floor((Date.now() - n) / 86_400_000);
  if (days < 1) return relTime(when);
  if (days === 1) return "ayer";
  if (days < 365) return `hace ${days} días`;
  const years = Math.floor(days / 365);
  return years === 1 ? "hace más de un año" : `hace más de ${years} años`;
}

function Stat({
  label,
  when,
  note,
  warnIfNever,
  action,
}: {
  label: string;
  when: unknown;
  note?: React.ReactNode;
  /** si nunca ocurrió, se muestra como advertencia con este tooltip */
  warnIfNever?: string;
  action?: { label: string; onClick: () => void };
}) {
  const never = when == null || when === "" || when === 0;
  const warn = never && !!warnIfNever;
  return (
    <div className={cn("flex flex-col gap-1 p-4", warn && "bg-[#fdf7e6]")}>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {never ? (
        <span
          className={cn(
            "flex items-center gap-1 text-sm font-bold",
            warn ? "text-warning-ink" : "text-muted-foreground",
          )}
          title={warnIfNever}
        >
          {warn && <AlertTriangle className="size-3.5" aria-hidden="true" />}
          Nunca
        </span>
      ) : (
        <>
          <span className="text-sm font-bold tabular-nums text-navy-900">{daysAgo(when)}</span>
          <span className="text-[11px] text-muted-foreground">{fmtDateTime(when)}</span>
        </>
      )}
      {note && <span className="text-[11px]">{note}</span>}
      {action &&
        (warn ? (
          <Button size="sm" variant="outline" className="mt-1 self-start" onClick={action.onClick}>
            {action.label}
          </Button>
        ) : (
          <button
            onClick={action.onClick}
            className="mt-0.5 self-start text-[11px] font-bold text-sky-600 hover:underline"
          >
            {action.label} →
          </button>
        ))}
    </div>
  );
}

function Kv({ label, value, full }: { label: string; value: React.ReactNode; full?: boolean }) {
  return (
    <div className={full ? "col-span-2" : undefined}>
      <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </label>
      <div className="break-words text-sm tabular-nums">{value}</div>
    </div>
  );
}

export function AccountDetailPanel({
  tenant,
  accountId,
  object,
  opened,
  onClose,
}: {
  tenant: string;
  accountId?: string;
  object: Account | null;
  opened: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("resumen");
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
  const [editOpen, setEditOpen] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [revealOpen, setRevealOpen] = useState(false);
  const [passwordMode, setPasswordMode] = useState<"vault" | "next" | null>(null);
  const [cpmAction, setCpmAction] = useState<CpmAction | null>(null);
  const [excludeOpen, setExcludeOpen] = useState(false);
  const [removingExemption, setRemovingExemption] = useState<ComplianceExemption | null>(null);

  const acc = encodeURIComponent(accountId ?? "");

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
    enabled: opened,
  });

  // Card "Compliance" del Resumen — cruza PolicyFailures nativo de
  // CyberArk con la política de rotación de LA PLATAFORMA de la cuenta
  // (server-side, ver app.analysis.account_compliance). Sin caché propia
  // del backend (mismo criterio que el resto de app/api/compliance.py):
  // siempre el dato más fresco.
  const compliance = useQuery({
    queryKey: ["compliance-status", tenant, accountId],
    queryFn: () =>
      api<AccountComplianceStatus>(`/tenants/${tenant}/compliance/accounts/${acc}/status`),
    enabled: opened && !!accountId,
  });

  // Excepción manual de compliance activa para esta cuenta, si la hay —
  // ver docs/specs/compliance-exemptions.md. Se comparte con la página
  // Compliance (misma queryKey), así que un alta/baja desde cualquiera de
  // los dos lados invalida al otro.
  const exemptions = useQuery({
    queryKey: ["compliance-exemptions", tenant],
    queryFn: () =>
      api<ComplianceExemptionsResponse>(`/tenants/${tenant}/compliance/exemptions`),
    enabled: opened,
  });
  const exemption = exemptions.data?.exemptions.find((e) => e.account_id === accountId);

  const detail = useQuery({
    queryKey: ["account-detail", tenant, accountId],
    queryFn: () => api<AccountDetailResponse>(`/tenants/${tenant}/accounts/${acc}`),
    enabled: opened && !!accountId,
    refetchInterval: (q) =>
      isInProcess(q.state.data?.account.secretManagement?.status) ? 5000 : false,
  });

  const account: Account | undefined = detail.data?.account ?? object ?? undefined;
  const sm = account?.secretManagement;
  const cpmBusy = isInProcess(sm?.status);

  const activities = useQuery({
    queryKey: ["account-activities", tenant, accountId],
    queryFn: () =>
      api<AccountActivitiesResponse>(`/tenants/${tenant}/accounts/${acc}/activities`),
    enabled: opened && !!accountId,
    refetchInterval: (q) =>
      cpmBusy ? 5000 : q.state.data && pendingCpmTask(q.state.data.activities) ? 15000 : false,
  });
  // Tarea del CPM pedida a mano ("Cambiar ahora"/"Verificar"/"Reconciliar",
  // desde PAM Web Console o desde el PVWA) que el CPM todavía no tomó -- mismo aviso
  // que el "manually scheduled for change" del PVWA. Mientras esté
  // pendiente, la actividad se vuelve a pedir cada 15 s para detectar
  // cuándo el CPM la procesa.
  const pending = !cpmBusy && activities.data ? pendingCpmTask(activities.data.activities) : null;
  const cpmFailed = ["failure", "failed"].includes(String(sm?.status ?? "").toLowerCase());
  const lastFailure = cpmFailed && activities.data ? cpmFailures(activities.data.activities)[0] : undefined;
  // misma query (y caché) que Troubleshooting / la base de conocimiento
  const kbArticles = useQuery({
    queryKey: ["kb-articles"],
    queryFn: () => api<KbArticlesResponse>("/knowledge-base"),
    enabled: opened && cpmFailed,
  });

  const auditTrail = useQuery({
    queryKey: ["audit", tenant, accountId],
    queryFn: () =>
      api<AuditEntry[]>(
        `/tenants/${encodeURIComponent(tenant)}/audit?target=${encodeURIComponent(accountId ?? "")}`,
      ),
    enabled: opened && !!accountId,
  });

  // el endpoint ya exige tenant_admin+ del lado del backend (mismo techo
  // que account.reveal) -- este chequeo inline (en vez de `canTenantAdmin`,
  // recién calculado más abajo) es sólo para no pedirlo de entrada a
  // alguien que igual va a recibir 403.
  const versions = useQuery({
    queryKey: ["account-password-versions", tenant, accountId],
    queryFn: () =>
      api<AccountPasswordVersionsResponse>(
        `/tenants/${tenant}/accounts/${acc}/password-versions`,
      ),
    enabled: opened && !!accountId && roleAtLeast(t.data?.my_role, "tenant_admin"),
  });
  const [revealVersion, setRevealVersion] = useState<number | null>(null);

  if (!accountId) {
    return <Sheet open={opened} onOpenChange={(o) => !o && onClose()} />;
  }

  const role = t.data?.my_role;
  const canOperator = roleAtLeast(role, "operator");
  const canTenantAdmin = roleAtLeast(role, "tenant_admin");
  const canApprover = roleAtLeast(role, "approver");

  const canReveal = canTenantAdmin;
  const canRevealHistory = canTenantAdmin;
  const canEdit = canOperator;
  const canChange = canOperator;
  const canVerify = canOperator;
  const canVaultOnly = canTenantAdmin;
  const canSetNext = canTenantAdmin;
  const canLink = canOperator;
  const canDelete = canApprover;
  const hasMoreMenu = canVerify || canChange || canVaultOnly || canSetNext || canLink;

  const name = String(account?.name ?? accountId);
  const heading =
    account?.userName && account?.address
      ? `${account.userName}@${account.address}`
      : name;

  const remoteMachines = String(
    asObj(account?.remoteMachinesAccess)?.remoteMachines ?? "",
  )
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
  const platformProps = Object.entries(asObj(account?.platformAccountProperties) ?? {});

  function refreshActivity() {
    detail.refetch();
    activities.refetch();
    versions.refetch();
  }

  return (
    <>
      <Sheet open={opened} onOpenChange={(o) => !o && onClose()}>
        <SheetContent className="flex w-full max-w-2xl flex-col gap-0 p-0">
          <SheetTitle className="sr-only">{heading}</SheetTitle>

          <div className="sticky top-0 z-10 flex flex-col gap-3 bg-card px-6 pb-0 pt-6">
            <div className="flex items-center gap-1.5 pr-8 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <KeyRound className="size-3.5 shrink-0" />
              Cuenta · {(t.data?.name ?? tenant).toString().toUpperCase()}
            </div>

            <h1 className="pr-8 text-[21px] font-bold leading-tight text-balance break-words text-navy-900">
              {heading}
            </h1>

            <div className="flex flex-wrap items-center gap-2">
              {account?.platformId && (
                <span className="text-xs text-muted-foreground">
                  Plataforma:{" "}
                  <span className="font-semibold text-navy-900">{String(account.platformId)}</span>
                </span>
              )}
              {account?.safeName && (
                <span className="text-xs text-muted-foreground">
                  Safe: <span className="font-semibold text-navy-900">{String(account.safeName)}</span>
                </span>
              )}
              {sm && <StatusPill status={sm.status} />}
              <button
                onClick={refreshActivity}
                title="Volver a pedir el estado a CyberArk — para ver si una rotación ya terminó sin recargar la página"
                className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:underline"
              >
                <RefreshCw
                  className={cn("size-3", (detail.isFetching || cpmBusy) && "animate-spin")}
                />
                Actualizar
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
              {canReveal && (
                <Button size="sm" onClick={() => setRevealOpen(true)}>
                  <Eye className="size-3.5" />
                  Ver contraseña
                </Button>
              )}
              {canEdit && (
                <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
                  <Pencil className="size-3.5" />
                  Editar
                </Button>
              )}
              {canChange && (
                <Button variant="outline" size="sm" onClick={() => setCpmAction("change")}>
                  <RefreshCw className="size-3.5" />
                  Cambiar ahora
                </Button>
              )}
              {hasMoreMenu && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm">
                      Más
                      <ChevronDown className="size-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-64">
                    {canVerify && (
                      <DropdownMenuItem onClick={() => setCpmAction("verify")}>
                        <div className="flex flex-col gap-0.5">
                          <span>Verificar contraseña</span>
                          <span className="text-[11px] font-normal text-muted-foreground">
                            el CPM chequea contra el target, no cambia nada
                          </span>
                        </div>
                      </DropdownMenuItem>
                    )}
                    {canChange && (
                      <DropdownMenuItem onClick={() => setCpmAction("reconcile")}>
                        <div className="flex flex-col gap-0.5">
                          <span>Reconciliar</span>
                          <span className="text-[11px] font-normal text-muted-foreground">
                            resetea usando la reconcile account
                          </span>
                        </div>
                      </DropdownMenuItem>
                    )}
                    {(canVaultOnly || canSetNext) && (canVerify || canChange) && (
                      <DropdownMenuSeparator />
                    )}
                    {canVaultOnly && (
                      <DropdownMenuItem onClick={() => setPasswordMode("vault")}>
                        <div className="flex flex-col gap-0.5">
                          <span>Cambiar sólo en la Vault</span>
                          <span className="text-[11px] font-normal text-muted-foreground">
                            sin tocar el target
                          </span>
                        </div>
                      </DropdownMenuItem>
                    )}
                    {canSetNext && (
                      <DropdownMenuItem onClick={() => setPasswordMode("next")}>
                        <div className="flex flex-col gap-0.5">
                          <span>Especificar próxima contraseña</span>
                          <span className="text-[11px] font-normal text-muted-foreground">
                            el CPM la usa en su próximo ciclo
                          </span>
                        </div>
                      </DropdownMenuItem>
                    )}
                    {canLink && (
                      <>
                        {(canVaultOnly || canSetNext) && <DropdownMenuSeparator />}
                        <DropdownMenuItem onClick={() => setLinkOpen(true)}>
                          Vincular cuenta de logon/reconcile
                        </DropdownMenuItem>
                      </>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              {canDelete && (
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto border-destructive/30 text-destructive hover:bg-destructive/10"
                  onClick={() => setDeleteOpen(true)}
                >
                  <Trash2 className="size-3.5" />
                  Borrar
                </Button>
              )}
            </div>

            <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
              <TabsList>
                <TabsTrigger value="resumen">Resumen</TabsTrigger>
                <TabsTrigger value="detalles">Detalles</TabsTrigger>
                <TabsTrigger value="actividad">Actividad</TabsTrigger>
                {canTenantAdmin && <TabsTrigger value="versiones">Versiones</TabsTrigger>}
              </TabsList>
            </Tabs>
          </div>

          <div className="px-6 py-5">
            {detail.isError && (
              <Alert variant="destructive" className="mb-4">
                {(detail.error as Error).message}
              </Alert>
            )}
            {pending && <PendingTaskBanner task={pending} />}
            {cpmFailed && (
              <CpmFailureBanner
                failure={lastFailure}
                kb={lastFailure ? matchKbArticle(String(lastFailure.Reason ?? ""), kbArticles.data?.articles ?? []) : undefined}
                canChange={canChange}
                onAction={setCpmAction}
              />
            )}
            {!account && detail.isLoading && (
              <div className="flex justify-center py-10">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            )}

            {account && (
              <Tabs value={tab}>
                <TabsContent value="resumen" className="mt-0 flex flex-col gap-4">
                  {sm && (
                    <PasswordStatusCard
                      sm={sm}
                      compliance={compliance.data}
                      canChange={canChange}
                      canVerify={canVerify}
                      onAction={setCpmAction}
                      exemption={exemption}
                      canOperator={canOperator}
                      onExclude={() => setExcludeOpen(true)}
                      onRemoveExemption={() => exemption && setRemovingExemption(exemption)}
                    />
                  )}

                  <Card
                    title="Actividad reciente"
                    headerRight={
                      <button
                        onClick={() => setTab("actividad")}
                        className="text-xs font-semibold text-sky-600 hover:underline"
                      >
                        Ver todo
                      </button>
                    }
                  >
                    <div className="p-4">
                      {activities.isLoading && (
                        <Loader2 className="size-4 animate-spin text-muted-foreground" />
                      )}
                      {activities.data && (
                        <AccountActivityFeed activities={activities.data.activities.slice(0, 3)} />
                      )}
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="detalles" className="mt-0">
                  <Card title="Propiedades de la cuenta">
                    <div className="grid grid-cols-2 gap-x-5 gap-y-3.5 p-4">
                      <Kv label="Nombre de cuenta" value={dash(account.name)} full />
                      <Kv label="Usuario" value={dash(account.userName)} />
                      <Kv label="Address" value={dash(account.address)} />
                      <Kv label="Plataforma" value={dash(account.platformId)} />
                      <Kv label="Safe" value={dash(account.safeName)} />
                      <Kv
                        label="Tipo de secreto"
                        value={account.secretType === "password" ? "Contraseña" : dash(account.secretType)}
                      />
                      <Kv
                        label="Gestión automática"
                        value={sm ? (sm.automaticManagementEnabled === false ? "No" : "Sí") : "—"}
                      />
                      <Kv
                        label="Creada"
                        value={
                          <>
                            {fmtDate(account.createdTime)}{" "}
                            <span className="text-muted-foreground">
                              · {relTime(account.createdTime)}
                            </span>
                          </>
                        }
                        full
                      />
                      {remoteMachines.length > 0 && (
                        <Kv
                          label="Remote machines"
                          value={
                            <div className="flex flex-col gap-0.5 font-mono text-[13px]">
                              {remoteMachines.map((m) => (
                                <span key={m}>{m}</span>
                              ))}
                            </div>
                          }
                          full
                        />
                      )}
                      {platformProps.length > 0 && (
                        <Kv
                          label="Propiedades de plataforma"
                          value={
                            <div className="flex flex-wrap gap-1.5">
                              {platformProps.map(([k, v]) => (
                                <span
                                  key={k}
                                  className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground"
                                >
                                  <b className="font-semibold text-foreground">{k}</b> · {dash(v)}
                                </span>
                              ))}
                            </div>
                          }
                          full
                        />
                      )}
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="actividad" className="mt-0">
                  <Card
                    icon={<RefreshCcw className="size-3.5 text-sky-600" />}
                    title="Línea de tiempo"
                    headerRight={
                      <button
                        onClick={refreshActivity}
                        className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:underline"
                      >
                        <RefreshCw
                          className={cn("size-3", (activities.isFetching || cpmBusy) && "animate-spin")}
                        />
                        {cpmBusy ? "CPM en curso…" : "Actualizar"}
                      </button>
                    }
                  >
                    <div className="flex gap-1.5 px-4 pt-3.5">
                      {(["all", "cyberark", "pam"] as ActivityFilter[]).map((f) => (
                        <button
                          key={f}
                          onClick={() => setActivityFilter(f)}
                          className={cn(
                            "rounded-full border px-2.5 py-1 text-xs font-semibold",
                            activityFilter === f
                              ? "border-navy-900 bg-navy-900 text-white"
                              : "border-border text-muted-foreground hover:bg-muted",
                          )}
                        >
                          {f === "all" ? "Todo" : f === "cyberark" ? "CyberArk" : "PAM Web Console"}
                        </button>
                      ))}
                    </div>

                    <div className="flex flex-col gap-4 p-4">
                      {(activityFilter === "all" || activityFilter === "cyberark") && (
                        <div className="flex flex-col gap-2">
                          {activityFilter === "all" && (
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                              CyberArk
                            </p>
                          )}
                          {activities.isLoading && (
                            <Loader2 className="size-4 animate-spin text-muted-foreground" />
                          )}
                          {activities.isError && (
                            <Alert variant="destructive">{(activities.error as Error).message}</Alert>
                          )}
                          {activities.data && (
                            <AccountActivityFeed activities={activities.data.activities} />
                          )}
                        </div>
                      )}

                      {(activityFilter === "all" || activityFilter === "pam") && (
                        <div className="flex flex-col gap-2">
                          {activityFilter === "all" && (
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                              PAM Web Console
                            </p>
                          )}
                          {auditTrail.isLoading && (
                            <Loader2 className="size-4 animate-spin text-muted-foreground" />
                          )}
                          {auditTrail.data && <AuditTrailFeed entries={auditTrail.data} />}
                        </div>
                      )}
                    </div>
                  </Card>
                </TabsContent>

                {canTenantAdmin && (
                  <TabsContent value="versiones" className="mt-0">
                    <Card icon={<History className="size-3.5 text-sky-600" />} title="Historial de contraseñas">
                      {!canRevealHistory && (
                        <p className="px-4 pt-3.5 text-xs text-muted-foreground">
                          Ver la lista no necesita nada extra, pero revelar el valor de una
                          versión requiere habilitar la feature{" "}
                          <code className="font-mono">account.reveal.history</code> para este
                          tenant.
                        </p>
                      )}
                      {versions.isLoading && (
                        <Loader2 className="m-4 size-4 animate-spin text-muted-foreground" />
                      )}
                      {versions.isError && (
                        <Alert variant="destructive" className="m-4">
                          {(versions.error as Error).message}
                        </Alert>
                      )}
                      {versions.data && versions.data.versions.length === 0 && (
                        <p className="p-4 text-sm text-muted-foreground">
                          Sin versiones anteriores.
                        </p>
                      )}
                      {versions.data && versions.data.versions.length > 0 && (
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-border text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                              <th className="px-4 py-2">Fecha y hora</th>
                              <th className="px-4 py-2">Modificado por</th>
                              <th className="px-4 py-2" />
                            </tr>
                          </thead>
                          <tbody>
                            {versions.data.versions.map((v, i) => (
                              <tr
                                key={v.VersionID ?? i}
                                className="border-b border-border/60 last:border-0"
                              >
                                <td className="px-4 py-2 tabular-nums">
                                  {fmtDateTime(v.ModificationTime)}
                                </td>
                                <td className="px-4 py-2">{dash(v.ModifiedBy)}</td>
                                <td className="px-4 py-2 text-right">
                                  {canRevealHistory && v.VersionID !== undefined && (
                                    <button
                                      onClick={() => setRevealVersion(v.VersionID!)}
                                      className="text-xs font-semibold text-sky-600 hover:underline"
                                    >
                                      Revelar
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </Card>
                  </TabsContent>
                )}
              </Tabs>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <RevealPasswordDialog
        open={revealOpen}
        onClose={() => setRevealOpen(false)}
        path={`/tenants/${tenant}/accounts/${acc}/reveal`}
        title={`Revelar contraseña — ${name}`}
      />

      <RevealPasswordDialog
        open={revealVersion !== null}
        onClose={() => setRevealVersion(null)}
        path={`/tenants/${tenant}/accounts/${acc}/reveal-version`}
        title={`Revelar versión anterior — ${name}`}
        extraBody={{ version: revealVersion }}
      />

      <AccountEditDialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        tenant={tenant}
        accountId={accountId}
        account={(account ?? {}) as Obj}
        onDone={() => setEditOpen(false)}
      />

      <AccountLinkDialog
        open={linkOpen}
        onClose={() => setLinkOpen(false)}
        tenant={tenant}
        accountId={accountId}
        onDone={() => setLinkOpen(false)}
      />

      {passwordMode && (
        <AccountPasswordDialog
          open={passwordMode !== null}
          onClose={() => setPasswordMode(null)}
          tenant={tenant}
          accountId={accountId}
          mode={passwordMode}
          onDone={() => setPasswordMode(null)}
        />
      )}

      <ComplianceExemptionCreateDialog
        open={excludeOpen}
        onClose={() => setExcludeOpen(false)}
        tenant={tenant}
        accountId={accountId}
      />
      <ComplianceExemptionDeleteDialog
        exemption={removingExemption}
        onClose={() => setRemovingExemption(null)}
        tenant={tenant}
      />

      <AccountDeleteDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        tenant={tenant}
        accountId={accountId}
        accountName={name}
        onDeleted={() => {
          setDeleteOpen(false);
          onClose();
        }}
      />

      <DryRunDialog
        open={cpmAction !== null}
        onClose={() => setCpmAction(null)}
        path={`/tenants/${tenant}/accounts/${acc}/${cpmAction ?? "verify"}`}
        changes={{}}
        title={cpmAction ? CPM_LABEL[cpmAction] : ""}
        onDone={() => {
          setCpmAction(null);
          invalidateAccount(qc, tenant, accountId);
        }}
      />
    </>
  );
}
