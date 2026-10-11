import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/DataTable";
import { DiscoveredAccountActivitiesDialog } from "@/components/DiscoveredAccountActivitiesDialog";
import { DiscoveryOnboardDialog } from "@/components/DiscoveryOnboardDialog";
import { DryRunDialog } from "@/components/DryRunDialog";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import { roleAtLeast } from "@/lib/roles";
import type { DiscoveredAccount, TenantSummary } from "@/lib/types";
import { MoreHorizontal } from "lucide-react";

const STATUS_VARIANT: Record<string, "danger" | "warning" | "info"> = {
  ERROR: "danger",
  WARNING: "warning",
  INFO: "info",
};

function identity(row: DiscoveredAccount): string {
  const username = String(row.identifiers?.username ?? row.identifiers?.address ?? row.id);
  const address = row.identifiers?.address ? String(row.identifiers.address) : "";
  return address ? `${username}@${address}` : username;
}

function DiscoveryView({ tenant }: { tenant: string }) {
  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const canWrite = roleAtLeast(t.data?.my_role, "operator");

  const q = useQuery({
    queryKey: ["discovery", tenant],
    queryFn: () =>
      api<{ accounts: DiscoveredAccount[]; total: number }>(
        `/tenants/${tenant}/discovered-accounts?limit=200`,
      ),
    enabled: !!tenant,
  });

  const [onboardTarget, setOnboardTarget] = useState<DiscoveredAccount | null>(null);
  const [activitiesTarget, setActivitiesTarget] = useState<DiscoveredAccount | null>(null);
  const [actionTarget, setActionTarget] = useState<{
    account: DiscoveredAccount;
    kind: "ignore" | "restore" | "delete";
  } | null>(null);

  const columns: Column<DiscoveredAccount>[] = [
    {
      key: "identity",
      header: "Cuenta",
      render: (row) => identity(row),
    },
    {
      key: "type",
      header: "Tipo",
      render: (row) => `${row.type}${row.subType ? ` / ${row.subType}` : ""}`,
    },
    {
      key: "osFamily",
      header: "Sistema operativo",
      render: (row) => dash(String(row.customProperties?.osFamily ?? "")),
    },
    {
      key: "isPrivileged",
      header: "Privilegiada",
      render: (row) => (row.isPrivileged ? "Sí" : "No"),
    },
    {
      key: "lastActivityStatus",
      header: "Último estado",
      render: (row) =>
        row.lastActivityStatus ? (
          <Badge variant={STATUS_VARIANT[row.lastActivityStatus] ?? "neutral"}>
            {row.lastActivityStatus}
          </Badge>
        ) : (
          dash("")
        ),
    },
    {
      key: "updateTime",
      header: "Actualizado",
      render: (row) => dash(row.updateTime ?? ""),
    },
    {
      key: "actions",
      header: "Acciones",
      render: (row) => (
        <div className="flex items-center justify-end gap-1">
          {canWrite && (
            <Button size="sm" variant="outline" onClick={() => setOnboardTarget(row)}>
              Onboard
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost" aria-label="Más acciones">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setActivitiesTarget(row)}>
                Ver actividad
              </DropdownMenuItem>
              {canWrite && (
                <>
                  <DropdownMenuItem
                    onSelect={() => setActionTarget({ account: row, kind: "ignore" })}
                  >
                    Ignorar
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => setActionTarget({ account: row, kind: "restore" })}
                  >
                    Restaurar (si estaba ignorada)
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => setActionTarget({ account: row, kind: "delete" })}
                  >
                    Borrar de la lista
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ];

  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;

  const actionLabel = { ignore: "Ignorar cuenta", restore: "Restaurar cuenta", delete: "Borrar de descubiertas" };
  const actionPath = (id: string, kind: "ignore" | "restore" | "delete") =>
    kind === "delete"
      ? { path: `/tenants/${tenant}/discovered-accounts/${id}`, method: "DELETE" as const }
      : { path: `/tenants/${tenant}/discovered-accounts/${id}/${kind}`, method: "POST" as const };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold tracking-tight">Cuentas Descubiertas</h1>
        <p className="text-muted-foreground">
          Cuentas pendientes de onboarding detectadas por CyberArk Discovery ({q.data.total} en total).
        </p>
      </div>

      <DataTable<DiscoveredAccount>
        rows={q.data.accounts}
        columns={columns}
        getRowKey={(row) => row.id}
        tableId="discovery-table"
        filterPlaceholder="Buscar por usuario o dirección..."
      />

      <DiscoveryOnboardDialog
        tenant={tenant}
        account={onboardTarget}
        open={onboardTarget !== null}
        onOpenChange={(o) => !o && setOnboardTarget(null)}
      />

      <DiscoveredAccountActivitiesDialog
        tenant={tenant}
        account={activitiesTarget}
        open={activitiesTarget !== null}
        onOpenChange={(o) => !o && setActivitiesTarget(null)}
      />

      {actionTarget && (
        <DryRunDialog
          open={actionTarget !== null}
          onClose={() => setActionTarget(null)}
          {...actionPath(actionTarget.account.id, actionTarget.kind)}
          changes={{}}
          title={actionLabel[actionTarget.kind]}
          onDone={() => {
            setActionTarget(null);
            q.refetch();
          }}
        />
      )}
    </div>
  );
}

export function Discovery() {
  return (
    <TenantScope title="Descubrimiento">{(tenant) => <DiscoveryView tenant={tenant} />}</TenantScope>
  );
}
