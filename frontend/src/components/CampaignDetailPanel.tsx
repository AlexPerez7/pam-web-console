import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { AccountDetailPanel } from "@/components/AccountDetailPanel";
import { TroubleshootingAccountPanel } from "@/components/TroubleshootingAccountPanel";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { CampaignDetailResponse, CampaignMemberRow } from "@/lib/types";

const STATUS_BADGE: Record<CampaignMemberRow["status"], "success" | "danger" | "neutral"> = {
  resolved: "success",
  pending: "danger",
  not_found: "neutral",
};
const STATUS_LABEL: Record<CampaignMemberRow["status"], string> = {
  resolved: "resuelta",
  pending: "pendiente",
  not_found: "no encontrada",
};

/** Detalle de una campaña — avance calculado en vivo (no un historial
 *  guardado): cada apertura vuelve a resolver el estado de las cuentas
 *  miembro contra CyberArk. Click en una fila pendiente abre
 *  `TroubleshootingAccountPanel` (reusado) para remediar sin salir de
 *  acá. Ver docs/specs/done/rotation-campaigns.md. */
export function CampaignDetailPanel({
  tenant,
  campaignId,
  opened,
  onClose,
}: {
  tenant: string;
  campaignId: number | null;
  opened: boolean;
  onClose: () => void;
}) {
  const [remediating, setRemediating] = useState<CampaignMemberRow | null>(null);
  const [fullView, setFullView] = useState(false);

  const detail = useQuery({
    queryKey: ["campaign-detail", tenant, campaignId],
    queryFn: () => api<CampaignDetailResponse>(`/tenants/${tenant}/campaigns/${campaignId}`),
    enabled: opened && campaignId != null,
  });

  return (
    <>
      <Sheet open={opened} onOpenChange={(o) => !o && onClose()}>
        <SheetContent className="w-full max-w-xl">
          <SheetTitle>{detail.data?.name ?? "Campaña"}</SheetTitle>

          {detail.isLoading && (
            <div className="flex justify-center py-10">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          )}
          {detail.isError && (
            <Alert variant="destructive">{(detail.error as Error).message}</Alert>
          )}

          {detail.data && (
            <div className="flex flex-col gap-4">
              {detail.data.description && (
                <p className="text-sm text-muted-foreground">{detail.data.description}</p>
              )}
              <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-xs">
                <div>
                  <span className="block font-semibold uppercase tracking-wide text-muted-foreground">
                    Creada por
                  </span>
                  {detail.data.created_by} · {fmtDateTime(detail.data.created_at)}
                </div>
                <div>
                  <span className="block font-semibold uppercase tracking-wide text-muted-foreground">
                    Razón
                  </span>
                  {detail.data.reason}
                </div>
              </div>

              <div className="flex items-center gap-2 text-sm">
                <Badge variant="success">{detail.data.resolved} resueltas</Badge>
                <Badge variant="danger">{detail.data.pending} pendientes</Badge>
                {detail.data.not_found > 0 && (
                  <Badge variant="neutral">{detail.data.not_found} no encontradas</Badge>
                )}
              </div>

              <ul className="divide-y divide-border rounded-lg border border-border">
                {detail.data.accounts.map((m) => (
                  <li
                    key={m.account_id}
                    className={m.status === "pending" ? "cursor-pointer hover:bg-accent/50" : ""}
                    onClick={() => {
                      if (m.status !== "pending") return;
                      setRemediating(m);
                      setFullView(false);
                    }}
                  >
                    <div className="flex items-center justify-between gap-2 px-3 py-2.5">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{m.account_name}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {m.safe_name} · {m.platform_id}
                        </div>
                      </div>
                      <Badge variant={STATUS_BADGE[m.status]}>{STATUS_LABEL[m.status]}</Badge>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SheetContent>
      </Sheet>

      <TroubleshootingAccountPanel
        opened={!!remediating && !fullView}
        onClose={() => setRemediating(null)}
        tenant={tenant}
        accountId={remediating?.account_id}
        onOpenFull={() => setFullView(true)}
      />

      <AccountDetailPanel
        opened={!!remediating && fullView}
        onClose={() => setRemediating(null)}
        object={null}
        tenant={tenant}
        accountId={remediating?.account_id}
      />
    </>
  );
}
