import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import type { ConnectorComponent, SystemConnectorsResponse } from "@/lib/types";

const STATUS_OK = new Set(["Active", "Initializing", "Downloaded"]);

const pct = (v: number | null) => (v === null ? "—" : `${v}%`);

function ComponentsTable({ components }: { components: ConnectorComponent[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase text-muted-foreground">
            <th className="py-2 pr-4">Estado</th>
            <th className="py-2 pr-4">Componente</th>
            <th className="py-2 pr-4">CPU</th>
            <th className="py-2 pr-4">Memoria</th>
            <th className="py-2 pr-4">Versión instalada</th>
            <th className="py-2 pr-4">Versión disponible</th>
            <th className="py-2">Última actividad</th>
          </tr>
        </thead>
        <tbody>
          {components.map((c, i) => {
            const ok = STATUS_OK.has(c.status);
            return (
              <tr key={`${c.name}-${i}`} className="border-b border-border last:border-0">
                <td className="py-3 pr-4">
                  {ok ? (
                    <CheckCircle2 className="size-5 text-success-ink" aria-label={c.status} />
                  ) : (
                    <XCircle className="size-5 text-danger-ink" aria-label={c.status || "Sin estado"} />
                  )}
                </td>
                <td className="py-3 pr-4 font-medium text-navy-900">
                  {dash(c.name)}
                  {c.acronym ? (
                    <span className="ml-1 text-muted-foreground">({c.acronym})</span>
                  ) : null}
                </td>
                <td className="py-3 pr-4">{pct(c.usedCpu)}</td>
                <td className="py-3 pr-4">{pct(c.usedMemory)}</td>
                <td className="py-3 pr-4">{dash(c.version)}</td>
                <td className="py-3 pr-4">
                  {c.upgradeVersions.length ? (
                    <Badge variant="info">{c.upgradeVersions.join(", ")}</Badge>
                  ) : (
                    <span className="text-muted-foreground">N/A</span>
                  )}
                </td>
                <td className="py-3">{c.updatedAt ? fmtDateTime(c.updatedAt) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function SetupConnectorDetail() {
  const { connectorId } = useParams<{ connectorId: string }>();
  const [params] = useSearchParams();
  const tenant = params.get("tenant") ?? "";

  // misma query (y misma caché) que el listado: el detalle sale de ahí
  const q = useQuery({
    queryKey: ["setup-connectors", tenant],
    queryFn: () => api<SystemConnectorsResponse>(`/tenants/${tenant}/setup/connectors`),
    enabled: !!tenant,
  });
  const c = q.data?.connectors.find((r) => r.connectorId === connectorId);

  return (
    <div className="flex flex-col gap-4">
      <Link
        to={`/setup/connectors?tenant=${tenant}`}
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Volver a Conectores
      </Link>

      {q.isLoading && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}
      {q.data && !c && <Alert variant="destructive">No se encontró el conector.</Alert>}

      {c && (
        <div className="flex flex-col gap-6 rounded-lg border border-border bg-card p-6">
          <div className="flex flex-col gap-2">
            <h1 className="text-[22px] font-bold text-navy-900">{dash(c.hostname)}</h1>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-muted-foreground">
              <span>
                Estado: <span className="text-navy-900">{dash(c.status)}</span>
              </span>
              <span>
                SO: <span className="text-navy-900">{dash(c.os)}</span>
              </span>
              <span>
                Plataforma: <span className="text-navy-900">{dash(c.platformType)}</span>
              </span>
              <span>
                Pool: <span className="text-navy-900">{dash(c.pool)}</span>
              </span>
              <span>
                Componentes: <span className="text-navy-900">{c.componentsTotal}</span>
              </span>
            </div>
          </div>

          <Tabs defaultValue="components" className="w-full">
            <TabsList className="mb-4">
              <TabsTrigger value="components">Componentes</TabsTrigger>
              <TabsTrigger value="details">Detalles del conector</TabsTrigger>
            </TabsList>
            <TabsContent value="components">
              <ComponentsTable components={c.components} />
            </TabsContent>
            <TabsContent value="details">
              <dl className="grid grid-cols-1 gap-x-8 gap-y-4 text-sm md:grid-cols-2 lg:grid-cols-3">
                {Object.entries(c.details).map(([k, v]) => (
                  <div key={k} className="flex flex-col gap-0.5">
                    <dt className="text-xs text-muted-foreground">{k}</dt>
                    <dd className="break-words font-medium text-navy-900">{v}</dd>
                  </div>
                ))}
              </dl>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
