import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { dash, fmtDateTime, relTime } from "@/lib/format";
import type { AleroActivitiesResponse, AleroActivityRow } from "@/lib/types";

const WINDOWS = [
  { hours: 1, label: "última hora" },
  { hours: 24, label: "últimas 24 horas" },
  { hours: 24 * 7, label: "últimos 7 días" },
  { hours: 24 * 30, label: "últimos 30 días" },
];

const cols: Column<AleroActivityRow>[] = [
  {
    key: "time",
    header: "Fecha y hora",
    width: 160,
    render: (r) => <span title={fmtDateTime(r.time)}>{relTime(r.time)}</span>,
    sortValue: (r) => r.time,
  },
  {
    key: "actor",
    header: "Quién",
    render: (r) => (
      <span>
        {dash(r.actor)}
        {r.actorRole && <span className="text-xs text-muted-foreground"> · {r.actorRole}</span>}
      </span>
    ),
    sortValue: (r) => r.actor,
  },
  {
    key: "label",
    header: "Actividad",
    render: (r) => dash(r.label),
    sortValue: (r) => r.label,
  },
  {
    key: "detail",
    header: "Detalle",
    width: 280,
    render: (r) => dash(r.detail),
    sortValue: (r) => r.detail,
  },
  {
    key: "sourceIp",
    header: "IP de origen",
    render: (r) => dash(r.sourceIp),
    sortValue: (r) => r.sourceIp,
  },
];

function AleroActivitiesView({ tenant }: { tenant: string }) {
  const [hours, setHours] = useState(24);
  const q = useQuery({
    queryKey: ["alero-activities", tenant, hours],
    queryFn: () => api<AleroActivitiesResponse>(`/tenants/${tenant}/alero-activities?hours=${hours}`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <DataTable
      rows={q.data.activities}
      total={q.data.count}
      columns={cols}
      tableId="alero-activities"
      getRowKey={(r, i) => r.id ?? i}
      filterOn={(r) => [r.actor, r.label, r.detail]}
      filterPlaceholder="filtrar por quién / actividad / detalle"
      minWidth={1000}
      toolbar={
        <Select value={String(hours)} onValueChange={(v) => setHours(Number(v))}>
          <SelectTrigger aria-label="Ventana de tiempo" className="h-8 w-40 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {WINDOWS.map((w) => (
              <SelectItem key={w.hours} value={String(w.hours)}>
                {w.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      }
    />
  );
}

export default function AleroActivities() {
  return (
    <TenantScope title="Activities">{(tenant) => <AleroActivitiesView tenant={tenant} />}</TenantScope>
  );
}
