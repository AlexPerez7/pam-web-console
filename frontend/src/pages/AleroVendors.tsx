import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Avatar, AleroExpirationBadge, AleroStatusBadge } from "@/components/AleroBadges";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { AleroVendorRow, AleroVendorsResponse } from "@/lib/types";

const cols: Column<AleroVendorRow>[] = [
  {
    key: "fullName",
    header: "Vendor",
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
  { key: "email", header: "Email", render: (r) => dash(r.email), sortValue: (r) => r.email },
  {
    key: "companyName",
    header: "Empresa",
    render: (r) => dash(r.companyName),
    sortValue: (r) => r.companyName,
  },
  {
    key: "status",
    header: "Estado",
    render: (r) => <AleroStatusBadge status={r.status} />,
    sortValue: (r) => r.status,
  },
  {
    key: "expirationStatus",
    header: "Vigencia de acceso",
    render: (r) => <AleroExpirationBadge status={r.expirationStatus} />,
    sortValue: (r) => r.expirationStatus,
  },
  {
    key: "roles",
    header: "Roles",
    width: 240,
    render: (r) => dash(r.roles?.join(", ")),
  },
  {
    key: "groups",
    header: "Grupos",
    width: 240,
    render: (r) => dash(r.groups?.join(", ")),
  },
];

function AleroVendorsView({ tenant }: { tenant: string }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const roleFilter = params.get("role");
  const q = useQuery({
    queryKey: ["alero-vendors", tenant],
    queryFn: () => api<AleroVendorsResponse>(`/tenants/${tenant}/alero-vendors`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;

  const rows = roleFilter
    ? q.data.vendors.filter((v) => v.roles?.includes(roleFilter))
    : q.data.vendors;

  function clearRoleFilter() {
    setParams(
      (p) => {
        p.delete("role");
        return p;
      },
      { replace: true },
    );
  }

  return (
    <DataTable
      rows={rows}
      total={q.data.count}
      columns={cols}
      tableId="alero-vendors"
      getRowKey={(r, i) => r.username ?? i}
      onRowClick={(r) => r.id && navigate(`/alero-vendors/${r.id}?tenant=${tenant}`)}
      filterOn={(r) => [r.username, r.fullName, r.email, r.companyName, r.status]}
      filterPlaceholder="filtrar por usuario / nombre / email / empresa / estado"
      minWidth={1100}
      toolbar={
        roleFilter ? (
          <button type="button" onClick={clearRoleFilter} className="cursor-pointer">
            <Badge variant="info" className="flex items-center gap-1">
              Rol: {roleFilter}
              <X className="size-3" />
            </Badge>
          </button>
        ) : undefined
      }
    />
  );
}

export default function AleroVendors() {
  return <TenantScope title="Vendors">{(tenant) => <AleroVendorsView tenant={tenant} />}</TenantScope>;
}
