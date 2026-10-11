import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search, ShieldOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AccountDetailPanel } from "@/components/AccountDetailPanel";
import {
  ComplianceExemptionCreateDialog,
  ComplianceExemptionDeleteDialog,
} from "@/components/ComplianceExemptionDialogs";
import { Column, DataTable } from "@/components/DataTable";
import { TenantScope } from "@/components/TenantScope";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { dash, fmtDate, fmtDateTime } from "@/lib/format";
import { roleAtLeast } from "@/lib/roles";
import { cn } from "@/lib/utils";
import type {
  ComplianceAccountRow,
  ComplianceAccountsResponse,
  ComplianceExemption,
  ComplianceExemptionsResponse,
  ComplianceReason,
  ComplianceSearchResponse,
  ComplianceSearchRow,
  TenantSummary,
} from "@/lib/types";

const REASONS: { value: ComplianceReason; label: string; hint: string }[] = [
  {
    value: "PolicyFailures",
    label: "Política vencida",
    hint: "CyberArk marca la cuenta como no-compliant contra la política de rotación de su plataforma.",
  },
  {
    value: "ActiveOverdue",
    label: "Gestionada pero vencida",
    hint: "gestión automática activa, pero sin rotar dentro del plazo de su plataforma (o el default del tenant) — verificación propia de PAM Web Console, independiente del PolicyFailures nativo de CyberArk.",
  },
  {
    value: "DisabledStale",
    label: "Deshabilitada y sin rotar",
    hint: "gestión automática apagada y sin rotar dentro del plazo de su plataforma (o el default del tenant) — CyberArk NO la cuenta como fallo de política.",
  },
  {
    value: "DisableByUser",
    label: "Deshabilitada por un operador",
    hint: 'CyberArk: "Disabled password by user".',
  },
  {
    value: "DisableByCPM",
    label: "Deshabilitada por el CPM",
    hint: "el CPM agotó los reintentos y desactivó la gestión automática.",
  },
  {
    value: "Locked",
    label: "Bloqueada",
    hint: 'CyberArk: "Locked".',
  },
];

function columns(showAge: boolean): Column<ComplianceAccountRow>[] {
  const cols: Column<ComplianceAccountRow>[] = [
    { key: "name", header: "Nombre de cuenta", render: (a) => dash(a.name) },
    { key: "userName", header: "Usuario", render: (a) => dash(a.userName) },
    { key: "address", header: "Address", render: (a) => dash(a.address) },
    { key: "safeName", header: "Safe", render: (a) => dash(a.safeName) },
    { key: "platformId", header: "Plataforma", render: (a) => dash(a.platformId) },
    {
      key: "lastModifiedTime",
      header: "Última modificación",
      render: (a) => fmtDate(a.lastModifiedTime),
      sortValue: (a) => a.lastModifiedTime ?? null,
    },
  ];
  if (showAge) {
    cols.push({
      key: "ageDays",
      header: "Antigüedad",
      render: (a) => (a.ageDays == null ? "Nunca rotada" : `${a.ageDays} días`),
      sortValue: (a) => a.ageDays ?? Number.POSITIVE_INFINITY,
    });
    cols.push({
      key: "thresholdDays",
      header: "Umbral aplicado",
      render: (a) => dash(a.thresholdDays),
      sortValue: (a) => a.thresholdDays ?? null,
    });
  }
  return cols;
}

const AGE_AWARE_REASONS: ComplianceReason[] = ["DisabledStale", "ActiveOverdue"];

function BreakdownView({ tenant }: { tenant: string }) {
  const [params, setParams] = useSearchParams();
  const initial = params.get("reason") as ComplianceReason | null;
  const [reason, setReason] = useState<ComplianceReason>(
    initial && REASONS.some((r) => r.value === initial) ? initial : "PolicyFailures",
  );
  const [sel, setSel] = useState<ComplianceAccountRow | null>(null);

  const q = useQuery({
    queryKey: ["compliance", tenant, reason],
    queryFn: () =>
      api<ComplianceAccountsResponse>(`/tenants/${tenant}/compliance/accounts?reason=${reason}`),
    staleTime: 60_000,
  });

  function selectReason(r: ComplianceReason) {
    setReason(r);
    setParams(
      (p) => {
        p.set("reason", r);
        return p;
      },
      { replace: true },
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-1">
        {REASONS.map((r) => (
          <button
            key={r.value}
            title={r.hint}
            onClick={() => selectReason(r.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm transition-colors",
              reason === r.value
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:text-foreground",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        {REASONS.find((r) => r.value === reason)?.hint}
      </p>

      {q.isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {q.isError && <p className="text-sm text-destructive">{(q.error as Error).message}</p>}

      {q.data && q.data.accounts.length === 0 && (
        <p className="text-sm text-muted-foreground">Sin cuentas en esta categoría.</p>
      )}

      {q.data && q.data.accounts.length > 0 && (
        <DataTable
          rows={q.data.accounts}
          columns={columns(AGE_AWARE_REASONS.includes(reason))}
          tableId="compliance-accounts"
          getRowKey={(a) => a.id}
          minWidth={900}
          onRowClick={setSel}
          filterPlaceholder="buscar por nombre / usuario / address / safe / plataforma"
        />
      )}

      <AccountDetailPanel
        opened={!!sel}
        onClose={() => setSel(null)}
        object={null}
        tenant={tenant}
        accountId={sel?.id}
      />
    </div>
  );
}

const REASON_BADGE_VARIANT: Record<ComplianceReason, BadgeProps["variant"]> = {
  PolicyFailures: "danger",
  DisabledStale: "danger",
  Locked: "danger",
  ActiveOverdue: "warning",
  DisableByCPM: "warning",
  DisableByUser: "neutral",
};

const SEARCH_MIN_CHARS = 2;
const SEARCH_DEBOUNCE_MS = 300;

/** Busca cuentas por nombre/usuario/address/safe y muestra, por cuenta,
 *  TODAS las categorías de compliance en las que cae a la vez — a
 *  diferencia del desglose por motivo, que muestra una categoría por vez.
 *  Pedido de Alex para poder tipear ej. "sa_tivit" y ver de un vistazo por
 *  qué está restando del % de Compliance. */
function SearchView({ tenant }: { tenant: string }) {
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sel, setSel] = useState<ComplianceSearchRow | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(term.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [term]);

  const enabled = debounced.length >= SEARCH_MIN_CHARS;
  const q = useQuery({
    queryKey: ["compliance-search", tenant, debounced],
    queryFn: () =>
      api<ComplianceSearchResponse>(
        `/tenants/${tenant}/compliance/search?q=${encodeURIComponent(debounced)}`,
      ),
    enabled,
    staleTime: 30_000,
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          aria-label="Buscar cuenta por nombre, usuario, address o safe"
          className="w-full rounded-md border border-input bg-card py-1.5 pl-8 pr-2 text-sm"
          placeholder="Buscar cuenta (ej. sa_tivit)…"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
      </div>

      {term.trim().length > 0 && term.trim().length < SEARCH_MIN_CHARS && (
        <p className="text-sm text-muted-foreground">
          Escriba al menos {SEARCH_MIN_CHARS} letras.
        </p>
      )}
      {enabled && q.isFetching && <p className="text-sm text-muted-foreground">Buscando…</p>}
      {q.isError && <p className="text-sm text-destructive">{(q.error as Error).message}</p>}
      {enabled && q.data && q.data.accounts.length === 0 && (
        <p className="text-sm text-muted-foreground">Sin cuentas que coincidan con "{debounced}".</p>
      )}

      {enabled && q.data && q.data.accounts.length > 0 && (
        <div className="flex flex-col gap-2">
          {q.data.accounts.map((a) => (
            <button
              key={a.id}
              onClick={() => setSel(a)}
              className="flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:bg-accent"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="font-medium">{dash(a.name)}</span>
                <span className="text-xs text-muted-foreground">
                  {dash(a.userName)} · {dash(a.safeName)} · {dash(a.platformId)}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {a.compliant ? (
                  <Badge variant="success">Compliant</Badge>
                ) : (
                  a.reasons.map((r) => (
                    <Badge
                      key={r}
                      variant={REASON_BADGE_VARIANT[r]}
                      title={REASONS.find((x) => x.value === r)?.hint}
                    >
                      {REASONS.find((x) => x.value === r)?.label ?? r}
                    </Badge>
                  ))
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      <AccountDetailPanel
        opened={!!sel}
        onClose={() => setSel(null)}
        object={null}
        tenant={tenant}
        accountId={sel?.id}
      />
    </div>
  );
}

function exemptionColumns(canOperator: boolean, onRemove: (e: ComplianceExemption) => void) {
  const cols: Column<ComplianceExemption>[] = [
    { key: "account_id", header: "ID de cuenta" },
    { key: "reason", header: "Motivo", render: (e) => <span className="break-words">{e.reason}</span> },
    { key: "created_by", header: "Excluida por" },
    {
      key: "created_at",
      header: "Fecha",
      render: (e) => fmtDateTime(e.created_at),
      sortValue: (e) => e.created_at,
    },
  ];
  if (canOperator) {
    cols.push({
      key: "actions",
      header: "",
      filterable: false,
      render: (e) => (
        <Button variant="ghost" size="sm" onClick={() => onRemove(e)}>
          Quitar
        </Button>
      ),
    });
  }
  return cols;
}

function candidateColumns(): Column<ComplianceAccountRow>[] {
  return [
    { key: "name", header: "Nombre de cuenta", render: (a) => dash(a.name) },
    { key: "userName", header: "Usuario", render: (a) => dash(a.userName) },
    { key: "safeName", header: "Safe", render: (a) => dash(a.safeName) },
    { key: "platformId", header: "Plataforma", render: (a) => dash(a.platformId) },
    {
      key: "ageDays",
      header: "Antigüedad",
      render: (a) => (a.ageDays == null ? "Nunca rotada" : `${a.ageDays} días`),
      sortValue: (a) => a.ageDays ?? Number.POSITIVE_INFINITY,
    },
  ];
}

/** Selección masiva de candidatas (pedido de Globex, 2026-09-29): todas
 *  las cuentas `DisabledStale` que NO están deshabilitadas por el CPM
 *  (`DisableByCPM`) — CyberArk marca ambas con el mismo mensaje genérico
 *  ("Automatic management disabled by the user"), así que no sirve para
 *  distinguir "pedido del cliente" de "olvidada" (caso real de Initech, ver
 *  docs/specs/compliance-exemptions.md) — por eso NO se excluyen solas, el
 *  operador confirma cuáles corresponden tildándolas acá. Cada una queda
 *  auditada individualmente igual que si se cargara una por una. */
function BulkExemptSection({ tenant }: { tenant: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [running, setRunning] = useState(false);

  const stale = useQuery({
    queryKey: ["compliance", tenant, "DisabledStale"],
    queryFn: () =>
      api<ComplianceAccountsResponse>(
        `/tenants/${tenant}/compliance/accounts?reason=DisabledStale`,
      ),
    enabled: open,
  });
  const cpmDisabled = useQuery({
    queryKey: ["compliance", tenant, "DisableByCPM"],
    queryFn: () =>
      api<ComplianceAccountsResponse>(
        `/tenants/${tenant}/compliance/accounts?reason=DisableByCPM`,
      ),
    enabled: open,
  });

  const cpmIds = new Set((cpmDisabled.data?.accounts ?? []).map((a) => a.id));
  const candidates = (stale.data?.accounts ?? []).filter((a) => !cpmIds.has(a.id));
  const loading = stale.isLoading || cpmDisabled.isLoading;

  async function run() {
    setRunning(true);
    const results = await Promise.allSettled(
      [...selected].map((account_id) =>
        api(`/tenants/${tenant}/compliance/exemptions`, {
          method: "POST",
          body: { account_id, reason: reason.trim() },
        }),
      ),
    );
    setRunning(false);
    const ok = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - ok;
    for (const key of [
      ["compliance-exemptions", tenant],
      ["compliance", tenant],
      ["dashboard", tenant],
    ]) {
      qc.invalidateQueries({ queryKey: key });
    }
    toast({
      title: `${ok} cuenta(s) excluida(s)`,
      description: failed > 0 ? `${failed} no se pudieron excluir (revisar)` : undefined,
      variant: failed > 0 ? "error" : "success",
    });
    if (failed === 0) {
      setOpen(false);
      setSelected(new Set());
      setReason("");
    }
  }

  if (!open) {
    return (
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Seleccionar candidatas en lote
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <p className="max-w-2xl text-xs text-muted-foreground">
          Cuentas con gestión deshabilitada que hoy restan del % de Compliance, EXCLUYENDO las
          que deshabilitó el CPM tras fallar (esas no se ofrecen acá — no son pedido del
          cliente). Tilde sólo las que correspondan a este caso; no se puede inferir del dato de
          CyberArk sin confirmación humana.
        </p>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cerrar
        </Button>
      </div>

      {loading && <p className="text-sm text-muted-foreground">Cargando candidatas…</p>}
      {!loading && candidates.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Sin candidatas — no hay cuentas DisabledStale fuera de las que deshabilitó el CPM.
        </p>
      )}
      {!loading && candidates.length > 0 && (
        <DataTable
          rows={candidates}
          columns={candidateColumns()}
          tableId="compliance-exemption-candidates"
          getRowKey={(a) => a.id}
          minWidth={700}
          selectedIds={selected}
          onSelectedIdsChange={setSelected}
          filterPlaceholder="buscar por nombre / usuario / safe / plataforma"
        />
      )}

      {selected.size > 0 && (
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <label className="text-xs font-medium text-muted-foreground">
            Motivo común para las {selected.size} cuenta(s) seleccionadas (obligatorio — queda en
            el audit de cada una)
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="ej. Pedido del cliente: cuentas root sólo almacenadas, no se conectan ni rotan."
              className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
            />
          </label>
          <div>
            <Button size="sm" disabled={reason.trim().length < 5 || running} onClick={run}>
              {running && <Loader2 className="size-3.5 animate-spin" />}
              Excluir {selected.size} cuenta(s)
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Excepciones manuales de compliance (R2/R3/R4, ver
 *  docs/specs/compliance-exemptions.md) — cuentas que no restan del % de
 *  Compliance porque la gestión automática se deshabilitó a pedido del
 *  cliente, para siempre. Nada se excluye en silencio: cualquier viewer+ ve
 *  la lista completa con motivo y quién la marcó; sólo operator+ puede
 *  agregar o quitar. */
function ExemptionsView({ tenant, canOperator }: { tenant: string; canOperator: boolean }) {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [removing, setRemoving] = useState<ComplianceExemption | null>(null);
  const [selectedRemove, setSelectedRemove] = useState<Set<string>>(new Set());
  const [removingBulk, setRemovingBulk] = useState(false);

  const q = useQuery({
    queryKey: ["compliance-exemptions", tenant],
    queryFn: () =>
      api<ComplianceExemptionsResponse>(`/tenants/${tenant}/compliance/exemptions`),
  });

  async function runBulkRemove() {
    setRemovingBulk(true);
    const results = await Promise.allSettled(
      [...selectedRemove].map((accountId) =>
        api(`/tenants/${tenant}/compliance/exemptions/${encodeURIComponent(accountId)}`, {
          method: "DELETE",
        }),
      ),
    );
    setRemovingBulk(false);
    const ok = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - ok;
    for (const key of [
      ["compliance-exemptions", tenant],
      ["compliance", tenant],
      ["dashboard", tenant],
    ]) {
      qc.invalidateQueries({ queryKey: key });
    }
    toast({
      title: `${ok} excepción(es) quitada(s)`,
      description: failed > 0 ? `${failed} no se pudieron quitar (revisar)` : undefined,
      variant: failed > 0 ? "error" : "info",
    });
    setSelectedRemove(new Set());
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <p className="max-w-2xl text-xs text-muted-foreground">
          Cuentas excluidas a mano del % de Compliance — CyberArk no registra el motivo de una
          desactivación manual, así que esto vive en PAM Web Console, con motivo obligatorio y auditado.
        </p>
        {canOperator && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <ShieldOff className="size-3.5" />
            Excluir cuenta
          </Button>
        )}
      </div>

      {canOperator && <BulkExemptSection tenant={tenant} />}

      {q.isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {q.isError && <p className="text-sm text-destructive">{(q.error as Error).message}</p>}

      {q.data && q.data.exemptions.length === 0 && (
        <p className="text-sm text-muted-foreground">Sin excepciones activas.</p>
      )}

      {q.data && q.data.exemptions.length > 0 && (
        <>
          {canOperator && selectedRemove.size > 0 && (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={removingBulk}
                onClick={runBulkRemove}
              >
                {removingBulk && <Loader2 className="size-3.5 animate-spin" />}
                Quitar {selectedRemove.size} excepción(es) seleccionada(s)
              </Button>
            </div>
          )}
          <DataTable
            rows={q.data.exemptions}
            columns={exemptionColumns(canOperator, setRemoving)}
            tableId="compliance-exemptions"
            getRowKey={(e) => e.account_id}
            minWidth={700}
            selectedIds={canOperator ? selectedRemove : undefined}
            onSelectedIdsChange={canOperator ? setSelectedRemove : undefined}
            filterPlaceholder="buscar por ID de cuenta / motivo / operador"
          />
        </>
      )}

      <ComplianceExemptionCreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        tenant={tenant}
      />
      <ComplianceExemptionDeleteDialog
        exemption={removing}
        onClose={() => setRemoving(null)}
        tenant={tenant}
      />
    </div>
  );
}

type ComplianceTab = "breakdown" | "search" | "exemptions";

function ComplianceView({ tenant }: { tenant: string }) {
  const [tab, setTab] = useState<ComplianceTab>("breakdown");
  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const canOperator = roleAtLeast(t.data?.my_role, "operator");

  return (
    <div className="flex flex-col gap-4">
      <div className="inline-flex gap-1 self-start rounded-md border border-border p-0.5">
        {(
          [
            { value: "breakdown", label: "Desglose por motivo" },
            { value: "search", label: "Buscar cuenta" },
            { value: "exemptions", label: "Excepciones manuales" },
          ] as const
        ).map((t2) => (
          <button
            key={t2.value}
            onClick={() => setTab(t2.value)}
            className={cn(
              "rounded px-3 py-1 text-sm",
              tab === t2.value ? "bg-accent font-medium" : "text-muted-foreground",
            )}
          >
            {t2.label}
          </button>
        ))}
      </div>

      {tab === "breakdown" && <BreakdownView tenant={tenant} />}
      {tab === "search" && <SearchView tenant={tenant} />}
      {tab === "exemptions" && <ExemptionsView tenant={tenant} canOperator={canOperator} />}
    </div>
  );
}

/** Sección "Compliance" — qué cuentas concretas arrastran el % del
 *  Dashboard hacia abajo y por qué motivo (distinto del Dashboard, que
 *  sólo agrega). Se llega acá desde el KPI/desglose de Compliance del
 *  Dashboard, con el motivo pre-seleccionado (`?reason=`), o directo por
 *  el nav. Sin caché propia — siempre trae el estado más fresco. */
export default function Compliance() {
  return <TenantScope title="Compliance">{(tenant) => <ComplianceView tenant={tenant} />}</TenantScope>;
}
