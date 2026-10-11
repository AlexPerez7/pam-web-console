import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { BulkDeleteExecuteResponse, BulkDeletePreviewResponse } from "@/lib/types";

const MIN_REASON = 8;

/** Diálogo "Eliminar N cuenta(s)" del toolbar de Cuentas. Cada cuenta es
 *  independiente (recuperar contraseña + backup cifrado + borrar — ver
 *  `app.api.writes._delete_account_with_backup`): una que falla no aborta el
 *  resto del lote, y si el backup de una falla esa cuenta NO se borra. Sin
 *  `Plan.confirm_phrase` (no aplica a N objetos): la confirmación reforzada
 *  es escribir `ELIMINAR <N>`. Sólo visible para `platform_admin`. */
export function AccountBulkDeleteDialog({
  tenant,
  accountIds,
  onClose,
  onDone,
}: {
  tenant: string;
  accountIds: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [preview, setPreview] = useState<BulkDeletePreviewResponse | null>(null);
  const [done, setDone] = useState<BulkDeleteExecuteResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = accountIds.length > 0;
  // N = cantidad de cuentas del lote (no sólo las válidas) — igual que el backend.
  const expected = `ELIMINAR ${new Set(accountIds).size}`;

  // Se previsualiza apenas se abre: no hay nada que elegir antes (a diferencia de mover).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<BulkDeletePreviewResponse>(`/tenants/${tenant}/accounts/bulk-delete/preview`, {
      method: "POST",
      body: { account_ids: accountIds },
    })
      .then((p) => !cancelled && setPreview(p))
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenant]);

  function reset() {
    setReason("");
    setConfirmText("");
    setPreview(null);
    setDone(null);
    setError(null);
    onClose();
  }

  async function execute() {
    setLoading(true);
    setError(null);
    try {
      const result = await api<BulkDeleteExecuteResponse>(
        `/tenants/${tenant}/accounts/bulk-delete`,
        {
          method: "POST",
          body: { account_ids: accountIds, reason, confirm: confirmText },
        },
      );
      setDone(result);
      qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && reset()}>
      <DialogContent className="max-w-xl">
        <DialogTitle>Eliminar {accountIds.length} cuenta(s)</DialogTitle>

        {done ? (
          <div className="flex flex-col gap-3">
            <Alert variant={done.failed.length === 0 ? "success" : "warning"}>
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>
                  {done.deleted} de {done.total} cuentas eliminadas
                  {done.failed.length > 0 ? `, ${done.failed.length} fallaron` : ""}.
                </span>
              </div>
            </Alert>
            {done.failed.length > 0 && (
              <div className="max-h-64 overflow-y-auto rounded-lg border border-border">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th className="px-3 py-1.5 font-medium">Cuenta</th>
                      <th className="px-3 py-1.5 font-medium">Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {done.failed.map((f) => (
                      <tr key={f.account_id} className="border-t border-border">
                        <td className="px-3 py-1.5 font-medium">{f.name ?? f.account_id}</td>
                        <td className="px-3 py-1.5 text-danger-ink">{f.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <Alert variant="warning">
              Se pierde el historial nativo de CyberArk de cada cuenta (versiones de contraseña,
              actividad). Antes de borrar, cada cuenta queda respaldada con su contraseña actual
              (cifrada) y se puede restaurar desde Backups. Si el backup de una falla, esa cuenta
              no se borra.
            </Alert>

            {loading && !preview && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                Verificando las cuentas…
              </p>
            )}

            {preview && (
              <>
                <div className="flex items-center gap-2 text-sm">
                  <Badge variant={preview.invalid === 0 ? "success" : "warning"}>
                    {preview.valid} de {preview.total} se pueden eliminar
                  </Badge>
                </div>
                <div className="max-h-64 overflow-y-auto rounded-lg border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-3 py-1.5 font-medium">Cuenta</th>
                        <th className="px-3 py-1.5 font-medium">Safe</th>
                        <th className="px-3 py-1.5 font-medium">Estado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r) => (
                        <tr key={r.account_id} className="border-t border-border">
                          <td className="px-3 py-1.5">{r.name ?? r.account_id}</td>
                          <td className="px-3 py-1.5">{r.safe_name ?? "—"}</td>
                          <td className="px-3 py-1.5">
                            {r.ok ? (
                              <Badge variant="success">OK</Badge>
                            ) : (
                              <span className="text-danger-ink">{r.error}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {preview.valid > 0 && (
                  <>
                    <label className="text-xs font-medium text-muted-foreground">
                      Razón (queda en el audit, mínimo {MIN_REASON} caracteres)
                      <textarea
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        rows={2}
                        className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                        placeholder="ej. baja pedida por el cliente; ticket OPS-31"
                      />
                    </label>
                    <label className="text-xs font-medium text-muted-foreground">
                      Para confirmar, escriba <span className="font-mono">{expected}</span>
                      <input
                        value={confirmText}
                        onChange={(e) => setConfirmText(e.target.value)}
                        className="mt-1 h-9 w-full rounded-md border border-input bg-card px-2 text-sm"
                      />
                    </label>
                    <Button
                      size="sm"
                      variant="destructive"
                      className="self-start"
                      disabled={
                        reason.trim().length < MIN_REASON || confirmText !== expected || loading
                      }
                      onClick={execute}
                    >
                      {loading && <Loader2 className="size-3.5 animate-spin" />}
                      Eliminar {preview.valid} cuenta(s)
                    </Button>
                  </>
                )}
              </>
            )}

            {error && <Alert variant="destructive">{error}</Alert>}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => {
              if (done) onDone();
              reset();
            }}
          >
            {done ? "Cerrar" : "Cancelar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
