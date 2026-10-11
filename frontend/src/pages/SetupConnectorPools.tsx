import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { ConnectorPoolRow, ConnectorPoolsResponse } from "@/lib/types";

function PoolsView({ tenant }: { tenant: string }) {
  const q = useQuery({
    queryKey: ["setup-connector-pools", tenant],
    queryFn: () => api<ConnectorPoolsResponse>(`/tenants/${tenant}/setup/connector-pools`),
  });

  const cols: Column<ConnectorPoolRow>[] = [
    { key: "name", header: "Pool", render: (r) => dash(r.name), sortValue: (r) => r.name },
    {
      key: "description",
      header: "Descripción",
      width: 320,
      render: (r) => dash(r.description),
      sortValue: (r) => r.description,
    },
    {
      key: "count",
      header: "Conectores",
      align: "right",
      render: (r) => <Badge variant="info">{r.connectorCount}</Badge>,
      sortValue: (r) => r.connectorCount,
    },
    {
      key: "hosts",
      header: "Hosts",
      render: (r) => dash(r.connectors.map((c) => c.hostname).join(", ")),
    },
  ];

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <DataTable
      rows={q.data.pools}
      total={q.data.count}
      columns={cols}
      tableId="setup-connector-pools"
      getRowKey={(r) => r.poolId}
      filterOn={(r) => [r.name, r.description]}
      filterPlaceholder="filtrar por nombre / descripción"
      minWidth={700}
    />
  );
}

export default function SetupConnectorPools() {
  return <TenantScope title="Pools de conectores">{(tenant) => <PoolsView tenant={tenant} />}</TenantScope>;
}
