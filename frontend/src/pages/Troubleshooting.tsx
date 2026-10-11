import { useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import { useRef, useState } from "react";
import { AccountDetailPanel } from "@/components/AccountDetailPanel";
import { BarList } from "@/components/BarList";
import { CampaignCreateDialog } from "@/components/CampaignCreateDialog";
import { Column, DataTable } from "@/components/DataTable";
import { LastCpmErrorCell } from "@/components/LastCpmErrorCell";
import { TroubleshootingAccountPanel } from "@/components/TroubleshootingAccountPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { dash, fmtDate } from "@/lib/format";
import type { FailedAccountRow, TenantSummary, TroubleshootingResponse } from "@/lib/types";

function Kpi({ label, value, color = "text-navy-900" }: { label: string; value: number; color?: string }) {
  return (
    <Card>
      <CardContent className="p-4 pt-4">
        <div className="mb-2 text-xs text-muted-foreground">{label}</div>
        <div className={`text-2xl font-bold leading-tight ${color}`}>
          {value.toLocaleString("es")}
        </div>
      </CardContent>
    </Card>
  );
}

function columns(tenant: string): Column<FailedAccountRow>[] {
  return [
    { key: "name", header: "Nombre de cuenta", render: (a) => dash(a.name) },
    { key: "userName", header: "Usuario", render: (a) => dash(a.userName) },
    { key: "address", header: "Address", render: (a) => dash(a.address) },
    { key: "safeName", header: "Safe", render: (a) => dash(a.safeName) },
    { key: "platformId", header: "Plataforma", render: (a) => dash(a.platformId) },
    {
      key: "retrying",
      header: "Estado",
      render: (a) => (
        <Badge variant={a.retrying ? "info" : "danger"}>
          {a.retrying ? "reintentando" : "gestión manual"}
        </Badge>
      ),
      sortValue: (a) => (a.retrying ? 1 : 0),
    },
    { key: "reason", header: "Motivo", render: (a) => dash(a.reason) },
    {
      key: "lastModifiedTime",
      header: "Última modificación",
      render: (a) => fmtDate(a.lastModifiedTime),
      sortValue: (a) => a.lastModifiedTime ?? null,
    },
    {
      key: "lastError",
      header: "Último error",
      sortable: false,
      filterable: false,
      render: (a) => <LastCpmErrorCell tenant={tenant} accountId={a.id} />,
    },
  ];
}

/** Sección "Troubleshooting" — errores de rotación/verificación del CPM,
 *  navegables por cuenta (a diferencia del Dashboard, que sólo los
 *  agrega). Click en una fila abre `TroubleshootingAccountPanel` (motivo
 *  de la falla + historial + acciones de remediación) — "Ver cuenta
 *  completa" ahí adentro cambia al `AccountDetailPanel` genérico de
 *  Inventario para lo demás. Ver docs/specs/troubleshooting-errores-cpm.md. */
export default function Troubleshooting() {
  const [tenant, setTenant] = useState<string | null>(null);
  const [sel, setSel] = useState<FailedAccountRow | null>(null);
  const [fullView, setFullView] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [creatingCampaign, setCreatingCampaign] = useState(false);
  const wantRefresh = useRef(false);

  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });

  const q = useQuery({
    queryKey: ["troubleshooting", tenant],
    queryFn: () => {
      const suffix = wantRefresh.current ? "?refresh=1" : "";
      wantRefresh.current = false;
      return api<TroubleshootingResponse>(
        `/tenants/${tenant}/troubleshooting/account-errors${suffix}`,
      );
    },
    enabled: !!tenant,
    staleTime: 5 * 60_000,
  });

  function doRefresh() {
    wantRefresh.current = true;
    q.refetch();
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">Troubleshooting</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Cuentas con error de rotación/verificación del CPM.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select value={tenant ?? undefined} onValueChange={setTenant}>
            <SelectTrigger aria-label="Tenant" className="w-52">
              <SelectValue placeholder="Elija un tenant" />
            </SelectTrigger>
            <SelectContent>
              {(tenants.data ?? []).map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {tenant && (
            <Button variant="outline" size="sm" onClick={doRefresh} disabled={q.isFetching}>
              {q.isFetching ? "Actualizando…" : "Actualizar"}
            </Button>
          )}
        </div>
      </div>

      {!tenant && <p className="text-sm text-muted-foreground">Elija un tenant para empezar.</p>}
      {q.isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {q.isError && (
        <p className="text-sm text-destructive">{(q.error as Error).message}</p>
      )}

      {q.data && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Kpi label="En error" value={q.data.summary.failed_total} color="text-destructive" />
            <Kpi label="Reintentando (CPM activo)" value={q.data.summary.active_retry} />
            <Kpi label="Gestión manual (CPM se rindió)" value={q.data.summary.given_up} />
          </div>

          {q.data.summary.by_reason.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle>Motivos</CardTitle>
              </CardHeader>
              <CardContent>
                <BarList data={q.data.summary.by_reason} color="var(--color-destructive)" />
              </CardContent>
            </Card>
          )}

          {q.data.accounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Sin cuentas en error en este tenant.
            </p>
          ) : (
            <DataTable
              rows={q.data.accounts}
              columns={columns(tenant!)}
              tableId="troubleshooting-accounts"
              getRowKey={(a) => a.id}
              minWidth={1000}
              onRowClick={(a) => {
                setSel(a);
                setFullView(false);
              }}
              selectedIds={selectedIds}
              onSelectedIdsChange={setSelectedIds}
              toolbar={
                selectedIds.size > 0 && (
                  <Button size="sm" variant="outline" onClick={() => setCreatingCampaign(true)}>
                    <Users className="size-3.5" />
                    Crear campaña ({selectedIds.size})
                  </Button>
                )
              }
              filterPlaceholder="buscar por nombre / usuario / address / safe / plataforma"
            />
          )}
        </>
      )}

      <TroubleshootingAccountPanel
        opened={!!sel && !fullView}
        onClose={() => setSel(null)}
        tenant={tenant ?? ""}
        accountId={sel?.id}
        onOpenFull={() => setFullView(true)}
      />

      <AccountDetailPanel
        opened={!!sel && fullView}
        onClose={() => setSel(null)}
        object={null}
        tenant={tenant ?? ""}
        accountId={sel?.id}
      />

      <CampaignCreateDialog
        open={creatingCampaign}
        onClose={() => setCreatingCampaign(false)}
        tenant={tenant ?? ""}
        accountIds={[...selectedIds]}
        onDone={() => setSelectedIds(new Set())}
      />
    </div>
  );
}
