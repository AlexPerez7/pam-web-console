import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FolderInput, RefreshCw, X } from "lucide-react";
import { useMemo, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { ExportCsvButton } from "@/components/ExportCsvButton";
import { PlatformBulkPsmDialog } from "@/components/PlatformBulkPsmDialog";
import { PlatformDetailPanel } from "@/components/PlatformDetailPanel";
import { PlatformImportDialog } from "@/components/PlatformImportDialog";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import { boolBadge } from "@/lib/gridHelpers";
import { roleAtLeast } from "@/lib/roles";
import type { Platform, PlatformsResponse, TenantSummary } from "@/lib/types";

/** "cada N días" si la periódica está activa, "-" si no (aunque el manual
 *  sí esté permitido) — mismo criterio que la columna PERIODIC del PVWA. */
function periodicDays(active: boolean | undefined, days: number | undefined) {
  return active && days ? `${days} días` : "-";
}

const platformCols: Column<Platform>[] = [
  {
    key: "id",
    header: "ID",
    width: 150,
    render: (p) => dash(p.general?.id),
    sortValue: (p) => p.general?.id,
  },
  {
    key: "name",
    header: "Nombre",
    width: 190,
    render: (p) => dash(p.general?.name),
    sortValue: (p) => p.general?.name,
  },
  {
    key: "psm",
    header: "PSM",
    width: 180,
    render: (p) => dash(p.psmServerId),
    sortValue: (p) => p.psmServerId,
  },
  {
    key: "active",
    header: "Estado",
    width: 100,
    filterable: false,
    render: (p) => boolBadge(p.general?.active, "activa", "inactiva"),
    sortValue: (p) => (p.general?.active ? 1 : 0),
  },
  {
    key: "verify_periodic",
    header: "Verificar (automático)",
    width: 140,
    filterable: false,
    render: (p) =>
      periodicDays(
        p.credentialsManagement?.performPeriodicVerification,
        p.credentialsManagement?.requirePasswordVerificationEveryXDays,
      ),
    sortValue: (p) => (p.credentialsManagement?.performPeriodicVerification ? 1 : 0),
  },
  {
    key: "change_periodic",
    header: "Cambiar (automático)",
    width: 140,
    filterable: false,
    render: (p) =>
      periodicDays(
        p.credentialsManagement?.performPeriodicChange,
        p.credentialsManagement?.requirePasswordChangeEveryXDays,
      ),
    sortValue: (p) => (p.credentialsManagement?.performPeriodicChange ? 1 : 0),
  },
  {
    key: "verify_manual",
    header: "Verificar (manual)",
    width: 120,
    filterable: false,
    render: (p) => boolBadge(p.credentialsManagement?.allowManualVerification, "sí", "no"),
    sortValue: (p) => (p.credentialsManagement?.allowManualVerification ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "change_manual",
    header: "Cambiar (manual)",
    width: 120,
    filterable: false,
    render: (p) => boolBadge(p.credentialsManagement?.allowManualChange, "sí", "no"),
    sortValue: (p) => (p.credentialsManagement?.allowManualChange ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "reconcile_auto",
    header: "Reconciliar (automático)",
    width: 130,
    filterable: false,
    render: (p) => boolBadge(p.credentialsManagement?.automaticReconcileWhenUnsynched, "sí", "no"),
    sortValue: (p) => (p.credentialsManagement?.automaticReconcileWhenUnsynched ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "reconcile_manual",
    header: "Reconciliar (manual)",
    width: 130,
    filterable: false,
    render: (p) => boolBadge(p.credentialsManagement?.allowManualReconciliation, "sí", "no"),
    sortValue: (p) => (p.credentialsManagement?.allowManualReconciliation ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "wf_approval",
    header: "Aprobación",
    width: 120,
    filterable: false,
    render: (p) =>
      boolBadge(p.privilegedAccessWorkflows?.requireDualControlPasswordAccessApproval, "sí", "no"),
    sortValue: (p) => (p.privilegedAccessWorkflows?.requireDualControlPasswordAccessApproval ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "wf_reason",
    header: "Motivo obligatorio",
    width: 150,
    filterable: false,
    render: (p) =>
      boolBadge(p.privilegedAccessWorkflows?.requireUsersToSpecifyReasonForAccess, "sí", "no"),
    sortValue: (p) => (p.privilegedAccessWorkflows?.requireUsersToSpecifyReasonForAccess ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "wf_exclusive",
    header: "Check-in/out exclusivo",
    width: 150,
    filterable: false,
    render: (p) => boolBadge(p.privilegedAccessWorkflows?.enforceCheckinCheckoutExclusiveAccess, "sí", "no"),
    sortValue: (p) => (p.privilegedAccessWorkflows?.enforceCheckinCheckoutExclusiveAccess ? 1 : 0),
    defaultHidden: true,
  },
  {
    key: "wf_otp",
    header: "OTP",
    width: 90,
    filterable: false,
    render: (p) => boolBadge(p.privilegedAccessWorkflows?.enforceOnetimePasswordAccess, "sí", "no"),
    sortValue: (p) => (p.privilegedAccessWorkflows?.enforceOnetimePasswordAccess ? 1 : 0),
    defaultHidden: true,
  },
];

/** Un grupo colapsable de plataformas de un mismo `systemType`, igual al
 *  "Platform Management" del PVWA (Windows / *NIX / Database / ...).
 *  Controlado por el padre: con una búsqueda activa se fuerza abierto
 *  (para ver los resultados sin tener que expandir grupo por grupo),
 *  sin búsqueda respeta el tilde manual del operador. */
function PlatformGroup({
  systemType,
  rows,
  open,
  onToggle,
  onRowClick,
  selectedIds,
  onSelectedIdsChange,
}: {
  systemType: string;
  rows: Platform[];
  open: boolean;
  onToggle: () => void;
  onRowClick: (p: Platform) => void;
  selectedIds?: Set<string>;
  onSelectedIdsChange?: (next: Set<string>) => void;
}) {
  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-1.5 px-3 py-2.5 text-left text-sm font-semibold text-navy-900"
      >
        {open ? (
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
        )}
        {systemType}
        <span className="text-xs font-normal text-muted-foreground">({rows.length})</span>
      </button>
      {open && (
        // fade + slide (no height-collapse): DataTable virtualiza sus filas
        // con TanStack Virtual, que mide su contenedor por ResizeObserver —
        // animar el alto del wrapper lo dejaría midiendo contra un alto
        // intermedio durante toda la transición.
        <div className="animate-in fade-in-0 slide-in-from-top-1 border-t border-border p-2 duration-200 ease-out">
          <DataTable
            rows={rows}
            columns={platformCols}
            tableId={`inv-platforms-${systemType}`}
            getRowKey={(p, i) => p.general?.id ?? i}
            minWidth={900}
            onRowClick={onRowClick}
            selectedIds={selectedIds}
            onSelectedIdsChange={onSelectedIdsChange}
          />
        </div>
      )}
    </div>
  );
}

function PlatformsView({ tenant }: { tenant: string }) {
  // Se guarda el ID, no la fila: tras activar/desactivar la grilla se
  // refresca y el panel muestra el estado nuevo sin cerrarse.
  const [selId, setSelId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkPsmMoving, setBulkPsmMoving] = useState(false);
  const q = useQuery({
    queryKey: ["inv", tenant, "platforms"],
    queryFn: () => api<PlatformsResponse>(`/tenants/${tenant}/platforms`),
  });
  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const canBulkPsm = roleAtLeast(t.data?.my_role, "approver");

  const filtered = useMemo(() => {
    const all = q.data?.platforms ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((p) =>
      [p.general?.id, p.general?.name, p.general?.systemType, p.general?.platformType, p.psmServerId].some(
        (v) => String(v ?? "").toLowerCase().includes(needle),
      ),
    );
  }, [q.data, search]);

  const groups = useMemo(() => {
    const byType = new Map<string, Platform[]>();
    for (const p of filtered) {
      const type = p.general?.systemType || "Sin tipo";
      (byType.get(type) ?? byType.set(type, []).get(type)!).push(p);
    }
    return [...byType.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  function toggleGroup(type: string) {
    setOpenGroups((s) => {
      const next = new Set(s);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  const sel = selId ? (q.data?.platforms.find((p) => p.general?.id === selId) ?? null) : null;

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  const searching = search.trim().length > 0;
  return (
    <div className={selectedIds.size > 0 ? "pb-16" : undefined}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Input
          placeholder="buscar por ID / nombre / tipo / PSM, en todas las plataformas"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-[420px] max-w-full"
        />
        <span className="text-sm text-muted-foreground">
          {filtered.length.toLocaleString("es")}
          {searching ? ` de ${q.data.platforms.length.toLocaleString("es")}` : ""}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => q.refetch()} disabled={q.isFetching}>
            <RefreshCw className={q.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />
            Actualizar
          </Button>
          <ExportCsvButton tenant={tenant} resource="platforms" />
          <PlatformImportDialog tenant={tenant} />
        </div>
      </div>
      <div className="flex flex-col gap-2">
        {groups.map(([systemType, rows]) => (
          <PlatformGroup
            key={systemType}
            systemType={systemType}
            rows={rows}
            open={searching || openGroups.has(systemType)}
            onToggle={() => toggleGroup(systemType)}
            onRowClick={(p) => setSelId(p.general?.id ?? null)}
            selectedIds={canBulkPsm ? selectedIds : undefined}
            onSelectedIdsChange={canBulkPsm ? setSelectedIds : undefined}
          />
        ))}
        {groups.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">Sin resultados.</p>
        )}
      </div>
      <PlatformDetailPanel tenant={tenant} platform={sel} onClose={() => setSelId(null)} />

      {selectedIds.size > 0 && (
        <div
          role="region"
          aria-label="Acciones sobre las plataformas seleccionadas"
          className="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-xl bg-navy-900 py-1.5 pl-4 pr-1.5 text-sm text-white shadow-lg"
        >
          <span className="mr-2 font-medium">
            {selectedIds.size.toLocaleString("es-AR")}{" "}
            {selectedIds.size === 1 ? "plataforma seleccionada" : "plataformas seleccionadas"}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="text-white hover:bg-white/10 hover:text-white"
            onClick={() => setBulkPsmMoving(true)}
          >
            <FolderInput className="size-3.5" />
            Mover de PSM
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="size-8 text-white hover:bg-white/10 hover:text-white"
            onClick={() => setSelectedIds(new Set())}
            aria-label="Quitar la selección"
            title="Quitar la selección"
          >
            <X className="size-4" />
          </Button>
        </div>
      )}

      {bulkPsmMoving && (
        <PlatformBulkPsmDialog
          tenant={tenant}
          platformIds={[...selectedIds]}
          onClose={() => setBulkPsmMoving(false)}
          onDone={() => {
            setBulkPsmMoving(false);
            setSelectedIds(new Set());
            q.refetch();
          }}
        />
      )}
    </div>
  );
}

export default function Platforms() {
  return (
    <TenantScope title="Plataformas">{(tenant) => <PlatformsView tenant={tenant} />}</TenantScope>
  );
}
