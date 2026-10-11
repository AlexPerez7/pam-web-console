import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Square } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";

/** Botón "Terminar" por fila en la grilla de Sesiones — sin el paso de
 *  confirmación inline que usan las secciones de Fase 3 (no hay
 *  `confirm_phrase`: terminar una sesión no borra nada persistente, el
 *  usuario se puede reconectar). Sólo si `session.terminate` está
 *  habilitada y el operador es `operator+`. Ver
 *  `docs/specs/session-terminate.md`. */
export function SessionTerminateButton({
  tenant,
  sessionId,
}: {
  tenant: string;
  sessionId: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        <Square className="size-3.5" />
        Terminar
      </Button>

      <DryRunDialog
        open={open}
        onClose={() => setOpen(false)}
        path={`/tenants/${tenant}/sessions/${encodeURIComponent(sessionId)}/terminate`}
        changes={{}}
        title="Terminar sesión"
        onDone={() => {
          setOpen(false);
          qc.invalidateQueries({ queryKey: ["inv", tenant, "sessions"] });
        }}
      />
    </>
  );
}
