import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { DetailDrawer } from "@/components/DetailDrawer";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { IdentityUserRow, IdentityUsersResponse } from "@/lib/types";

const cols: Column<IdentityUserRow>[] = [
  { key: "username", header: "Usuario", render: (u) => dash(u.username), sortValue: (u) => u.username },
  { key: "displayName", header: "Nombre", render: (u) => dash(u.displayName), sortValue: (u) => u.displayName },
  { key: "email", header: "Email", render: (u) => dash(u.email), sortValue: (u) => u.email },
  { key: "status", header: "Estado", render: (u) => dash(u.status), sortValue: (u) => u.status },
];

function SsoUsersView({ tenant }: { tenant: string }) {
  const [sel, setSel] = useState<IdentityUserRow | null>(null);
  const q = useQuery({
    queryKey: ["identity-users", tenant],
    queryFn: () => api<IdentityUsersResponse>(`/tenants/${tenant}/identity-users`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <>
      <DataTable
        rows={q.data.users}
        total={q.data.count}
        columns={cols}
        tableId="identity-sso-users"
        getRowKey={(u, i) => u.id ?? u.username ?? i}
        filterOn={(u) => [u.username, u.displayName, u.email, u.status]}
        filterPlaceholder="filtrar por usuario / nombre / email"
        minWidth={800}
        onRowClick={setSel}
      />
      <DetailDrawer
        opened={!!sel}
        onClose={() => setSel(null)}
        title={sel?.username ?? "Usuario de Identity"}
        object={sel as Record<string, unknown> | null}
        tenant={tenant}
        identityUserId={sel?.id != null ? String(sel.id) : undefined}
      />
    </>
  );
}

export default function SsoUsers() {
  return (
    <TenantScope title="Usuarios (Identity)">
      {(tenant) => <SsoUsersView tenant={tenant} />}
    </TenantScope>
  );
}
