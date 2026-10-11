import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays,
  Check,
  ChevronDown,
  Copy,
  Eye,
  FolderInput,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { AccountBulkCreateDialog } from "@/components/AccountBulkCreateDialog";
import { AccountBulkDeleteDialog } from "@/components/AccountBulkDeleteDialog";
import { AccountBulkEditDialog } from "@/components/AccountBulkEditDialog";
import { AccountBulkMoveDialog } from "@/components/AccountBulkMoveDialog";
import { AccountCreateWizard } from "@/components/AccountCreateWizard";
import { AccountDetailPanel } from "@/components/AccountDetailPanel";
import { DataTable, type Column } from "@/components/DataTable";
import { DryRunDialog } from "@/components/DryRunDialog";
import { ExportCsvButton } from "@/components/ExportCsvButton";
import { QueryState } from "@/components/QueryState";
import { RevealPasswordDialog } from "@/components/RevealPasswordDialog";
import { TenantScope } from "@/components/TenantScope";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { CPM_LABEL, type CpmAction } from "@/lib/cpmActions";
import { dash, fmtDateTime, relTime } from "@/lib/format";
import { useServerGrid } from "@/lib/gridHelpers";
import { roleAtLeast } from "@/lib/roles";
import { cn } from "@/lib/utils";
import type {
  Account,
  AccountStatusFilter,
  AccountsResponse,
  AccountsSummary,
  SafeNamesResponse,
  TenantSummary,
} from "@/lib/types";

// --- estado ---------------------------------------------------------------

/** Mismo criterio que `app.domain.accounts.account_status_flags` (backend),
 *  que es el que usan los chips de arriba para contar y filtrar. */
function statusFlags(a: Account): AccountStatusFilter[] {
  const sm = a.secretManagement;
  const flags: AccountStatusFilter[] = [];
  const raw = String(sm?.status ?? "").toLowerCase();
  if (raw === "failure" || raw === "failed") flags.push("failure");
  if (sm?.automaticManagementEnabled === false) {
    // el CPM la deshabilitó solo al agotar los reintentos: motivo "(CPM)…"
    flags.push(isCpmDisabledReason(sm.manualManagementReason) ? "cpm_disabled" : "manual");
  }
  return flags;
}

/** Motivo que pone el propio CPM al deshabilitar la gestión automática,
 *  ej. "(CPM)MaxRetries" o "(CPM)UnrecoverableError". */
function isCpmDisabledReason(reason: string | undefined): boolean {
  return String(reason ?? "").startsWith("(CPM)");
}

const FLAG_STYLE: Record<AccountStatusFilter, { label: string; cls: string; title: string }> = {
  failure: {
    label: "Con falla",
    cls: "bg-[#fbe2e2] text-danger-ink",
    title: "El CPM no pudo completar la última operación",
  },
  manual: {
    label: "Gestión automática deshabilitada",
    cls: "bg-[#fdf0d6] text-warning-ink",
    title: "Un usuario deshabilitó la gestión automática: el CPM no rota esta contraseña",
  },
  cpm_disabled: {
    label: "Deshabilitada por el CPM",
    cls: "bg-[#fde6d9] text-danger-ink",
    title:
      "El CPM deshabilitó la gestión automática al agotar los reintentos fallidos: hay que corregir el error y volver a habilitarla",
  },

};

function StatusCell({ account }: { account: Account }) {
  const flags = statusFlags(account);
  if (flags.length === 0) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="size-2 rounded-full bg-line-transformation" aria-hidden="true" />
        En orden
      </span>
    );
  }
  return (
    // el texto completo de cada estado queda en el tooltip
    <span className="flex min-w-0 gap-1">
      {flags.map((f) => (
        <span
          key={f}
          title={FLAG_STYLE[f].title}
          className={cn(
            "rounded-full px-2 py-0.5 text-[11px] font-semibold",
            // sólo la larga se acorta si comparte celda; las cortas, completas
            f === "manual" || f === "cpm_disabled" ? "min-w-0 truncate" : "shrink-0",
            FLAG_STYLE[f].cls,
          )}
        >
          {FLAG_STYLE[f].label}
        </span>
      ))}
    </span>
  );
}

/** "hace 9 días" / "hace 3 meses"; la fecha exacta queda en el tooltip. */
function ago(epoch: number | undefined): string {
  if (!epoch) return "Nunca";
  const days = Math.floor((Date.now() - epoch * 1000) / 86_400_000);
  if (days < 1) return "Hoy";
  if (days === 1) return "Ayer";
  if (days < 60) return `hace ${days} días`;
  if (days < 730) return `hace ${Math.round(days / 30)} meses`;
  return `hace ${Math.round(days / 365)} años`;
}

function lastChangeEpoch(a: Account): number | undefined {
  const sm = a.secretManagement;
  return sm?.lastModifiedTime ?? sm?.lastModifiedDateTime;
}

// --- filtros --------------------------------------------------------------

// Column.key -> query param del endpoint /accounts (snake_case). Sólo estos
// 3 se filtran por columna server-side; Safe y fecha tienen su control propio.
const COL_TO_PARAM: Record<string, string> = {
  userName: "user_name",
  address: "address",
  platformId: "platform_id",
};

const DATE_PRESETS = [
  { days: 7, label: "Últimos 7 días" },
  { days: 30, label: "Últimos 30 días" },
  { days: 90, label: "Últimos 90 días" },
];

function isoDaysAgo(days: number): string {
  const d = new Date(Date.now() - days * 86_400_000);
  return d.toISOString().slice(0, 10);
}

function DateFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const preset = DATE_PRESETS.find((p) => isoDaysAgo(p.days) === value);
  const label = !value
    ? "Modificadas: cualquier fecha"
    : preset
      ? `Modificadas: ${preset.label.toLowerCase()}`
      : `Modificadas desde ${new Date(`${value}T00:00:00`).toLocaleDateString("es")}`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={cn(value && "border-sky-300 bg-sky-50")}>
          <CalendarDays className="size-3.5" />
          {label}
          <ChevronDown className="size-3.5 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-60">
        <DropdownMenuItem onSelect={() => onChange("")}>
          {!value ? <Check className="mr-2 size-3.5" /> : <span className="mr-2 w-3.5" />}
          Cualquier fecha
        </DropdownMenuItem>
        {DATE_PRESETS.map((p) => (
          <DropdownMenuItem key={p.days} onSelect={() => onChange(isoDaysAgo(p.days))}>
            {preset?.days === p.days ? (
              <Check className="mr-2 size-3.5" />
            ) : (
              <span className="mr-2 w-3.5" />
            )}
            {p.label}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <div className="px-2 py-1.5">
          <label className="text-xs text-muted-foreground">
            Desde una fecha
            <input
              type="date"
              lang="es"
              value={value}
              max={isoDaysAgo(0)}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
              className="mt-1 h-8 w-full rounded-md border border-input bg-card px-2 text-sm text-foreground"
            />
          </label>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SummaryChips({
  summary,
  value,
  onChange,
}: {
  summary: AccountsSummary | undefined;
  value: AccountStatusFilter | "";
  onChange: (v: AccountStatusFilter | "") => void;
}) {
  const chips: { key: AccountStatusFilter | ""; label: string; n?: number; dot: string }[] = [
    { key: "", label: "Todas", n: summary?.total, dot: "bg-navy-900" },
    { key: "failure", label: "Con falla", n: summary?.failure, dot: "bg-line-cyberdefense" },
    {
      key: "manual",
      label: "Gestión automática deshabilitada",
      n: summary?.manual,
      dot: "bg-line-consulting",
    },
    {
      key: "cpm_disabled",
      label: "Deshabilitada por el CPM",
      n: summary?.cpm_disabled,
      dot: "bg-line-awareness",
    },
  ];
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Filtrar por estado">
      {chips.map((c) => {
        const active = value === c.key;
        return (
          <button
            key={c.key || "all"}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(active && c.key ? "" : c.key)}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-3.5 py-2 text-left transition-colors",
              active
                ? "border-navy-900 bg-navy-900 text-white"
                : "border-border bg-card hover:border-sky-300 hover:bg-sky-50",
            )}
          >
            <span className={cn("size-2 rounded-full", c.dot)} aria-hidden="true" />
            <span className="text-sm">{c.label}</span>
            <span
              className={cn(
                "text-base font-bold tabular-nums",
                active ? "text-white" : "text-navy-900",
              )}
            >
              {c.n === undefined ? "…" : c.n.toLocaleString("es-AR")}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// --- acciones rápidas por fila ------------------------------------------------

function RowActions({
  account,
  canReveal,
  canChange,
  onReveal,
  onChange,
}: {
  account: Account;
  canReveal: boolean;
  canChange: boolean;
  onReveal: () => void;
  onChange: () => void;
}) {
  async function copyUser() {
    try {
      await navigator.clipboard.writeText(account.userName ?? "");
      toast({ title: "Usuario copiado", description: account.userName });
    } catch {
      toast({ title: "No se pudo copiar", variant: "error" });
    }
  }
  const btn =
    "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-card hover:text-navy-900 focus-visible:outline-2 focus-visible:outline-ring";
  return (
    // oculto hasta pasar el mouse por la fila (o enfocarla con el teclado)
    <span
      className="flex justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className={btn}
        onClick={copyUser}
        title="Copiar usuario"
        aria-label={`Copiar usuario ${account.userName ?? ""}`}
      >
        <Copy className="size-3.5" />
      </button>
      {canReveal && (
        <button
          type="button"
          className={btn}
          onClick={onReveal}
          title="Ver contraseña"
          aria-label="Ver contraseña"
        >
          <Eye className="size-3.5" />
        </button>
      )}
      {canChange && (
        <button
          type="button"
          className={btn}
          onClick={onChange}
          title="Cambiar ahora (CPM)"
          aria-label="Cambiar contraseña ahora"
        >
          <RotateCw className="size-3.5" />
        </button>
      )}
    </span>
  );
}

// --- página -------------------------------------------------------------------

function AccountsView({ tenant }: { tenant: string }) {
  const qc = useQueryClient();
  const [sel, setSel] = useState<Account | null>(null);
  const [safeName, setSafeName] = useState("");
  const [modAfter, setModAfter] = useState(""); // yyyy-mm-dd
  const [status, setStatus] = useState<AccountStatusFilter | "">("");
  const [colFilters, setColFilters] = useState<Record<string, string>>({});
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkEditing, setBulkEditing] = useState(false);
  const [bulkMoving, setBulkMoving] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkCpm, setBulkCpm] = useState<CpmAction | null>(null);
  const [creating, setCreating] = useState(false);
  const [bulkCreating, setBulkCreating] = useState(false);
  const [revealFor, setRevealFor] = useState<Account | null>(null);
  const [changeFor, setChangeFor] = useState<Account | null>(null);

  const modEpoch = modAfter ? Math.floor(new Date(modAfter).getTime() / 1000) : undefined;
  const grid = useServerGrid(
    {
      safe_name: safeName || undefined,
      modified_after: modEpoch ? String(modEpoch) : undefined,
      status: status || undefined,
      ...Object.fromEntries(
        Object.entries(colFilters)
          .filter(([, v]) => v)
          .map(([k, v]) => [COL_TO_PARAM[k] ?? k, v]),
      ),
    },
    50,
  );
  const safes = useQuery({
    queryKey: ["safe-names", tenant],
    queryFn: () => api<SafeNamesResponse>(`/tenants/${tenant}/safe-names`),
    staleTime: 5 * 60_000,
  });
  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const summary = useQuery({
    queryKey: ["inv", tenant, "accounts-summary"],
    queryFn: () => api<AccountsSummary>(`/tenants/${tenant}/accounts/summary`),
    staleTime: 60_000,
  });
  const role = t.data?.my_role;
  const canOperate = roleAtLeast(role, "operator");
  const canBulkMove = roleAtLeast(role, "platform_admin");
  const canReveal = roleAtLeast(role, "tenant_admin");
  const canSelectRows = canOperate || canBulkMove;

  const q = useQuery({
    queryKey: ["inv", tenant, "accounts", grid.params],
    queryFn: () => api<AccountsResponse>(`/tenants/${tenant}/accounts?${grid.params}`),
    placeholderData: keepPreviousData,
  });

  const columns: Column<Account>[] = useMemo(
    () => [
      {
        key: "status",
        header: "Estado",
        sortable: false,
        filterable: false,
        width: 235,
        minWidth: 120,
        render: (a) => <StatusCell account={a} />,
      },
      { key: "name", header: "Nombre", width: 120, render: (a) => dash(a.name) },
      {
        key: "userName",
        header: "Usuario",
        width: 115,
        render: (a) => dash(a.userName),
        serverFilterable: true,
      },
      {
        key: "address",
        header: "Address",
        width: 155,
        render: (a) => dash(a.address),
        serverFilterable: true,
      },
      {
        key: "platformId",
        header: "Plataforma",
        width: 125,
        render: (a) => dash(a.platformId),
        serverFilterable: true,
      },
      { key: "safeName", header: "Safe", width: 130, render: (a) => dash(a.safeName) },
      {
        key: "remoteMachines",
        header: "Máquinas remotas",
        sortable: false,
        filterable: false,
        width: 170,
        render: (a) => {
          const rm = remoteMachinesOf(a);
          return rm ? (
            <span className="block truncate" title={rm}>
              {rm}
            </span>
          ) : (
            dash(undefined)
          );
        },
      },
      {
        key: "lastChange",
        header: "Último cambio",
        sortable: false,
        filterable: false,
        width: 115,
        render: (a) => {
          const e = lastChangeEpoch(a);
          // con el último intento del CPM fallido, `lastModifiedTime` es la
          // hora de ESE intento, no la del último cambio exitoso (caso real
          // de Globex, 2026-09-29): se muestra como intento fallido para
          // no dar a entender que la contraseña se cambió. La fecha real del
          // último cambio está en el detalle (sale de las versiones).
          if (e && statusFlags(a).includes("failure")) {
            return (
              <span
                title={`Último intento del CPM, que falló: ${fmtDateTime(e)}. La contraseña vigente es anterior; vea el detalle de la cuenta.`}
                className="text-danger-ink"
              >
                Falló {ago(e).toLowerCase()}
              </span>
            );
          }
          return (
            <span
              title={e ? fmtDateTime(e) : "La contraseña nunca se cambió"}
              className="text-muted-foreground"
            >
              {ago(e)}
            </span>
          );
        },
      },
      {
        key: "actions",
        header: "",
        sortable: false,
        filterable: false,
        width: 96,
        minWidth: 96,
        render: (a) => (
          <RowActions
            account={a}
            canReveal={canReveal}
            canChange={canOperate}
            onReveal={() => setRevealFor(a)}
            onChange={() => setChangeFor(a)}
          />
        ),
      },
    ],
    [canReveal, canOperate],
  );

  const hasFilter =
    !!safeName || !!modAfter || !!status || Object.values(colFilters).some((v) => v);
  function clearFilters() {
    setSafeName("");
    setModAfter("");
    setStatus("");
    setColFilters({});
  }
  function afterWrite() {
    qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
    qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts-summary"] });
  }

  const header: ReactNode = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <SummaryChips summary={summary.data} value={status} onChange={setStatus} />
      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          onClick={() => {
            q.refetch();
            summary.refetch();
          }}
          disabled={q.isFetching}
          aria-label="Actualizar"
          title="Volver a pedir la lista a CyberArk (por ejemplo, para ver si una rotación ya terminó)"
        >
          <RefreshCw className={cn("size-3.5", q.isFetching && "animate-spin")} />
        </Button>
        <ExportCsvButton tenant={tenant} resource="accounts" iconOnly />
        {canOperate && (
          <div className="flex">
            <Button size="sm" className="rounded-r-none" onClick={() => setCreating(true)}>
              <Plus className="size-3.5" />
              Crear cuenta
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  className="rounded-l-none border-l border-white/25 px-2"
                  aria-label="Más formas de crear cuentas"
                >
                  <ChevronDown className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuItem onSelect={() => setCreating(true)}>
                  <Plus className="mr-2 size-3.5" />
                  Crear una cuenta
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setBulkCreating(true)}>
                  <Upload className="mr-2 size-3.5" />
                  <span className="flex flex-col">
                    Alta masiva de cuentas
                    <span className="text-xs text-muted-foreground">
                      CSV o filas pegadas de un ticket
                    </span>
                  </span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>
    </div>
  );

  const filters: ReactNode = (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={safeName || "__all"}
        onValueChange={(v) => setSafeName(v === "__all" ? "" : v)}
      >
        <SelectTrigger
          aria-label="Filtrar por safe"
          className={cn("h-8 w-52 text-xs", safeName && "border-sky-300 bg-sky-50")}
        >
          <SelectValue placeholder="Safe" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all">Todos los Safes</SelectItem>
          {(safes.data?.names ?? []).map((n) => (
            <SelectItem key={n} value={n}>
              {n}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DateFilter value={modAfter} onChange={setModAfter} />
      {hasFilter && (
        <Button variant="ghost" size="sm" onClick={clearFilters}>
          <X className="size-3.5" />
          Limpiar filtros
        </Button>
      )}
    </div>
  );

  return (
    // con selección, espacio abajo para que la barra flotante no tape la paginación
    <div className={cn("flex flex-col gap-4", selectedIds.size > 0 && "pb-16")}>
      {header}

      {!q.data ? (
        q.isLoading ? (
          <div className="flex flex-col gap-2" aria-busy="true" aria-label="Cargando cuentas">
            <Skeleton className="h-9 w-[420px] max-w-full" />
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : (
          <QueryState isLoading={false} error={q.error} tenant={tenant} />
        )
      ) : (
        <DataTable
          rows={q.data.accounts}
          columns={columns}
          tableId="inv-accounts-v2"
          getRowKey={(a, i) => a.id ?? i}
          minWidth={980}
          onRowClick={setSel}
          selectedIds={canSelectRows ? selectedIds : undefined}
          onSelectedIdsChange={canSelectRows ? setSelectedIds : undefined}
          leadingToolbar={filters}
          countNoun={q.data.count === 1 ? "cuenta" : "cuentas"}
          collapsibleColumnFilters
          compactColumnsButton
          filterPlaceholder="Buscar por nombre, usuario, address o máquina remota (varios: separe con coma)"
          server={{
            total: q.data.count,
            offset: q.data.offset,
            limit: q.data.limit,
            sort: grid.sort,
            search: grid.search,
            columnFilters: colFilters,
            loading: q.isFetching,
            onLimitChange: grid.setLimit,
            pageSizes: [25, 50, 100],
            onChange: (n) => {
              grid.onChange(n);
              if (n.columnFilters !== undefined) setColFilters(n.columnFilters);
            },
          }}
        />
      )}
      {(q.data?.synced_at || summary.data?.synced_at) && (
        <p className="text-right text-xs text-muted-foreground">
          Conteos y filtros por estado o columna: datos de{" "}
          {relTime(q.data?.synced_at ?? summary.data?.synced_at)}
        </p>
      )}

      {selectedIds.size > 0 && (
        <SelectionBar
          count={selectedIds.size}
          onEdit={canOperate ? () => setBulkEditing(true) : undefined}
          onMove={canBulkMove ? () => setBulkMoving(true) : undefined}
          onDelete={canBulkMove ? () => setBulkDeleting(true) : undefined}
          onCpm={canOperate ? setBulkCpm : undefined}
          onClear={() => setSelectedIds(new Set())}
        />
      )}

      <AccountCreateWizard tenant={tenant} open={creating} onOpenChange={setCreating} hideTrigger />
      <AccountBulkCreateDialog
        tenant={tenant}
        open={bulkCreating}
        onOpenChange={setBulkCreating}
        hideTrigger
      />
      {bulkEditing && (
        <AccountBulkEditDialog
          tenant={tenant}
          accountIds={[...selectedIds]}
          onClose={() => setBulkEditing(false)}
          onDone={() => {
            setBulkEditing(false);
            setSelectedIds(new Set());
            afterWrite();
          }}
        />
      )}
      {bulkMoving && (
        <AccountBulkMoveDialog
          tenant={tenant}
          accountIds={[...selectedIds]}
          onClose={() => setBulkMoving(false)}
          onDone={() => {
            setBulkMoving(false);
            setSelectedIds(new Set());
            afterWrite();
          }}
        />
      )}
      {bulkDeleting && (
        <AccountBulkDeleteDialog
          tenant={tenant}
          accountIds={[...selectedIds]}
          onClose={() => setBulkDeleting(false)}
          onDone={() => {
            setBulkDeleting(false);
            setSelectedIds(new Set());
            afterWrite();
          }}
        />
      )}
      <DryRunDialog
        open={!!bulkCpm}
        onClose={() => setBulkCpm(null)}
        path={`/tenants/${tenant}/accounts/bulk-cpm`}
        changes={{}}
        extraBody={{ action: bulkCpm, account_ids: [...selectedIds] }}
        title={`${bulkCpm ? BULK_CPM_LABEL[bulkCpm] : ""} — ${selectedIds.size} cuenta(s)`}
        onDone={() => {
          setBulkCpm(null);
          setSelectedIds(new Set());
          afterWrite();
        }}
      />
      <RevealPasswordDialog
        open={!!revealFor}
        onClose={() => setRevealFor(null)}
        path={`/tenants/${tenant}/accounts/${revealFor?.id ?? ""}/reveal`}
        title={`Ver contraseña — ${revealFor?.userName ?? ""}@${revealFor?.address ?? ""}`}
      />
      <DryRunDialog
        open={!!changeFor}
        onClose={() => setChangeFor(null)}
        path={`/tenants/${tenant}/accounts/${changeFor?.id ?? ""}/change`}
        changes={{}}
        title={`${CPM_LABEL.change} — ${changeFor?.userName ?? ""}@${changeFor?.address ?? ""}`}
        onDone={() => {
          setChangeFor(null);
          afterWrite();
        }}
      />
      <AccountDetailPanel
        opened={!!sel}
        onClose={() => setSel(null)}
        object={sel}
        tenant={tenant}
        accountId={typeof sel?.id === "string" ? sel.id : undefined}
      />
    </div>
  );
}

const BULK_CPM_LABEL: Record<CpmAction, string> = {
  change: "Cambiar contraseña ahora",
  verify: "Verificar contraseña",
  reconcile: "Reconciliar",
};

/** `remoteMachinesAccess.remoteMachines` (las máquinas del dominio a las que
 *  puede acceder la cuenta), como texto, o `undefined` si no tiene. */
function remoteMachinesOf(a: Account): string | undefined {
  const rma = a.remoteMachinesAccess as { remoteMachines?: string } | undefined;
  return rma?.remoteMachines?.trim() || undefined;
}

/** Barra flotante abajo con las acciones sobre las cuentas tildadas. */
function SelectionBar({
  count,
  onEdit,
  onMove,
  onDelete,
  onCpm,
  onClear,
}: {
  count: number;
  onEdit?: () => void;
  onMove?: () => void;
  /** Eliminar en lote (`POST .../accounts/bulk-delete`), `platform_admin`. */
  onDelete?: () => void;
  /** Acciones del CPM en lote (`POST .../accounts/bulk-cpm`), `operator+`. */
  onCpm?: (action: CpmAction) => void;
  onClear: () => void;
}) {
  return (
    <div
      role="region"
      aria-label="Acciones sobre las cuentas seleccionadas"
      className="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-xl bg-navy-900 py-1.5 pl-4 pr-1.5 text-sm text-white shadow-lg"
    >
      <span className="mr-2 flex items-center gap-2 font-medium">
        <KeyRound className="size-4 text-sky-300" aria-hidden="true" />
        {count.toLocaleString("es-AR")}{" "}
        {count === 1 ? "cuenta seleccionada" : "cuentas seleccionadas"}
      </span>
      {onCpm && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              className="text-white hover:bg-white/10 hover:text-white"
            >
              <RefreshCw className="size-3.5" />
              Rotar
              <ChevronDown className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" side="top" className="w-64">
            <DropdownMenuItem onClick={() => onCpm("change")}>
              <div className="flex flex-col gap-0.5">
                <span>{BULK_CPM_LABEL.change}</span>
                <span className="text-[11px] font-normal text-muted-foreground">
                  el CPM genera una contraseña nueva en cada cuenta
                </span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onCpm("verify")}>
              <div className="flex flex-col gap-0.5">
                <span>{BULK_CPM_LABEL.verify}</span>
                <span className="text-[11px] font-normal text-muted-foreground">
                  chequea contra el target, no cambia nada
                </span>
              </div>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onCpm("reconcile")}>
              <div className="flex flex-col gap-0.5">
                <span>{BULK_CPM_LABEL.reconcile}</span>
                <span className="text-[11px] font-normal text-muted-foreground">
                  resetea usando la reconcile account
                </span>
              </div>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      {onEdit && (
        <Button
          size="sm"
          variant="ghost"
          className="text-white hover:bg-white/10 hover:text-white"
          onClick={onEdit}
        >
          <Pencil className="size-3.5" />
          Editar
        </Button>
      )}
      {onMove && (
        <Button
          size="sm"
          variant="ghost"
          className="text-white hover:bg-white/10 hover:text-white"
          onClick={onMove}
        >
          <FolderInput className="size-3.5" />
          Mover
        </Button>
      )}
      {onDelete && (
        <Button
          size="sm"
          variant="ghost"
          className="text-red-300 hover:bg-white/10 hover:text-red-200"
          onClick={onDelete}
        >
          <Trash2 className="size-3.5" />
          Eliminar
        </Button>
      )}
      <Button
        size="icon"
        variant="ghost"
        className="size-8 text-white hover:bg-white/10 hover:text-white"
        onClick={onClear}
        aria-label="Quitar la selección"
        title="Quitar la selección"
      >
        <X className="size-4" />
      </Button>
    </div>
  );
}

export default function Accounts() {
  return <TenantScope title="Cuentas">{(tenant) => <AccountsView tenant={tenant} />}</TenantScope>;
}
