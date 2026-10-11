import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { SiaWarnings } from "@/components/SiaWarnings";
import { TenantScope } from "@/components/TenantScope";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import {
  fmtDuration,
  sessionDuration,
  type SiaSessionResponse,
  type SiaSessionRow,
  type SiaSessionsResponse,
} from "@/lib/sia";

type Status = "active" | "recent";

function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s === "active") return <Badge variant="success">Activa</Badge>;
  if (s === "failed") return <Badge variant="danger">Fallida</Badge>;
  if (s === "ended") return <Badge variant="neutral">Terminada</Badge>;
  return <Badge variant="neutral">{dash(status)}</Badge>;
}

const cols: Column<SiaSessionRow>[] = [
  { key: "user", header: "Usuario", render: (r) => dash(r.user), sortValue: (r) => r.user },
  { key: "target", header: "Target", render: (r) => dash(r.target), sortValue: (r) => r.target },
  {
    key: "protocol",
    header: "Protocolo",
    width: 100,
    render: (r) => dash(r.protocol),
    sortValue: (r) => r.protocol,
  },
  {
    key: "targetUsername",
    header: "Cuenta",
    width: 150,
    render: (r) => dash(r.targetUsername),
    sortValue: (r) => r.targetUsername,
  },
  {
    key: "startTime",
    header: "Inicio",
    width: 150,
    render: (r) => fmtDateTime(r.startTime),
    sortValue: (r) => r.startTime,
  },
  {
    key: "duration",
    header: "Duración",
    width: 110,
    align: "right",
    render: (r) => fmtDuration(sessionDuration(r)),
    sortValue: (r) => sessionDuration(r),
  },
  {
    key: "status",
    header: "Estado",
    width: 110,
    render: (r) => <StatusBadge status={r.status} />,
    sortValue: (r) => r.status,
  },
  {
    key: "source",
    header: "Origen",
    width: 130,
    defaultHidden: true,
    render: (r) => dash(r.source),
  },
];

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="contents">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-words">{value}</span>
    </div>
  );
}

function SessionPanel({
  tenant,
  row,
  onClose,
}: {
  tenant: string;
  row: SiaSessionRow | null;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["sia-session", tenant, row?.id],
    queryFn: () =>
      api<SiaSessionResponse>(`/tenants/${tenant}/sia/sessions/${encodeURIComponent(row!.id)}`),
    enabled: !!row,
  });
  const s = q.data?.session ?? row;
  return (
    <Sheet open={!!row} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="max-w-2xl">
        <SheetTitle>
          {dash(s?.user)} → {dash(s?.target)}
        </SheetTitle>
        {s && (
          <div className="grid grid-cols-[150px_1fr] gap-x-3 gap-y-1 text-xs">
            <Field label="Estado" value={<StatusBadge status={s.status} />} />
            <Field label="Protocolo" value={dash(s.protocol)} />
            <Field label="Cuenta" value={dash(s.targetUsername)} />
            <Field label="Origen" value={dash(s.source)} />
            <Field label="Plataforma" value={dash(s.platform)} />
            <Field label="Inicio" value={fmtDateTime(s.startTime)} />
            <Field label="Fin" value={fmtDateTime(s.endTime)} />
            <Field label="Duración" value={fmtDuration(sessionDuration(s))} />
            <Field label="Grabada" value={s.isRecording == null ? "—" : s.isRecording ? "Sí" : "No"} />
            {s.endReason && <Field label="Motivo de cierre" value={s.endReason} />}
            {s.errorCode && <Field label="Código de error" value={s.errorCode} />}
          </div>
        )}
        <section>
          <h3 className="mb-2 text-sm font-semibold">Actividad</h3>
          {!q.data ? (
            <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />
          ) : (
            <>
              <SiaWarnings warnings={q.data.warnings} />
              {q.data.activities.length === 0 ? (
                <p className="text-xs text-muted-foreground">Sin actividad registrada.</p>
              ) : (
                <ul className="flex flex-col gap-1 text-xs">
                  {q.data.activities.map((a) => (
                    <li key={a.id} className="rounded-sm border border-border px-2 py-1">
                      <span className="text-muted-foreground">{fmtDateTime(a.timestamp)} · </span>
                      {a.command ? <code>{a.command}</code> : dash(a.action ?? a.message)}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      </SheetContent>
    </Sheet>
  );
}

function SessionsView({ tenant }: { tenant: string }) {
  const [status, setStatus] = useState<Status>("active");
  const [sel, setSel] = useState<SiaSessionRow | null>(null);
  const q = useQuery({
    queryKey: ["sia-sessions", tenant, status],
    queryFn: () => api<SiaSessionsResponse>(`/tenants/${tenant}/sia/sessions?status=${status}`),
    refetchInterval: status === "active" ? 30_000 : false,
  });
  return (
    <>
      <Tabs value={status} onValueChange={(v) => setStatus(v as Status)} className="mb-3">
        <TabsList>
          <TabsTrigger value="active">Activas</TabsTrigger>
          <TabsTrigger value="recent">Últimas 24 h</TabsTrigger>
        </TabsList>
      </Tabs>
      {!q.data ? (
        <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />
      ) : (
        <DataTable
          rows={q.data.sessions}
          total={q.data.count}
          columns={cols}
          tableId="sia-sessions"
          getRowKey={(r) => r.id}
          filterOn={(r) => [r.user, r.target, r.targetUsername, r.protocol, r.source]}
          filterPlaceholder="Buscar por usuario, target o cuenta"
          countNoun="sesiones"
          minWidth={900}
          onRowClick={setSel}
        />
      )}
      <SessionPanel tenant={tenant} row={sel} onClose={() => setSel(null)} />
    </>
  );
}

export default function SiaSessions() {
  return (
    <TenantScope title="Sesiones de SIA">{(tenant) => <SessionsView tenant={tenant} />}</TenantScope>
  );
}
