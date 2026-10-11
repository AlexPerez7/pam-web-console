import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { DiscoveredAccount, DiscoveredAccountActivity } from "@/lib/types";

/** Historial de actividades de las reglas de remediación de Discovery
 *  sobre una cuenta descubierta puntual (por qué no se onboardeó sola,
 *  qué rule set matcheó, etc.) -- `GET .../discovered-accounts/{id}/
 *  activities`, sólo lectura, `viewer+`. Ver
 *  `docs.cyberark.com/.../discovery-discoveredaccountsservice-
 *  getaccountactivities.htm`. */
export function DiscoveredAccountActivitiesDialog({
  tenant,
  account,
  open,
  onOpenChange,
}: {
  tenant: string;
  account: DiscoveredAccount | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const q = useQuery({
    queryKey: ["discovery-activities", tenant, account?.id],
    queryFn: () =>
      api<{ activities: DiscoveredAccountActivity[] }>(
        `/tenants/${tenant}/discovered-accounts/${account!.id}/activities`,
      ),
    enabled: open && !!account,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogTitle>Actividad de Discovery</DialogTitle>

        {q.isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Cargando…
          </div>
        )}
        {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}
        {q.data && q.data.activities.length === 0 && (
          <p className="text-sm text-muted-foreground">Sin actividad registrada.</p>
        )}
        {q.data && q.data.activities.length > 0 && (
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {q.data.activities.map((a) => (
              <div key={a.id} className="rounded-md border border-border p-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{a.activity}</span>
                  <span className="text-xs text-muted-foreground">{dash(a.creationTime)}</span>
                </div>
                {a.message && <p className="mt-1 text-muted-foreground">{a.message}</p>}
                {a.ruleSets && a.ruleSets.length > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Rule sets: {a.ruleSets.map((rs) => rs.name).join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
