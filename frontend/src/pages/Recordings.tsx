import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { RecordingDetailPanel } from "@/components/RecordingDetailPanel";
import { TenantScope } from "@/components/TenantScope";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import type { Recording, RecordingsResponse } from "@/lib/types";

function durationText(d: number | string | undefined): string {
  if (d == null) return "—";
  if (typeof d === "string") return d;
  const h = Math.floor(d / 3600);
  const m = Math.floor((d % 3600) / 60);
  return h > 0 ? `${h} h ${m} min` : `${m} min`;
}

/** CyberArk sólo calcula riesgo para grabaciones (no para sesiones en
 *  vivo) — PTA marca comportamiento anómalo con `Severity`/`RiskScore`. */
function riskBadge(severity: string | undefined) {
  if (!severity) return <span className="text-muted-foreground">—</span>;
  const s = severity.toLowerCase();
  const variant =
    s.includes("critical") || s.includes("high") ? "danger" : s.includes("medium") ? "warning" : "neutral";
  return <Badge variant={variant}>{severity}</Badge>;
}

const recordingCols: Column<Recording>[] = [
  { key: "User", header: "Usuario", render: (r) => dash(r.User), sortValue: (r) => r.User },
  {
    key: "RemoteMachine",
    header: "Target",
    render: (r) => dash(r.RemoteMachine),
    sortValue: (r) => r.RemoteMachine,
  },
  { key: "SafeName", header: "Safe", render: (r) => dash(r.SafeName), sortValue: (r) => r.SafeName },
  { key: "Protocol", header: "Protocolo", render: (r) => dash(r.Protocol), sortValue: (r) => r.Protocol },
  {
    key: "Start",
    header: "Inicio",
    render: (r) => fmtDateTime(r.Start),
    sortValue: (r) => r.Start ?? null,
  },
  {
    key: "Duration",
    header: "Duración",
    render: (r) => durationText(r.Duration),
    sortValue: (r) => (typeof r.Duration === "number" ? r.Duration : null),
  },
  {
    key: "risk",
    header: "Riesgo",
    filterable: false,
    render: (r) => riskBadge(r.Severity),
    sortValue: (r) => r.RiskScore ?? null,
  },
];

function RecordingsView({ tenant }: { tenant: string }) {
  const [sel, setSel] = useState<Recording | null>(null);
  const [safe, setSafe] = useState("");
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState(""); // yyyy-mm-dd
  const [toDate, setToDate] = useState("");

  const fromTs = fromDate ? Math.floor(new Date(fromDate).getTime() / 1000) : undefined;
  // "hasta" incluye todo el día elegido (+23:59:59)
  const toTs = toDate ? Math.floor(new Date(toDate).getTime() / 1000) + 86399 : undefined;

  const params = new URLSearchParams();
  if (safe) params.set("safe", safe);
  if (search) params.set("search", search);
  if (fromTs) params.set("from_time", String(fromTs));
  if (toTs) params.set("to_time", String(toTs));

  const q = useQuery({
    queryKey: ["inv", tenant, "recordings", safe, search, fromTs, toTs],
    queryFn: () => api<RecordingsResponse>(`/tenants/${tenant}/recordings?${params.toString()}`),
  });

  const hasFilter = !!safe || !!search || !!fromDate || !!toDate;

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;

  return (
    <>
      <DataTable
        rows={q.data.recordings}
        total={q.data.count}
        columns={recordingCols}
        tableId="inv-recordings"
        getRowKey={(r, i) => r.SessionID ?? i}
        onRowClick={setSel}
        minWidth={900}
        toolbar={
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="Safe"
              value={safe}
              onChange={(e) => setSafe(e.target.value)}
              className="h-8 w-40 text-xs"
            />
            <Input
              placeholder="buscar (usuario / target / ...)"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-8 w-56 text-xs"
            />
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              title="desde"
              className="h-8 rounded-md border border-input bg-card px-2 text-xs text-muted-foreground"
            />
            <input
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              title="hasta"
              className="h-8 rounded-md border border-input bg-card px-2 text-xs text-muted-foreground"
            />
            {hasFilter ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSafe("");
                  setSearch("");
                  setFromDate("");
                  setToDate("");
                }}
              >
                Limpiar
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">últimas 48 h por defecto</span>
            )}
          </div>
        }
      />
      <RecordingDetailPanel tenant={tenant} recording={sel} onClose={() => setSel(null)} />
    </>
  );
}

export default function Recordings() {
  return (
    <TenantScope title="Grabaciones">{(tenant) => <RecordingsView tenant={tenant} />}</TenantScope>
  );
}
