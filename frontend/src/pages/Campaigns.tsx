import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { CampaignDetailPanel } from "@/components/CampaignDetailPanel";
import { DataTable, type Column } from "@/components/DataTable";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import type { CampaignRow, CampaignsResponse } from "@/lib/types";
import { toast, toastError } from "@/components/ui/toast";

function DeleteCampaignButton({ tenant, id }: { tenant: string; id: number }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const del = useMutation({
    mutationFn: () => api(`/tenants/${tenant}/campaigns/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns", tenant] });
      toast({ title: "Campaña borrada" });
    },
    onError: (e) => toastError("No se pudo borrar la campaña", e),
  });

  if (!confirming) {
    return (
      <Button
        variant="ghost"
        size="sm"
        onClick={(e) => {
          e.stopPropagation();
          setConfirming(true);
        }}
        aria-label="Borrar campaña"
      >
        <Trash2 className="size-3.5" />
      </Button>
    );
  }
  return (
    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <Button
        variant="destructive"
        size="sm"
        disabled={del.isPending}
        onClick={() => del.mutate()}
      >
        Borrar
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
        Cancelar
      </Button>
    </div>
  );
}

function columns(tenant: string): Column<CampaignRow>[] {
  return [
    { key: "name", header: "Nombre", render: (c) => dash(c.name) },
    { key: "reason", header: "Razón", width: 280, sortable: false, render: (c) => dash(c.reason) },
    { key: "created_by", header: "Creada por", render: (c) => dash(c.created_by) },
    {
      key: "created_at",
      header: "Creada",
      render: (c) => fmtDateTime(c.created_at),
      sortValue: (c) => c.created_at,
    },
    {
      key: "progress",
      header: "Avance",
      sortable: false,
      filterable: false,
      render: (c) => (
        <div className="flex items-center gap-1.5">
          <Badge variant={c.pending === 0 ? "success" : "danger"}>
            {c.resolved} de {c.total} resueltas
          </Badge>
          {c.not_found > 0 && <Badge variant="neutral">{c.not_found} no encontradas</Badge>}
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      sortable: false,
      filterable: false,
      width: 60,
      render: (c) => <DeleteCampaignButton tenant={tenant} id={c.id} />,
    },
  ];
}

function CampaignsView({ tenant }: { tenant: string }) {
  const [sel, setSel] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["campaigns", tenant],
    queryFn: () => api<CampaignsResponse>(`/tenants/${tenant}/campaigns`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  if (q.data.campaigns.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        Sin campañas todavía — se crean desde Troubleshooting, tildando cuentas en error.
      </p>
    );
  return (
    <>
      <DataTable
        rows={q.data.campaigns}
        columns={columns(tenant)}
        tableId="campaigns"
        getRowKey={(c) => c.id}
        minWidth={900}
        onRowClick={(c) => setSel(c.id)}
        filterPlaceholder="buscar por nombre / razón / creada por"
      />
      <CampaignDetailPanel
        tenant={tenant}
        campaignId={sel}
        opened={sel != null}
        onClose={() => setSel(null)}
      />
    </>
  );
}

export default function Campaigns() {
  return (
    <TenantScope title="Campañas">{(tenant) => <CampaignsView tenant={tenant} />}</TenantScope>
  );
}
