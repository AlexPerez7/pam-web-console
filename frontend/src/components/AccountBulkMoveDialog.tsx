import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";
import { SafeField } from "@/components/SafeField";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { BulkMoveExecuteResponse, BulkMovePreviewResponse } from "@/lib/types";

const MIN_REASON = 8;

/** Diálogo "Mover N cuenta(s)" del toolbar de Cuentas — equivalente al
 *  tilde-múltiple + "Move" de la vista clásica del PVWA. Cada cuenta es
 *  independiente (retrieve + backup + create en destino + delete de la
 *  original, con rollback si algo falla a mitad de camino — ver
 *  `app.api.writes._move_account`); una cuenta que falla no aborta el
 *  resto del lote, mismo criterio que "Importar CSV"
 *  (`AccountBulkCreateDialog`). Sin `Plan.confirm_phrase` (no aplica a N
 *  objetos): la confirmación reforzada es escribir el nombre exacto de la
 *  Safe destino. Sólo visible para `platform_admin` — mismo techo que el
 *  move de una cuenta. */
export function AccountBulkMoveDialog({
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
  const [safeName, setSafeName] = useState("");
  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [preview, setPreview] = useState<BulkMovePreviewResponse | null>(null);
  const [done, setDone] = useState<BulkMoveExecuteResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = accountIds.length > 0;

  function reset() {
    setSafeName("");
    setReason("");
    setConfirmText("");
    setPreview(null);
    setDone(null);
    setError(null);
    onClose();
  }

  async function doPreview() {
    if (!safeName) return;
    setLoading(true);
    setError(null);
    try {
      setPreview(
        await api<BulkMovePreviewResponse>(`/tenants/${tenant}/accounts/bulk-move/preview`, {
          method: "POST",
          body: { account_ids: accountIds, safe_name: safeName },
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function execute() {
    setLoading(true);
    setError(null);
    try {
      const result = await api<BulkMoveExecuteResponse>(
        `/tenants/${tenant}/accounts/bulk-move`,
        {
          method: "POST",
          body: {
            account_ids: accountIds,
            safe_name: safeName,
            reason,
            confirm: confirmText,
          },
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
        <DialogTitle>Mover {accountIds.length} cuenta(s) de Safe</DialogTitle>

        {done ? (
          <div className="flex flex-col gap-3">
            <Alert variant={done.failed.length === 0 ? "success" : "warning"}>
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>
                  {done.moved} de {done.total} cuentas movidas
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
                        <td className="px-3 py-1.5 font-medium">{f.account_id}</td>
                        <td className="px-3 py-1.5 text-destructive">{f.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              Se pierde el historial nativo de CyberArk de cada cuenta (versiones de
              contraseña, actividad) — mismo criterio que mover una cuenta de a una. Cada
              cuenta queda respaldada antes de borrarse; si una falla, no afecta al resto del
              lote.
            </p>
            <label className="text-xs font-medium text-muted-foreground">
              Safe destino
              <div className="mt-1">
                <SafeField
                  tenant={tenant}
                  value={safeName}
                  onChange={(v) => {
                    setSafeName(v);
                    setPreview(null);
                  }}
                />
              </div>
            </label>

            {!preview && (
              <Button
                size="sm"
                className="self-start"
                disabled={!safeName || loading}
                onClick={doPreview}
              >
                {loading && <Loader2 className="size-3.5 animate-spin" />}
                Previsualizar
              </Button>
            )}

            {preview && (
              <>
                <div className="flex items-center gap-2 text-sm">
                  <Badge variant={preview.invalid === 0 ? "success" : "warning"}>
                    {preview.valid} de {preview.total} válidas
                  </Badge>
                </div>
                <div className="max-h-64 overflow-y-auto rounded-lg border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-3 py-1.5 font-medium">Cuenta</th>
                        <th className="px-3 py-1.5 font-medium">Estado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r) => (
                        <tr key={r.account_id} className="border-t border-border">
                          <td className="px-3 py-1.5">{r.name ?? r.account_id}</td>
                          <td className="px-3 py-1.5">
                            {r.ok ? (
                              <Badge variant="success">OK</Badge>
                            ) : (
                              <span className="text-destructive">{r.error}</span>
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
                        placeholder="ej. reorganización de safes; ticket OPS-20"
                      />
                    </label>
                    <label className="text-xs font-medium text-muted-foreground">
                      Para confirmar, escriba el nombre exacto de la Safe destino (
                      <span className="font-mono">{safeName}</span>)
                      <input
                        value={confirmText}
                        onChange={(e) => setConfirmText(e.target.value)}
                        className="mt-1 h-9 w-full rounded-md border border-input bg-card px-2 text-sm"
                      />
                    </label>
                    <Button
                      size="sm"
                      className="self-start"
                      disabled={
                        reason.trim().length < MIN_REASON || confirmText !== safeName || loading
                      }
                      onClick={execute}
                    >
                      {loading && <Loader2 className="size-3.5 animate-spin" />}
                      Mover {preview.valid} cuenta(s)
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
