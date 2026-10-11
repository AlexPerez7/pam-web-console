import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { dash, relTime } from "@/lib/format";
import type {
  SiaConnectorRow,
  SiaConnectorsResponse,
  SystemConnectorRow,
  SystemConnectorsResponse,
} from "@/lib/types";

function SystemConnectors({ tenant }: { tenant: string }) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["setup-connectors", tenant],
    queryFn: () => api<SystemConnectorsResponse>(`/tenants/${tenant}/setup/connectors`),
  });

  const cols: Column<SystemConnectorRow>[] = [
    { key: "host", header: "Host", render: (r) => dash(r.hostname), sortValue: (r) => r.hostname },
    { key: "ip", header: "IP", render: (r) => dash(r.ip), sortValue: (r) => r.ip },
    { key: "os", header: "SO", render: (r) => dash(r.os), sortValue: (r) => r.os },
    {
      key: "platform",
      header: "Plataforma",
      render: (r) => dash(r.platformType),
      sortValue: (r) => r.platformType,
    },
    {
      key: "components",
      header: "Componentes",
      render: (r) => (
        <Badge variant={r.degraded ? "warning" : "success"}>
          {r.componentsActive}/{r.componentsTotal} activos
          {r.degraded ? " · degradado" : ""}
        </Badge>
      ),
      sortValue: (r) => r.componentsActive - r.componentsTotal,
    },
    {
      key: "heartbeat",
      header: "Último heartbeat",
      render: (r) => relTime(r.lastHeartbeat),
      sortValue: (r) => r.lastHeartbeat ?? "",
    },
    { key: "pool", header: "Pool", render: (r) => dash(r.pool), sortValue: (r) => r.pool ?? "" },
    {
      key: "upgrade",
      header: "Upgrade",
      render: (r) =>
        r.upgradeAvailable ? (
          <Badge variant="info" title={r.upgradeVersions.join(", ")}>
            Upgrade disponible
          </Badge>
        ) : (
          <span className="text-muted-foreground">Al día</span>
        ),
      sortValue: (r) => Number(r.upgradeAvailable),
    },
  ];

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <DataTable
      rows={q.data.connectors}
      total={q.data.count}
      columns={cols}
      tableId="setup-connectors"
      onRowClick={(r) =>
        navigate(`/setup/connectors/${encodeURIComponent(r.connectorId)}?tenant=${tenant}`)
      }
      getRowKey={(r) => r.connectorId}
      filterOn={(r) => [r.hostname, r.ip, r.pool, r.os]}
      filterPlaceholder="filtrar por host / IP / pool"
      minWidth={900}
    />
  );
}

function SiaConnectors({ tenant }: { tenant: string }) {
  const q = useQuery({
    queryKey: ["setup-sia-connectors", tenant],
    queryFn: () => api<SiaConnectorsResponse>(`/tenants/${tenant}/setup/sia-connectors`),
    retry: false,
  });

  const cols: Column<SiaConnectorRow>[] = [
    { key: "host", header: "Host", render: (r) => dash(r.hostname), sortValue: (r) => r.hostname },
    { key: "ip", header: "IP", render: (r) => dash(r.ip), sortValue: (r) => r.ip },
    { key: "os", header: "SO", render: (r) => dash(r.os), sortValue: (r) => r.os },
    { key: "status", header: "Estado", render: (r) => dash(r.status), sortValue: (r) => r.status },
    { key: "version", header: "Versión", render: (r) => dash(r.version), sortValue: (r) => r.version },
    {
      key: "latest",
      header: "Versión actual",
      render: (r) =>
        r.isLatestVersion === null ? (
          dash(null)
        ) : r.isLatestVersion ? (
          <span className="text-success-ink">Al día</span>
        ) : (
          <Badge variant="info">Upgrade disponible</Badge>
        ),
      sortValue: (r) => Number(r.isLatestVersion),
    },
  ];

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  return (
    <DataTable
      rows={q.data.connectors}
      total={q.data.count}
      columns={cols}
      tableId="setup-sia-connectors"
      getRowKey={(r) => r.connectorId}
      filterOn={(r) => [r.hostname, r.ip]}
      filterPlaceholder="filtrar por host / IP"
      minWidth={700}
    />
  );
}

export default function SetupConnectors() {
  return (
    <TenantScope title="Conectores">
      {(tenant) => (
        <Tabs defaultValue="system" className="w-full">
          <TabsList className="mb-4">
            <TabsTrigger value="system">System connectors</TabsTrigger>
            <TabsTrigger value="sia">SIA connectors</TabsTrigger>
          </TabsList>
          <TabsContent value="system">
            <SystemConnectors tenant={tenant} />
          </TabsContent>
          <TabsContent value="sia">
            <SiaConnectors tenant={tenant} />
          </TabsContent>
        </Tabs>
      )}
    </TenantScope>
  );
}
