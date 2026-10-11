import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { CampaignCreateResponse } from "@/lib/types";
import { toast } from "@/components/ui/toast";

const MIN_REASON = 8;

/** Diálogo "Crear campaña" — se abre desde Troubleshooting con las
 *  cuentas tildadas. Sin dry-run (no hay nada que previsualizar contra
 *  CyberArk, es bookkeeping de PAM Web Console). Ver
 *  docs/specs/done/rotation-campaigns.md. */
export function CampaignCreateDialog({
  open,
  onClose,
  tenant,
  accountIds,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  tenant: string;
  accountIds: string[];
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [reason, setReason] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api<CampaignCreateResponse>(`/tenants/${tenant}/campaigns`, {
        method: "POST",
        body: {
          name,
          description: description || undefined,
          reason,
          account_ids: accountIds,
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns", tenant] });
      toast({ title: "Campaña creada", description: name });
    },
  });

  function reset() {
    setName("");
    setDescription("");
    setReason("");
    create.reset();
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && reset()}>
      <DialogContent className="max-w-lg">
        <DialogTitle>Crear campaña</DialogTitle>

        {create.isSuccess ? (
          <Alert variant="success">
            <div className="flex items-start gap-2">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
              <span>
                Campaña creada con {create.data.members} cuenta(s)
                {create.data.missing.length > 0
                  ? ` (${create.data.missing.length} ya no existían en CyberArk)`
                  : ""}
                .
              </span>
            </div>
          </Alert>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              {accountIds.length} cuenta(s) seleccionada(s). No toca CyberArk — sólo agrupa las
              cuentas para hacerles seguimiento de remediación.
            </p>
            <label className="text-xs text-muted-foreground">
              Nombre
              <Input
                className="mt-1 h-8 text-sm"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ej. Migración SolarWinds"
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Descripción (opcional)
              <Input
                className="mt-1 h-8 text-sm"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label className="text-xs font-medium text-muted-foreground">
              Razón (queda en el audit, mínimo {MIN_REASON} caracteres)
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                placeholder="ej. cuentas afectadas por la migración; ticket OPS-30"
              />
            </label>
            {create.isError && (
              <Alert variant="destructive">{(create.error as Error).message}</Alert>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={reset}>
            {create.isSuccess ? "Cerrar" : "Cancelar"}
          </Button>
          {!create.isSuccess && (
            <Button
              disabled={!name.trim() || reason.trim().length < MIN_REASON || create.isPending}
              onClick={() =>
                create.mutate(undefined, {
                  onSuccess: () => onDone(),
                })
              }
            >
              {create.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Crear
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
