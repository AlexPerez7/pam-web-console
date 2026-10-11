import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { IdentityRoleCreateDialog } from "@/components/IdentityRoleCreateDialog";
import { IdentityRoleDetailPanel } from "@/components/IdentityRoleDetailPanel";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { IdentityRoleRow, IdentityRolesResponse } from "@/lib/types";

/** CyberArk devuelve el código interno de `RoleType`, no la etiqueta que
 *  muestra PVWA ("Static"/"Dynamic") -- confirmado contra Globex
 *  (2026-09-25): `PrincipalList` (lista fija de miembros) y `Script` (rol
 *  dinámico, membresía calculada por un script) son los únicos valores
 *  vistos hasta ahora. Cualquier otro código se muestra tal cual, sin
 *  inventar una traducción sin confirmar. */
const ROLE_TYPE_LABELS: Record<string, string> = {
  PrincipalList: "Static",
  Script: "Dynamic",
};

function roleTypeLabel(v: string | null | undefined): string {
  if (!v) return "—";
  return ROLE_TYPE_LABELS[v] ?? v;
}

const cols: Column<IdentityRoleRow>[] = [
  {
    key: "name",
    header: "Rol",
    render: (r) => dash(r.name),
    sortValue: (r) => r.name,
    filterable: false,
  },
  {
    key: "roleType",
    header: "Tipo",
    width: 110,
    render: (r) => roleTypeLabel(r.roleType),
    sortValue: (r) => r.roleType,
    filterable: false,
  },
  {
    key: "description",
    header: "Descripción",
    width: 320,
    render: (r) => dash(r.description),
    sortValue: (r) => r.description,
    filterable: false,
  },
  {
    key: "organization",
    header: "Organización",
    width: 160,
    render: (r) => dash(r.organization),
    sortValue: (r) => r.organization,
    filterable: false,
  },
];

function IdentityRolesView({ tenant }: { tenant: string }) {
  const [sel, setSel] = useState<IdentityRoleRow | null>(null);
  const q = useQuery({
    queryKey: ["identity-roles", tenant],
    queryFn: () => api<IdentityRolesResponse>(`/tenants/${tenant}/identity-roles`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <>
      <DataTable
        rows={q.data.roles}
        total={q.data.count}
        columns={cols}
        tableId="identity-roles"
        getRowKey={(r, i) => r.id ?? r.name ?? i}
        filterOn={(r) => [r.name, r.description, roleTypeLabel(r.roleType), r.organization]}
        filterPlaceholder="Buscar en todos los roles"
        minWidth={760}
        toolbar={<IdentityRoleCreateDialog tenant={tenant} />}
        onRowClick={setSel}
      />
      <IdentityRoleDetailPanel
        tenant={tenant}
        role={sel}
        typeLabel={roleTypeLabel(sel?.roleType)}
        onClose={() => setSel(null)}
      />
    </>
  );
}

export default function IdentityRoles() {
  return (
    <TenantScope title="Roles">{(tenant) => <IdentityRolesView tenant={tenant} />}</TenantScope>
  );
}
