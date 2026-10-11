import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { dash, fmtDateTime, relTime } from "@/lib/format";
import type { ApprovalRequest } from "@/lib/types";
import { toast, toastError } from "@/components/ui/toast";

// Cola de aprobaciones de un segundo operador (principio 4 de CLAUDE.md,
// docs/specs/dual-approval.md). "Aprobar" re-envía el request original con
// el header X-PAM-Approval-Id a través del DryRunDialog de siempre: el
// aprobador ve el diff RECALCULADO contra el estado actual antes de
// confirmar, y escribe su propia razón y confirmación.

const MIN_REASON = 8;

const STATUS: Record<
  ApprovalRequest["status"],
  { label: string; variant: "info" | "success" | "danger" | "warning" | "neutral" }
> = {
  pending: { label: "Pendiente", variant: "info" },
  approved: { label: "Aprobada", variant: "success" },
  rejected: { label: "Rechazada", variant: "danger" },
  cancelled: { label: "Cancelada", variant: "neutral" },
  expired: { label: "Vencida", variant: "warning" },
  stale: { label: "Desactualizada", variant: "warning" },
};

interface ListResponse {
  count: number;
  approvals: ApprovalRequest[];
}

function RejectDialog({
  approval,
  onClose,
}: {
  approval: ApprovalRequest | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const reject = useMutation({
    mutationFn: () =>
      api(`/approvals/${approval?.id}/reject`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      toast({ title: "Solicitud rechazada", variant: "info" });
      setReason("");
      onClose();
    },
  });
  return (
    <Dialog open={!!approval} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogTitle>Rechazar solicitud</DialogTitle>
        <label className="text-xs font-medium text-muted-foreground">
          Motivo del rechazo (se le muestra al solicitante y queda en el audit)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
          />
        </label>
        {reject.isError && <Alert variant="destructive">{(reject.error as Error).message}</Alert>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Volver
          </Button>
          <Button
            variant="destructive"
            onClick={() => reject.mutate()}
            disabled={reason.trim().length < MIN_REASON || reject.isPending}
          >
            {reject.isPending && <Loader2 className="size-4 animate-spin" />}
            Rechazar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ApprovalCard({
  a,
  onApprove,
  onReject,
}: {
  a: ApprovalRequest;
  onApprove: () => void;
  onReject: () => void;
}) {
  const qc = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => api(`/approvals/${a.id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      toast({ title: "Solicitud cancelada", variant: "info" });
    },
    onError: (e) => toastError("No se pudo cancelar la solicitud", e),
  });
  const st = STATUS[a.status];

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              {a.tenant_id} · {a.feature}
            </div>
            <div className="truncate font-semibold">{a.target}</div>
          </div>
          <Badge variant={st.variant}>{st.label}</Badge>
        </div>

        <div className="grid gap-1 text-sm sm:grid-cols-2">
          <div>
            <span className="text-muted-foreground">Solicitó: </span>
            <span className="font-medium">{a.requested_by}</span>{" "}
            <span className="text-muted-foreground">({relTime(a.created_at)})</span>
          </div>
          <div>
            <span className="text-muted-foreground">
              {a.status === "pending" ? "Vence: " : "Resuelta: "}
            </span>
            {a.status === "pending"
              ? fmtDateTime(a.expires_at)
              : `${dash(a.decided_by)} · ${fmtDateTime(a.decided_at)}`}
          </div>
          <div className="sm:col-span-2">
            <span className="text-muted-foreground">Razón: </span>
            {a.request_reason}
          </div>
          {a.decision_reason && (
            <div className="sm:col-span-2">
              <span className="text-muted-foreground">Motivo de la decisión: </span>
              {a.decision_reason}
            </div>
          )}
        </div>

        {a.diff.length > 0 ? (
          <div className="rounded-lg border border-border">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="px-3 py-1.5 font-medium">Campo</th>
                  <th className="px-3 py-1.5 font-medium">Antes</th>
                  <th className="px-3 py-1.5 font-medium">Después</th>
                </tr>
              </thead>
              <tbody>
                {a.diff.map((d) => (
                  <tr key={d.field} className="border-t border-border">
                    <td className="px-3 py-1.5 font-medium">{d.field}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">
                      {dash(String(d.from ?? ""))}
                    </td>
                    <td className="px-3 py-1.5">{dash(String(d.to ?? ""))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : a.effect ? (
          <p className="text-sm">{a.effect.replace(/\*\*/g, "")}</p>
        ) : null}

        {a.warnings.length > 0 && (
          <Alert variant="warning">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <ul className="list-disc space-y-1 pl-4">
                {a.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          </Alert>
        )}

        {a.status === "pending" && (
          <div className="flex flex-wrap justify-end gap-2">
            {a.is_mine && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
              >
                Cancelar solicitud
              </Button>
            )}
            {a.can_approve && (
              <>
                <Button variant="outline" size="sm" onClick={onReject}>
                  Rechazar
                </Button>
                <Button size="sm" onClick={onApprove}>
                  Revisar y aprobar
                </Button>
              </>
            )}
            {a.is_mine && !a.can_approve && (
              <span className="self-center text-xs text-muted-foreground">
                Esperando a otro operador
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function Approvals() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"pending" | "all">("pending");
  const [approving, setApproving] = useState<ApprovalRequest | null>(null);
  const [rejecting, setRejecting] = useState<ApprovalRequest | null>(null);

  const q = useQuery({
    queryKey: ["approvals", status],
    queryFn: () => api<ListResponse>(`/approvals?status=${status}`),
    refetchInterval: 30_000,
  });

  const { changes, ...extraBody } = (approving?.body ?? {}) as {
    changes?: Record<string, unknown>;
  } & Record<string, unknown>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Aprobaciones</h1>
          <p className="text-sm text-muted-foreground">
            Las operaciones de alto riesgo que no ejecuta un platform_admin necesitan que otro
            operador las apruebe. Nadie puede aprobar su propia solicitud.
          </p>
        </div>
        <div className="flex gap-1 rounded-md border border-border p-0.5">
          {(["pending", "all"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={
                "rounded px-3 py-1 text-sm " +
                (status === s ? "bg-accent font-medium" : "text-muted-foreground")
              }
            >
              {s === "pending" ? "Pendientes" : "Todas"}
            </button>
          ))}
        </div>
      </div>

      {q.isPending && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Cargando…
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}
      {q.data && q.data.approvals.length === 0 && (
        <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {status === "pending"
            ? "No hay solicitudes pendientes."
            : "Todavía no hay solicitudes de aprobación."}
        </p>
      )}
      <div className="grid gap-3 xl:grid-cols-2">
        {q.data?.approvals.map((a) => (
          <ApprovalCard
            key={a.id}
            a={a}
            onApprove={() => setApproving(a)}
            onReject={() => setRejecting(a)}
          />
        ))}
      </div>

      {approving && (
        <DryRunDialog
          open
          onClose={() => setApproving(null)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["approvals"] });
            setApproving(null);
          }}
          path={approving.path}
          method={approving.method}
          changes={changes ?? {}}
          extraBody={extraBody}
          title={`Aprobar: ${approving.feature} · ${approving.target}`}
          approvalId={approving.id}
          requestedBy={approving.requested_by}
        />
      )}
      <RejectDialog approval={rejecting} onClose={() => setRejecting(null)} />
    </div>
  );
}
