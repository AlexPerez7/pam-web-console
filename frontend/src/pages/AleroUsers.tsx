import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Avatar, AleroStatusBadge } from "@/components/AleroBadges";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { api } from "@/lib/api";
import { aleroRoleLabel, dash, fmtDateTime, relTime } from "@/lib/format";
import type { AleroUserRow, AleroUsersResponse } from "@/lib/types";

const cols: Column<AleroUserRow>[] = [
  {
    key: "fullName",
    header: "Usuario",
    width: 260,
    render: (r) => (
      <div className="flex items-center gap-2.5">
        <Avatar name={r.fullName ?? r.username} />
        <div className="flex flex-col leading-tight">
          <span className="font-medium">{dash(r.fullName)}</span>
          <span className="text-xs text-muted-foreground">{dash(r.username)}</span>
        </div>
      </div>
    ),
    sortValue: (r) => r.fullName ?? r.username,
  },
  {
    key: "role",
    header: "Rol",
    render: (r) => <Badge variant={r.role === "TenantAdmin" ? "info" : "neutral"}>{aleroRoleLabel(r.role)}</Badge>,
    sortValue: (r) => r.role,
  },
  {
    key: "status",
    header: "Estado",
    render: (r) => <AleroStatusBadge status={r.status} />,
    sortValue: (r) => r.status,
  },
  {
    key: "lastLogin",
    header: "Último login (app)",
    render: (r) => (
      <span title={fmtDateTime(r.lastLogin)} className="text-muted-foreground">
        {relTime(r.lastLogin)}
      </span>
    ),
    sortValue: (r) => r.lastLogin,
  },
  {
    key: "appVersion",
    header: "Versión app",
    render: (r) => dash(r.appVersion),
    sortValue: (r) => r.appVersion,
  },
];

function AleroUsersView({ tenant }: { tenant: string }) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["alero-users", tenant],
    queryFn: () => api<AleroUsersResponse>(`/tenants/${tenant}/alero-users`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <DataTable
      rows={q.data.users}
      total={q.data.count}
      columns={cols}
      tableId="alero-users"
      getRowKey={(r, i) => r.id ?? r.username ?? i}
      onRowClick={(r) => r.id && navigate(`/alero-users/${r.id}?tenant=${tenant}`)}
      filterOn={(r) => [r.username, r.fullName, r.role, r.status]}
      filterPlaceholder="filtrar por usuario / nombre / rol / estado"
      minWidth={800}
    />
  );
}

export default function AleroUsers() {
  return <TenantScope title="Usuarios">{(tenant) => <AleroUsersView tenant={tenant} />}</TenantScope>;
}
