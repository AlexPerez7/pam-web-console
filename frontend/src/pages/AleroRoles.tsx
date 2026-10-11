import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { AleroRoleRow, AleroRolesResponse } from "@/lib/types";

function AleroRolesView({ tenant }: { tenant: string }) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["alero-roles", tenant],
    queryFn: () => api<AleroRolesResponse>(`/tenants/${tenant}/alero-roles`),
  });

  const cols: Column<AleroRoleRow>[] = [
    { key: "name", header: "Rol", render: (r) => dash(r.name), sortValue: (r) => r.name },
    {
      key: "description",
      header: "Descripción",
      width: 320,
      render: (r) => dash(r.description),
      sortValue: (r) => r.description,
    },
    {
      key: "vendors",
      header: "Vendors",
      align: "right",
      render: (r) =>
        r.vendors ? (
          <button
            type="button"
            className="cursor-pointer"
            title={`Ver los ${r.vendors} vendor(s) con este rol`}
            onClick={() =>
              navigate(`/alero-vendors?tenant=${tenant}&role=${encodeURIComponent(r.name ?? "")}`)
            }
          >
            <Badge variant="info" className="hover:opacity-80">
              {r.vendors}
            </Badge>
          </button>
        ) : (
          <span className="text-muted-foreground">0</span>
        ),
      sortValue: (r) => r.vendors,
    },
    {
      key: "invitations",
      header: "Invitaciones",
      align: "right",
      render: (r) =>
        r.invitations ? (
          <Badge variant="warning">{r.invitations}</Badge>
        ) : (
          <span className="text-muted-foreground">0</span>
        ),
      sortValue: (r) => r.invitations,
    },
  ];

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <DataTable
      rows={q.data.roles}
      total={q.data.count}
      columns={cols}
      tableId="alero-roles"
      getRowKey={(r, i) => r.id ?? r.name ?? i}
      filterOn={(r) => [r.name, r.description]}
      filterPlaceholder="filtrar por nombre / descripción"
      minWidth={700}
    />
  );
}

export default function AleroRoles() {
  return <TenantScope title="Roles">{(tenant) => <AleroRolesView tenant={tenant} />}</TenantScope>;
}
