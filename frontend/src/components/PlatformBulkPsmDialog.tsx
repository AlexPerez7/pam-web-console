import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";
import { PsmServerField } from "@/components/PsmServerField";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { PlatformBulkPsmExecuteResponse, PlatformBulkPsmPreviewResponse } from "@/lib/types";

const MIN_REASON = 8;

/** Diálogo "Mover N plataforma(s) de PSM" del toolbar de Plataformas — para
 *  una migración real (ej. un parche programado en uno de los dos
 *  servidores PSM de un tenant: mover todo lo que apunta ahí al otro
 *  mientras dura). Cada plataforma es independiente (mismo patrón de
 *  seguridad que el cambio de PSM de una sola: GET completo -> mutar ->
 *  PUT -> verificar -> rollback automático si CyberArk borra
 *  PSMConnectors/Connection Components — bug conocido, ver
 *  `app.api.writes._apply_platform_psm_update`); una que falla no aborta
 *  el resto del lote. Sin `Plan.confirm_phrase` (no aplica a N objetos):
 *  la confirmación reforzada es escribir el ID exacto del PSM destino.
 *  Mismo RBAC `approver+` que el cambio individual. Ver
 *  `docs/specs/done/platform-psm-bulk-migration.md`. */
export function PlatformBulkPsmDialog({
  tenant,
  platformIds,
  onClose,
  onDone,
}: {
  tenant: string;
  platformIds: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [psmServerId, setPsmServerId] = useState("");
  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [preview, setPreview] = useState<PlatformBulkPsmPreviewResponse | null>(null);
  const [done, setDone] = useState<PlatformBulkPsmExecuteResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = platformIds.length > 0;

  function reset() {
    setPsmServerId("");
    setReason("");
    setConfirmText("");
    setPreview(null);
    setDone(null);
    setError(null);
    onClose();
  }

  async function doPreview() {
    if (!psmServerId) return;
    setLoading(true);
    setError(null);
    try {
      setPreview(
        await api<PlatformBulkPsmPreviewResponse>(
          `/tenants/${tenant}/platforms/bulk-psm-update/preview`,
          { method: "POST", body: { platform_ids: platformIds, psm_server_id: psmServerId } },
        ),
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
      const result = await api<PlatformBulkPsmExecuteResponse>(
        `/tenants/${tenant}/platforms/bulk-psm-update`,
        {
          method: "POST",
          body: {
            platform_ids: platformIds,
            psm_server_id: psmServerId,
            reason,
            confirm: confirmText,
          },
        },
      );
      setDone(result);
      qc.invalidateQueries({ queryKey: ["inv", tenant, "platforms"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && reset()}>
      <DialogContent className="max-w-xl">
        <DialogTitle>Mover {platformIds.length} plataforma(s) de PSM</DialogTitle>

        {done ? (
          <div className="flex flex-col gap-3">
            <Alert variant={done.failed.length === 0 ? "success" : "warning"}>
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>
                  {done.updated.length} de {done.total} plataformas reasignadas
                  {done.failed.length > 0 ? `, ${done.failed.length} fallaron` : ""}.
                </span>
              </div>
            </Alert>
            {done.failed.length > 0 && (
              <div className="max-h-64 overflow-y-auto rounded-lg border border-border">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground">
                      <th className="px-3 py-1.5 font-medium">Plataforma</th>
                      <th className="px-3 py-1.5 font-medium">Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {done.failed.map((f) => (
                      <tr key={f.platform_id} className="border-t border-border">
                        <td className="px-3 py-1.5 font-medium">{f.platform_id}</td>
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
            <Alert variant="warning">
              CyberArk reemplaza el objeto de gestión de sesiones COMPLETO al guardar (bug
              conocido: puede borrar Connection Components de cuentas de dominio). Cada
              plataforma se verifica después del cambio y revierte automáticamente si detecta
              algo distinto — una que falla no afecta al resto del lote.
            </Alert>
            <label className="text-xs font-medium text-muted-foreground">
              PSM destino
              <div className="mt-1">
                <PsmServerField
                  tenant={tenant}
                  value={psmServerId}
                  onChange={(v) => {
                    setPsmServerId(v);
                    setPreview(null);
                  }}
                />
              </div>
            </label>

            {!preview && (
              <Button
                size="sm"
                className="self-start"
                disabled={!psmServerId || loading}
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
                        <th className="px-3 py-1.5 font-medium">Plataforma</th>
                        <th className="px-3 py-1.5 font-medium">Estado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((r) => (
                        <tr key={r.platform_id} className="border-t border-border">
                          <td className="px-3 py-1.5 font-medium">{r.platform_id}</td>
                          <td className="px-3 py-1.5">
                            {r.ok ? (
                              <Badge variant="success">
                                {String(r.diff?.[0]?.from ?? "")} → {String(r.diff?.[0]?.to ?? "")}
                              </Badge>
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
                        placeholder="ej. parche programado en el PSM origen; ticket OPS-20"
                      />
                    </label>
                    <label className="text-xs font-medium text-muted-foreground">
                      Para confirmar, escriba el ID exacto del PSM destino (
                      <span className="font-mono">{psmServerId}</span>)
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
                        reason.trim().length < MIN_REASON ||
                        confirmText !== psmServerId ||
                        loading
                      }
                      onClick={execute}
                    >
                      {loading && <Loader2 className="size-3.5 animate-spin" />}
                      Mover {preview.valid} plataforma(s)
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
