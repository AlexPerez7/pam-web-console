import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import type { DryRunResult, WriteResult } from "@/lib/types";
import { toast } from "@/components/ui/toast";

const MIN_REASON = 8;

interface Props {
  open: boolean;
  onClose: () => void;
  /** endpoint de escritura, ej. /tenants/x/accounts/42/update */
  path: string;
  /** método HTTP del endpoint de escritura (default POST; Fase 3 usa PUT/DELETE) */
  method?: "POST" | "PUT" | "DELETE";
  /** cambios ya calculados por el formulario del llamador */
  changes: Record<string, unknown>;
  /** campos extra a nivel raíz del body, junto a `changes`/`dry_run`/
   *  `reason` (ej. `account_ids` en un bulk-update) */
  extraBody?: Record<string, unknown>;
  title: string;
  onDone: () => void;
  /** aprobando una solicitud pendiente de otro operador: se manda en
   *  `X-PAM-Approval-Id` y el backend valida que el plan no haya cambiado */
  approvalId?: string;
  /** quién pidió la solicitud que se está aprobando (sólo para mostrar) */
  requestedBy?: string;
}

/** Flujo genérico dry-run → diff → (Fase 3: confirmación reforzada) →
 *  razón → confirmar. */
export function DryRunDialog({
  open,
  onClose,
  path,
  method = "POST",
  changes,
  extraBody,
  title,
  onDone,
  approvalId,
  requestedBy,
}: Props) {
  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");

  const headers = approvalId ? { "X-PAM-Approval-Id": approvalId } : undefined;
  const preview = useMutation({
    mutationFn: () =>
      api<DryRunResult>(path, {
        method,
        headers,
        body: { ...extraBody, changes, dry_run: true },
      }),
  });
  const confirm = useMutation({
    mutationFn: () =>
      api<WriteResult>(path, {
        method,
        headers,
        body: { ...extraBody, changes, dry_run: false, reason, confirm: confirmText || undefined },
      }),
    onSuccess: (r) => {
      // además del mensaje dentro del diálogo: el toast queda visible
      // después de cerrarlo, y lo anuncia el lector de pantalla
      if (r.pending_approval) {
        toast({
          title: "Solicitud de aprobación enviada",
          description: `${title}: otro operador debe aprobarla en Aprobaciones.`,
          variant: "info",
        });
      } else if (!r.no_op) {
        toast({ title: approvalId ? "Aprobado y ejecutado" : "Cambio aplicado", description: title });
      }
    },
    // no llamamos onDone acá -- el diálogo se queda abierto mostrando el
    // resultado; onDone (que suele cerrar el wrapper del caller) recién se
    // dispara cuando el usuario cierra este diálogo a mano (ver footer).
  });

  function dismiss() {
    if (confirm.isSuccess) onDone();
    onClose();
  }

  useEffect(() => {
    if (open) {
      setReason("");
      setConfirmText("");
      confirm.reset();
      preview.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const p = preview.data;
  const done = confirm.isSuccess;
  const pending = confirm.data?.pending_approval === true;
  const needsApproval = !!p?.requires_approval;
  const drifted = !!approvalId && p?.approval_matches === false;
  const confirmPhrase = p?.confirm_phrase ?? null;
  const confirmMatches = !confirmPhrase || confirmText === confirmPhrase;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && dismiss()}>
      <DialogContent className="max-w-lg">
        <DialogTitle>{title}</DialogTitle>

        {done ? (
          <Alert variant="success">
            <div className="flex items-start gap-2">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
              <span>
                {pending
                  ? "Solicitud enviada. No se aplicó ningún cambio todavía: otro operador (approver o platform_admin) debe aprobarla desde Aprobaciones."
                  : p?.is_cpm_action
                    ? "Pedido enviado al CPM. Lo procesa en su próximo ciclo; el resultado aparece en la actividad del detalle de cada cuenta."
                    : approvalId
                      ? "Solicitud aprobada y cambio aplicado."
                      : "Cambio aplicado."}
              </span>
            </div>
          </Alert>
        ) : null}
        {done && (confirm.data?.object?.failed?.length ?? 0) > 0 && (
          <Alert variant="warning">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <div>
                <p>CyberArk rechazó el pedido para estas cuentas (el resto se envió bien):</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">
                  {confirm.data!.object!.failed!.map((f) => (
                    <li key={f.account_id}>
                      <span className="font-medium">{f.account_id}</span>: {f.error}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Alert>
        )}

        {!done && approvalId && (
          <Alert variant="info">
            Está aprobando una solicitud de <span className="font-semibold">{requestedBy}</span>.
            El cambio se calculó de nuevo contra el estado actual; al confirmar se ejecuta.
          </Alert>
        )}
        {!done && needsApproval && (
          <Alert variant="info">
            Esta operación requiere la aprobación de un segundo operador. Al confirmar se envía
            la solicitud; no se aplica nada hasta que otro operador la apruebe.
          </Alert>
        )}
        {!done && drifted && (
          <Alert variant="destructive">
            El objeto cambió desde que se hizo la solicitud: lo que se ejecutaría ya no es lo
            que se pidió. No se puede aprobar; hay que volver a solicitarla.
          </Alert>
        )}

        {!done && preview.isPending && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Calculando cambios…
          </div>
        )}
        {!done && preview.isError && (
          <Alert variant="destructive">{(preview.error as Error).message}</Alert>
        )}

        {!done && p && p.no_op && (
          <p className="text-sm text-muted-foreground">Sin cambios respecto al valor actual.</p>
        )}

        {!done && p && !p.no_op && (
          <>
            {p.diff.length > 0 ? (
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
                    {p.diff.map((d) => (
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
            ) : p.effect ? (
              <p className="text-sm">{p.effect.replace(/\*\*/g, "")}</p>
            ) : null}

            {p.warnings.length > 0 && (
              <Alert variant="warning">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  {p.warnings.length === 1 ? (
                    <span>{p.warnings[0]}</span>
                  ) : (
                    <ul className="list-disc space-y-1 pl-4">
                      {p.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </Alert>
            )}

            <label className="text-xs font-medium text-muted-foreground">
              Razón del cambio (queda en el audit, mínimo {MIN_REASON} caracteres)
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                placeholder="ej. la base migró de host; ticket OPS-1234"
              />
            </label>

            {confirmPhrase && (
              <label className="text-xs font-medium text-muted-foreground">
                Para confirmar, escriba exactamente <code className="font-semibold">{confirmPhrase}</code>
                <input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                  placeholder={confirmPhrase}
                  autoComplete="off"
                />
              </label>
            )}

            {confirm.isError && (
              <Alert variant="destructive">{(confirm.error as Error).message}</Alert>
            )}
          </>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={dismiss} disabled={confirm.isPending}>
            {done ? "Cerrar" : "Cancelar"}
          </Button>
          {!done && p && !p.no_op && (
            <Button
              onClick={() => confirm.mutate()}
              disabled={
                reason.trim().length < MIN_REASON ||
                !confirmMatches ||
                drifted ||
                confirm.isPending
              }
            >
              {confirm.isPending && <Loader2 className="size-4 animate-spin" />}
              {approvalId ? "Aprobar y ejecutar" : needsApproval ? "Solicitar aprobación" : "Confirmar"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
