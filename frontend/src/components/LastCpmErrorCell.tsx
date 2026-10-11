import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cpmFailures } from "@/lib/cpmFailures";
import { fmtDateTime } from "@/lib/format";
import type { AccountActivitiesResponse } from "@/lib/types";

/** Columna "Último error" de Troubleshooting — el motivo real por el que
 *  el CPM dejó de gestionar la cuenta (`secretManagement` sólo da el
 *  estado, no el porqué). Reusa `/accounts/{id}/activities` (mismo
 *  queryKey que `AccountDetailPanel`, comparte caché) pero **al click**,
 *  no de entrada para las N cuentas de la lista — evita N llamadas a
 *  CyberArk sólo por abrir la página. */
export function LastCpmErrorCell({ tenant, accountId }: { tenant: string; accountId: string }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["account-activities", tenant, accountId],
    queryFn: () =>
      api<AccountActivitiesResponse>(`/tenants/${tenant}/accounts/${accountId}/activities`),
    enabled: open,
    staleTime: 5 * 60_000,
  });

  if (!open) {
    return (
      <Button
        variant="ghost"
        size="sm"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        Ver error
      </Button>
    );
  }
  if (q.isLoading) return <span className="text-xs text-muted-foreground">Cargando…</span>;
  if (q.isError) return <span className="text-xs text-destructive">No se pudo cargar</span>;

  const fail = cpmFailures(q.data?.activities ?? [])[0];
  if (!fail)
    return <span className="text-xs text-muted-foreground">Sin fallas en la actividad</span>;
  return (
    <span className="text-xs italic text-muted-foreground" title={fmtDateTime(fail.Date)}>
      “{fail.Reason || fail.Action}”
    </span>
  );
}
