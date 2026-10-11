import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { SiaWarnings } from "@/components/SiaWarnings";
import { TenantScope } from "@/components/TenantScope";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { dash, fmtDate } from "@/lib/format";
import {
  KIND_LABEL,
  POLICY_STATUS,
  locationLabel,
  type SiaPoliciesResponse,
  type SiaPolicyRow,
} from "@/lib/sia";

// Mismas columnas que "User access policies" del portal, más identidades,
// vencimiento y duración máxima (ocultables desde "Columnas").
const cols: Column<SiaPolicyRow>[] = [
  { key: "name", header: "Política", render: (r) => dash(r.name), sortValue: (r) => r.name },
  {
    key: "status",
    header: "Estado",
    width: 130,
    render: (r) => {
      const st = POLICY_STATUS[r.status];
      return <Badge variant={st.variant}>{st.label}</Badge>;
    },
    sortValue: (r) => POLICY_STATUS[r.status].label,
  },
  {
    key: "kind",
    header: "Categoría",
    width: 120,
    render: (r) => KIND_LABEL[r.kind],
    sortValue: (r) => r.kind,
  },
  {
    key: "locations",
    header: "Ubicación",
    width: 120,
    render: (r) => dash(r.locations.map(locationLabel).join(", ")),
    sortValue: (r) => r.locations.join(","),
  },
  {
    key: "principals",
    header: "Identidades",
    width: 110,
    align: "right",
    render: (r) => dash(r.principalsCount),
    sortValue: (r) => r.principalsCount,
  },
  {
    key: "maxSession",
    header: "Sesión máx.",
    width: 110,
    align: "right",
    render: (r) => (r.maxSessionHours != null ? `${r.maxSessionHours} h` : "—"),
    sortValue: (r) => r.maxSessionHours,
  },
  {
    key: "validTo",
    header: "Vence",
    width: 110,
    render: (r) => (r.validTo ? fmtDate(r.validTo) : "Nunca"),
    sortValue: (r) => r.validTo ?? "9999",
  },
  {
    key: "createdAt",
    header: "Creada",
    width: 110,
    render: (r) => fmtDate(r.createdAt),
    sortValue: (r) => r.createdAt,
  },
  {
    key: "modifiedAt",
    header: "Actualizada",
    width: 110,
    render: (r) => fmtDate(r.modifiedAt),
    sortValue: (r) => r.modifiedAt,
  },
  {
    key: "modifiedBy",
    header: "Actualizada por",
    width: 160,
    defaultHidden: true,
    render: (r) => dash(r.modifiedBy),
    sortValue: (r) => r.modifiedBy,
  },
  {
    key: "tags",
    header: "Tags",
    width: 160,
    render: (r) =>
      r.tags.length ? (
        <span className="flex flex-wrap gap-1">
          {r.tags.map((t) => (
            <Badge key={t} variant="neutral">
              {t}
            </Badge>
          ))}
        </span>
      ) : (
        "—"
      ),
  },
  {
    key: "description",
    header: "Descripción",
    width: 260,
    defaultHidden: true,
    render: (r) => dash(r.description),
  },
];

function PoliciesView({ tenant }: { tenant: string }) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["sia-policies", tenant],
    queryFn: () => api<SiaPoliciesResponse>(`/tenants/${tenant}/sia/policies`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <>
      <SiaWarnings warnings={q.data.warnings} />
      <DataTable
        rows={q.data.policies}
        total={q.data.count}
        columns={cols}
        tableId="sia-policies-v2"
        getRowKey={(r) => `${r.source}-${r.kind}-${r.id}`}
        filterOn={(r) => [
          r.name,
          r.description,
          KIND_LABEL[r.kind],
          POLICY_STATUS[r.status].label,
          r.tags.join(" "),
        ]}
        filterPlaceholder="Buscar por nombre, categoría, estado o tag"
        countNoun="políticas"
        minWidth={1100}
        onRowClick={(r) =>
          navigate(
            `/sia/policies/${encodeURIComponent(r.id)}?tenant=${tenant}&source=${r.source}&kind=${r.kind}`,
          )
        }
      />
    </>
  );
}

export default function SiaPolicies() {
  return (
    <TenantScope title="Políticas de acceso">
      {(tenant) => <PoliciesView tenant={tenant} />}
    </TenantScope>
  );
}
