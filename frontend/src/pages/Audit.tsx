import { useQuery } from "@tanstack/react-query";
import { Download, Loader2, ShieldCheck, ShieldX } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AuditEntryPanel } from "@/components/AuditEntryPanel";
import { DataTable, type Column } from "@/components/DataTable";
import { MultiSelectField } from "@/components/MultiSelectField";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast, toastError } from "@/components/ui/toast";
import { api, apiBlob } from "@/lib/api";
import { kindBadge, resultBadge } from "@/lib/auditFormat";
import { saveBlob } from "@/lib/download";
import { dash, fmtDateTime, relTime } from "@/lib/format";
import { roleAtLeast } from "@/lib/roles";
import type { AuditEntry, AuditPage, AuditSummary, AuditVerifyResult, TenantSummary } from "@/lib/types";

const CATEGORIES = [
  "Cuentas",
  "Safes",
  "Plataformas",
  "Contraseñas y secretos",
  "Sesiones y grabaciones",
  "Identity",
  "Alero",
  "SIA",
  "Reportes",
  "Acceso a PAM Web Console",
  "Administración",
];

const RESULT_LABELS: Record<string, string> = {
  ok: "ok",
  denied: "denegado",
  error: "error",
  pending_approval: "pendiente de aprobación",
  locked: "bloqueado",
};
const RESULT_OPTIONS = Object.keys(RESULT_LABELS);

/** Tarjetas del resumen (R3) — cada una aplica el filtro que muestra ese
 *  conteo. `pending_approvals` no sale de audit_log (es la cola de
 *  `/approvals`), así que en vez de filtrar navega a esa página. */
const SUMMARY_CARDS: {
  key: keyof AuditSummary["counts"];
  label: string;
  actions?: string[];
  result?: string[];
}[] = [
  {
    key: "passwords_revealed",
    label: "Contraseñas reveladas",
    actions: ["account.reveal", "account.reveal.bulk", "account.reveal.history"],
  },
  {
    key: "recordings_downloaded",
    label: "Grabaciones descargadas",
    actions: ["recording.play", "recording.play.mp4"],
  },
  {
    key: "credential_backups_accessed",
    label: "Backups de credenciales",
    actions: ["account.backup.reveal", "account.backup.restore"],
  },
  { key: "access_denied", label: "Accesos denegados", result: ["denied"] },
  { key: "errors", label: "Errores", result: ["error"] },
  {
    key: "rollbacks_failed",
    label: "Rollbacks fallidos",
    actions: ["account.move.rollback_failed", "platform.psm.rollback_failed"],
  },
  {
    key: "failed_logins",
    label: "Inicios de sesión fallidos",
    actions: ["auth.login"],
    result: ["denied", "error"],
  },
  { key: "pending_approvals", label: "Aprobaciones pendientes" },
];

/** Rastro de auditoría de PAM Web Console — distinto del audit nativo de CyberArk
 *  (Audit and Reports / System activities de PVWA, que registra lo que
 *  hace el service account): acá se registra cada acción real de un
 *  OPERADOR de PAM Web Console, escritura o lectura, con `reason` cuando aplica.
 *  Lecturas ocultas por defecto (Tanda 1 de docs/specs/done/audit-page.md) —
 *  "Incluir consultas" las muestra. Paginado por cursor (`before_id`),
 *  "Cargar más" pide la página siguiente y la acumula en pantalla. */
export default function Audit() {
  const [params, setParams] = useSearchParams();
  const tenantId = params.get("tenant_id") ?? "";
  const operator = params.get("operator") ?? "";
  const category = params.get("category") ?? "";
  const action = params.get("action") ?? "";
  const target = params.get("target") ?? "";
  const since = params.get("since") ?? "";
  const until = params.get("until") ?? "";
  const result = params.get("result") ?? "";
  const requestId = params.get("request_id") ?? "";
  const includeReads = params.get("include_reads") === "1";
  const includeDryRun = params.get("include_dry_run") === "1";

  const [sel, setSel] = useState<AuditEntry | null>(null);
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);

  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });
  const auditTenants = (tenants.data ?? []).filter((t) => roleAtLeast(t.my_role, "tenant_admin"));

  function buildParams(beforeId: number | null, actionsOverride?: string[]) {
    const p = new URLSearchParams({ limit: "100" });
    if (tenantId) p.set("tenant_id", tenantId);
    if (operator) p.set("operator", operator);
    if (category) p.set("category", category);
    if (target) p.set("target", target);
    if (since) p.set("since", `${since}T00:00:00`);
    if (until) p.set("until", `${until}T23:59:59`);
    for (const r of result ? result.split(",") : []) p.append("result", r);
    const actions = actionsOverride ?? (action ? [action] : []);
    for (const a of actions) p.append("action", a);
    if (requestId) p.set("request_id", requestId);
    if (includeReads) p.set("include_reads", "true");
    if (includeDryRun) p.set("include_dry_run", "true");
    if (beforeId) p.set("before_id", String(beforeId));
    return p;
  }

  const queryKey = [
    "audit",
    tenantId,
    operator,
    category,
    action,
    target,
    since,
    until,
    result,
    requestId,
    includeReads,
    includeDryRun,
  ];

  const q = useQuery({
    queryKey,
    queryFn: () => api<AuditPage>(`/audit?${buildParams(null).toString()}`),
  });

  // los filtros cambiaron -> se reinicia la paginación acumulada
  useEffect(() => {
    if (q.data) {
      setItems(q.data.items);
      setCursor(q.data.next_before_id);
    }
  }, [q.data]);

  const [loadingMore, setLoadingMore] = useState(false);
  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await api<AuditPage>(`/audit?${buildParams(cursor).toString()}`);
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_before_id);
    } finally {
      setLoadingMore(false);
    }
  }

  const [exporting, setExporting] = useState(false);
  async function exportAudit(format: "csv" | "json") {
    setExporting(true);
    try {
      const p = buildParams(null);
      p.delete("limit");
      p.set("format", format);
      const { blob, filename } = await apiBlob(`/audit/export?${p.toString()}`);
      saveBlob(blob, filename);
      toast({ title: `${format.toUpperCase()} descargado`, description: filename });
    } catch (e) {
      toastError("No se pudo exportar", e);
    } finally {
      setExporting(false);
    }
  }

  const summaryQ = useQuery({
    queryKey: ["audit-summary", tenantId],
    queryFn: () =>
      api<AuditSummary>(`/audit/summary${tenantId ? `?tenant_id=${tenantId}` : ""}`),
  });

  const verify = useQuery({
    queryKey: ["audit-verify"],
    queryFn: () => api<AuditVerifyResult>("/audit/verify"),
    enabled: false,
  });

  function set(key: string, value: string) {
    setParams(
      (p) => {
        if (value) p.set(key, value);
        else p.delete(key);
        return p;
      },
      { replace: true },
    );
  }

  function applyCard(actions?: string[], resultValues?: string[]) {
    setParams(
      (p) => {
        p.delete("action");
        p.delete("result");
        if (actions?.length === 1) p.set("action", actions[0]);
        if (resultValues?.length) p.set("result", resultValues.join(","));
        if (actions?.length) p.set("include_reads", "1");
        return p;
      },
      { replace: true },
    );
  }

  /** R9: clic en un operador o un target filtra la tabla por ese valor. */
  function filterByOperator(value: string) {
    setSel(null);
    set("operator", value);
  }
  function filterByTarget(value: string) {
    setSel(null);
    set("target", value);
  }
  /** "Ver todo el mismo request" (R8) — aísla ese request, sin arrastrar
   *  el resto de los filtros activos. */
  function filterByRequestId(value: string) {
    setSel(null);
    setParams(
      () => {
        const next = new URLSearchParams();
        next.set("request_id", value);
        return next;
      },
      { replace: true },
    );
  }

  /** R11: abre el registro roto que señala "Verificar integridad", sin
   *  depender de que esté en la página actual ni de sus filtros (antes de
   *  `include_reads`/`include_dry_run` para no perderlo si era una
   *  consulta oculta o un dry-run). */
  async function openById(id: number) {
    const page = await api<AuditPage>(
      `/audit?before_id=${id + 1}&limit=1&include_reads=true&include_dry_run=true`,
    );
    if (page.items[0]?.id === id) setSel(page.items[0]);
  }

  const hasFilter =
    !!tenantId ||
    !!operator ||
    !!category ||
    !!action ||
    !!target ||
    !!since ||
    !!until ||
    !!result ||
    !!requestId;

  const columns: Column<AuditEntry>[] = [
    {
      key: "ts",
      header: "Fecha y hora",
      render: (e) => <span title={fmtDateTime(e.ts)}>{relTime(e.ts)}</span>,
      sortValue: (e) => e.ts,
    },
    {
      key: "operator",
      header: "Operador",
      render: (e) => (
        <span>
          <button
            type="button"
            className="underline decoration-dotted underline-offset-2 hover:text-primary"
            onClick={(ev) => {
              ev.stopPropagation();
              filterByOperator(e.operator);
            }}
          >
            {dash(e.operator)}
          </button>{" "}
          <span className="text-muted-foreground">· {e.role}</span>
        </span>
      ),
    },
    { key: "tenant_id", header: "Tenant", render: (e) => dash(e.tenant_id) },
    { key: "category", header: "Categoría", render: (e) => e.category },
    { key: "action", header: "Acción", render: (e) => <span title={e.action}>{e.label}</span> },
    { key: "kind", header: "Tipo", render: (e) => kindBadge(e.kind), filterable: false },
    {
      key: "target",
      header: "Target",
      render: (e) =>
        e.target ? (
          <button
            type="button"
            className="underline decoration-dotted underline-offset-2 hover:text-primary"
            onClick={(ev) => {
              ev.stopPropagation();
              filterByTarget(e.target!);
            }}
          >
            {dash(e.target_label)}
          </button>
        ) : (
          dash(e.target_label)
        ),
    },
    {
      key: "result",
      header: "Resultado",
      filterable: false,
      render: (e) => (
        <span className="flex items-center gap-1.5">
          {resultBadge(e.result)}
          {e.dry_run && <Badge variant="neutral">dry-run</Badge>}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">Auditoría</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Rastro de auditoría de PAM Web Console — cada acción de un operador, con razón cuando aplica.
            Cadena hash-encadenada, append-only.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" disabled={exporting}>
                {exporting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Download className="size-3.5" />
                )}
                {exporting ? "Exportando…" : "Exportar"}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => exportAudit("csv")}>CSV</DropdownMenuItem>
              <DropdownMenuItem onClick={() => exportAudit("json")}>JSON</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            variant="outline"
            size="sm"
            onClick={() => verify.refetch()}
            disabled={verify.isFetching}
          >
            {verify.isFetching ? "Verificando…" : "Verificar integridad"}
          </Button>
        </div>
      </div>

      {verify.data && (
        <Alert variant={verify.data.intact ? "success" : "destructive"}>
          <span className="flex flex-wrap items-center gap-1.5">
            {verify.data.intact ? (
              <ShieldCheck className="size-4 shrink-0" />
            ) : (
              <ShieldX className="size-4 shrink-0" />
            )}
            {verify.data.intact ? (
              <>
                Cadena de hashes intacta — {verify.data.checked} registro(s) verificados (id{" "}
                {verify.data.first_id}–{verify.data.last_id}).
              </>
            ) : (
              <>
                Cadena rota a partir del registro{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => openById(verify.data!.first_broken_id!)}
                >
                  #{verify.data.first_broken_id}
                </button>
                .
              </>
            )}
            <span className="text-xs text-muted-foreground">
              Verificado {fmtDateTime(verify.data.verified_at)}.
            </span>
          </span>
        </Alert>
      )}

      {summaryQ.data && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          {SUMMARY_CARDS.map((c) => (
            <button
              key={c.key}
              type="button"
              onClick={() => applyCard(c.actions, c.result)}
              className="rounded-md border border-border bg-card p-2 text-left transition-colors hover:border-primary"
            >
              <div className="text-lg font-bold text-navy-900">{summaryQ.data.counts[c.key]}</div>
              <div className="text-[11px] leading-tight text-muted-foreground">{c.label}</div>
            </button>
          ))}
        </div>
      )}

      <DataTable
        rows={items}
        columns={columns}
        tableId="audit-log"
        getRowKey={(e) => e.id}
        minWidth={1000}
        onRowClick={setSel}
        toolbar={
          <div className="flex flex-wrap items-center gap-2">
            {requestId && (
              <button type="button" onClick={() => set("request_id", "")} className="cursor-pointer">
                <Badge variant="info" className="flex items-center gap-1">
                  Request: {requestId} ✕
                </Badge>
              </button>
            )}
            <Select value={tenantId || "__all"} onValueChange={(v) => set("tenant_id", v === "__all" ? "" : v)}>
              <SelectTrigger aria-label="Filtrar por tenant" className="h-8 w-40 text-xs">
                <SelectValue placeholder="Tenant" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all">Todos los tenants</SelectItem>
                {auditTenants.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={category || "__all"} onValueChange={(v) => set("category", v === "__all" ? "" : v)}>
              <SelectTrigger aria-label="Filtrar por categoría" className="h-8 w-40 text-xs">
                <SelectValue placeholder="Categoría" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all">Todas las categorías</SelectItem>
                {CATEGORIES.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <MultiSelectField
              options={RESULT_OPTIONS}
              labels={RESULT_LABELS}
              value={result}
              onChange={(v) => set("result", v)}
              placeholder="Resultado"
              ariaLabel="Filtrar por resultado"
              className="h-8 w-36 text-xs"
            />
            <Input
              placeholder="operador"
              value={operator}
              onChange={(e) => set("operator", e.target.value)}
              className="h-8 w-32 text-xs"
            />
            <Input
              placeholder="acción (código exacto)"
              value={action}
              onChange={(e) => set("action", e.target.value)}
              className="h-8 w-44 text-xs"
            />
            <Input
              placeholder="target"
              value={target}
              onChange={(e) => set("target", e.target.value)}
              className="h-8 w-32 text-xs"
            />
            <input
              type="date"
              value={since}
              onChange={(e) => set("since", e.target.value)}
              title="desde"
              className="h-8 rounded-md border border-input bg-card px-2 text-xs text-muted-foreground"
            />
            <input
              type="date"
              value={until}
              onChange={(e) => set("until", e.target.value)}
              title="hasta"
              className="h-8 rounded-md border border-input bg-card px-2 text-xs text-muted-foreground"
            />
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Switch checked={includeReads} onCheckedChange={(v) => set("include_reads", v ? "1" : "")} />
              Incluir consultas
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Switch checked={includeDryRun} onCheckedChange={(v) => set("include_dry_run", v ? "1" : "")} />
              Incluir previsualizaciones
            </label>
            {hasFilter && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  setParams(new URLSearchParams(), { replace: true })
                }
              >
                Limpiar
              </Button>
            )}
          </div>
        }
      />
      {cursor && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Cargando…" : "Cargar más"}
          </Button>
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      <Dialog open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <DialogContent>
          <DialogTitle>Detalle del registro #{sel?.id}</DialogTitle>
          {sel && (
            <AuditEntryPanel
              entry={sel}
              onFilterOperator={filterByOperator}
              onFilterTarget={filterByTarget}
              onFilterRequestId={filterByRequestId}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
