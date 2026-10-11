import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { PsmServerField } from "@/components/PsmServerField";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { PlatformPsmResponse, TenantSummary } from "@/lib/types";

/** Cuerpo de la tarjeta "Gestión de sesiones (PSM)" en el detalle de una plataforma —
 *  muestra el PSM asignado y permite reasignarlo (Fase 3). Reusa el mismo
 *  patrón de seguridad que `Platform_PSM_Migration.ps1`: backend hace
 *  backup + verificación + rollback automático ante el bug conocido de
 *  CyberArk. Gateado por `platform.psm.update` + `approver+`. Ver
 *  `docs/specs/platform-psm-migration.md`. */
export function PlatformPsmSection({
  tenant,
  platformId,
}: {
  tenant: string;
  platformId: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [newPsm, setNewPsm] = useState("");
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const psm = useQuery({
    queryKey: ["platform-psm", tenant, platformId],
    queryFn: () => api<PlatformPsmResponse>(`/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}/psm`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "approver");

  return (
    <div className="p-4">

      {psm.isLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      {psm.isError && <Alert variant="destructive">{(psm.error as Error).message}</Alert>}

      {psm.data && (
        <div className="flex flex-col gap-2">
          {!editing ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Servidor PSM asignado:</span>
              <span className="font-semibold text-navy-900">
                {psm.data.psm.PSMServerId ?? "ninguno"}
              </span>
              {psm.data.psm.PSMServerName &&
                psm.data.psm.PSMServerName !== psm.data.psm.PSMServerId && (
                  <span className="text-xs text-muted-foreground">
                    ({String(psm.data.psm.PSMServerName)})
                  </span>
                )}
              {allowed && (
                <Button variant="outline" size="sm" className="ml-auto" onClick={() => setEditing(true)}>
                  <Pencil className="size-3.5" />
                  Cambiar
                </Button>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <Alert variant="warning">
                CyberArk reemplaza el objeto de gestión de sesiones COMPLETO al guardar (bug
                conocido: puede borrar Connection Components de cuentas de dominio). PAM Web Console
                verifica después del cambio y revierte automáticamente si detecta algo
                distinto — igual que <code>Platform_PSM_Migration.ps1</code>.
              </Alert>
              <label className="text-xs text-muted-foreground">
                Nuevo PSM
                <div className="mt-1">
                  <PsmServerField tenant={tenant} value={newPsm} onChange={setNewPsm} />
                </div>
              </label>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={!newPsm.trim()}
                  onClick={() => setDialog({ psm_server_id: newPsm.trim() })}
                >
                  Previsualizar
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                  Cancelar
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}/psm`}
        method="PUT"
        changes={dialog ?? {}}
        title="Reasignar PSM"
        onDone={() => {
          setDialog(null);
          setEditing(false);
          setNewPsm("");
          qc.invalidateQueries({ queryKey: ["platform-psm", tenant, platformId] });
        }}
      />
    </div>
  );
}
