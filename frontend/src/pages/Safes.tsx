import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { ExportCsvButton } from "@/components/ExportCsvButton";
import { QueryState } from "@/components/QueryState";
import { SafeCreateDialog } from "@/components/SafeCreateDialog";
import { SafeDetailPanel } from "@/components/SafeDetailPanel";
import { TenantScope } from "@/components/TenantScope";
import { api } from "@/lib/api";
import { dash, fmtDate } from "@/lib/format";
import { boolBadge, useServerGrid } from "@/lib/gridHelpers";
import type { Safe, SafesResponse } from "@/lib/types";

const safeCols: Column<Safe>[] = [
  { key: "safeName", header: "Safe", render: (s) => dash(s.safeName) },
  {
    key: "description",
    header: "Descripción",
    sortable: false,
    width: 280,
    render: (s) => dash(s.description),
  },
  { key: "managingCPM", header: "CPM", sortable: false, render: (s) => dash(s.managingCPM) },
  {
    key: "retention",
    header: "Retención",
    sortable: false,
    render: (s) =>
      s.numberOfDaysRetention != null
        ? `${s.numberOfDaysRetention} días`
        : s.numberOfVersionsRetention != null
          ? `${s.numberOfVersionsRetention} versiones`
          : "—",
  },
  {
    key: "olac",
    header: "OLAC",
    sortable: false,
    render: (s) => boolBadge(s.olacEnabled, "sí", "no"),
  },
  { key: "creationTime", header: "Creado", sortable: false, render: (s) => fmtDate(s.creationTime) },
];

function SafesView({ tenant }: { tenant: string }) {
  const [sel, setSel] = useState<Safe | null>(null);
  const grid = useServerGrid();
  const q = useQuery({
    queryKey: ["inv", tenant, "safes", grid.params],
    queryFn: () => api<SafesResponse>(`/tenants/${tenant}/safes?${grid.params}`),
    placeholderData: keepPreviousData,
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <div className="flex min-w-0 items-start gap-4">
      <div className={sel ? "min-w-0 shrink-0 basis-[420px]" : "min-w-0 flex-1"}>
        <DataTable
          rows={q.data.safes}
          columns={safeCols}
          tableId="inv-safes"
          getRowKey={(s, i) => s.safeUrlId ?? s.safeName ?? i}
          filterPlaceholder="buscar por nombre de safe"
          minWidth={900}
          onRowClick={setSel}
          toolbar={
            <div className="flex flex-wrap items-center gap-2">
              <ExportCsvButton tenant={tenant} resource="safes" />
              <SafeCreateDialog tenant={tenant} />
            </div>
          }
          server={{
            total: q.data.count,
            offset: q.data.offset,
            limit: q.data.limit,
            sort: grid.sort,
            search: grid.search,
            loading: q.isFetching,
            onChange: grid.onChange,
          }}
        />
      </div>

      {sel && (
        <SafeDetailPanel
          tenant={tenant}
          safe={sel}
          onClose={() => setSel(null)}
          onDeleted={() => setSel(null)}
        />
      )}
    </div>
  );
}

export default function Safes() {
  return <TenantScope title="Safes">{(tenant) => <SafesView tenant={tenant} />}</TenantScope>;
}
