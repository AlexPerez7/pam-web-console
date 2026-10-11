import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { SessionTerminateButton } from "@/components/SessionTerminateButton";
import { TenantScope } from "@/components/TenantScope";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { dash, fmtDate } from "@/lib/format";
import type { LiveSession, SessionsResponse } from "@/lib/types";

function durationText(d: number | string | undefined): string {
  if (d == null) return "—";
  if (typeof d === "string") return d;
  const h = Math.floor(d / 3600);
  const m = Math.floor((d % 3600) / 60);
  return h > 0 ? `${h} h ${m} min` : `${m} min`;
}

function sessionCols(tenant: string): Column<LiveSession>[] {
  return [
    { key: "User", header: "Usuario", render: (s) => dash(s.User), sortValue: (s) => s.User },
    { key: "RemoteMachine", header: "Target", render: (s) => dash(s.RemoteMachine), sortValue: (s) => s.RemoteMachine },
    {
      key: "AccountUsername",
      header: "Cuenta",
      render: (s) => dash(s.AccountUsername),
      sortValue: (s) => s.AccountUsername,
    },
    { key: "Protocol", header: "Protocolo", render: (s) => dash(s.Protocol), sortValue: (s) => s.Protocol },
    { key: "Start", header: "Inicio", render: (s) => fmtDate(s.Start), sortValue: (s) => s.Start ?? null },
    {
      key: "Duration",
      header: "Duración",
      render: (s) => durationText(s.Duration),
      sortValue: (s) => (typeof s.Duration === "number" ? s.Duration : null),
    },
    {
      key: "actions",
      header: "Acciones",
      sortable: false,
      filterable: false,
      render: (s) =>
        s.SessionID ? (
          <SessionTerminateButton tenant={tenant} sessionId={String(s.SessionID)} />
        ) : null,
    },
  ];
}

function SessionsView({ tenant }: { tenant: string }) {
  const q = useQuery({
    queryKey: ["inv", tenant, "sessions"],
    queryFn: () => api<SessionsResponse>(`/tenants/${tenant}/sessions`),
  });

  const refreshButton = (
    <Button variant="outline" size="sm" onClick={() => q.refetch()} disabled={q.isFetching}>
      <RefreshCw className={q.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />
      Actualizar
    </Button>
  );

  if (!q.data)
    return (
      <div className="flex flex-col gap-3">
        <div className="flex justify-end">{refreshButton}</div>
        <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />
      </div>
    );

  if (q.data.sessions.length === 0)
    return (
      <div className="flex flex-col gap-3">
        <div className="flex justify-end">{refreshButton}</div>
        <p className="text-sm text-muted-foreground">No hay sesiones PSM activas.</p>
      </div>
    );

  return (
    <DataTable
      rows={q.data.sessions}
      total={q.data.count}
      columns={sessionCols(tenant)}
      tableId="inv-sessions"
      getRowKey={(s, i) => (s.SessionID as string | undefined) ?? i}
      filterOn={(s) => [s.User, s.RemoteMachine, s.AccountUsername, s.Protocol]}
      filterPlaceholder="filtrar por usuario / target / cuenta / protocolo"
      minWidth={800}
      toolbar={refreshButton}
    />
  );
}

export default function Sessions() {
  return <TenantScope title="Sesiones">{(tenant) => <SessionsView tenant={tenant} />}</TenantScope>;
}
