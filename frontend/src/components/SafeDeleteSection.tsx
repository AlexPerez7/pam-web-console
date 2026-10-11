import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { invalidateSafe } from "@/lib/invalidate";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";

/** Sección "Borrar safe" en el detalle de una safe (Fase 3). Sólo aparece
 *  si el tenant tiene `safe.delete` habilitada y el operador es
 *  `approver+`. Sólo borra safes VACÍAS — si tiene cuentas, el backend
 *  rechaza con 422 antes de tocar CyberArk. Ver
 *  `docs/specs/account-safe-delete.md`. */
export function SafeDeleteSection({
  tenant,
  safeId,
  safeName,
  onDeleted,
}: {
  tenant: string;
  safeId: string;
  safeName: string;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "approver");
  if (!allowed) return null;

  return (
    <section className="min-w-[160px] flex-1">
      {!confirming ? (
        <Button variant="destructive" size="sm" onClick={() => setConfirming(true)}>
          <Trash2 className="size-3.5" />
          Borrar safe
        </Button>
      ) : (
        <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
          <p className="text-sm">
            ¿Borrar la safe <span className="font-medium">{safeName}</span>? Sólo funciona si
            está vacía — si tiene cuentas, se rechaza sin tocar CyberArk.
          </p>
          <div className="flex gap-2">
            <Button variant="destructive" size="sm" onClick={() => setDialog({})}>
              Continuar
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/safes/${encodeURIComponent(safeId)}`}
        method="DELETE"
        changes={dialog ?? {}}
        title="Borrar safe"
        onDone={() => {
          setDialog(null);
          setConfirming(false);
          qc.invalidateQueries({ queryKey: ["inv", tenant, "safes"] });
          invalidateSafe(qc, tenant, safeId);
          onDeleted();
        }}
      />
    </section>
  );
}
