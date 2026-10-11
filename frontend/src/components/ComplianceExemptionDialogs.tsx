import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { toast, toastError } from "@/components/ui/toast";
import { api } from "@/lib/api";
import type { ComplianceExemption } from "@/lib/types";

/** Invalida todo lo que puede mostrar un número de compliance stale tras
 *  crear/quitar una excepción (ver docs/specs/compliance-exemptions.md):
 *  la lista de excepciones, el estado de ESA cuenta puntual, el desglose
 *  navegable de Compliance (cualquier motivo) y el % del Dashboard. */
function invalidateCompliance(
  qc: ReturnType<typeof useQueryClient>,
  tenant: string,
  accountId: string,
) {
  for (const key of [
    ["compliance-exemptions", tenant],
    ["compliance-status", tenant, accountId],
    ["compliance", tenant],
    ["dashboard", tenant],
  ]) {
    qc.invalidateQueries({ queryKey: key });
  }
}

const MIN_REASON = 5;

/** Alta de una excepción de compliance (R2). `accountId` fijo (ej. desde la
 *  ficha de cuenta) la muestra de sólo lectura; sin él (desde la página
 *  Compliance) el operador la escribe a mano. */
export function ComplianceExemptionCreateDialog({
  open,
  onClose,
  tenant,
  accountId,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  tenant: string;
  /** si se omite, se pide como campo editable (alta desde la página Compliance) */
  accountId?: string;
  onDone?: () => void;
}) {
  const qc = useQueryClient();
  const [id, setId] = useState(accountId ?? "");
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!open) return;
    setId(accountId ?? "");
    setReason("");
  }, [open, accountId]);

  const create = useMutation({
    mutationFn: () =>
      api<ComplianceExemption>(`/tenants/${tenant}/compliance/exemptions`, {
        method: "POST",
        body: { account_id: id.trim(), reason: reason.trim() },
      }),
    onSuccess: (row) => {
      invalidateCompliance(qc, tenant, row.account_id);
      toast({ title: "Cuenta excluida del % de compliance", description: row.account_id });
      onDone?.();
      onClose();
    },
  });

  const valid = id.trim().length > 0 && reason.trim().length >= MIN_REASON;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogTitle>Excluir del % de compliance</DialogTitle>
        <p className="text-sm text-muted-foreground">
          Para cuentas con la gestión automática deshabilitada a pedido del cliente, para
          siempre (ej. cuentas sólo custodiadas, sin uso) — no CyberArk: la excepción vive en
          PAM Web Console, queda auditada, y se puede quitar en cualquier momento.
        </p>
        <div className="flex flex-col gap-3">
          {!accountId && (
            <label className="text-xs font-medium text-muted-foreground">
              ID de cuenta
              <Input
                className="mt-1 h-8 text-sm"
                value={id}
                onChange={(e) => setId(e.target.value)}
                placeholder="ej. 12_34"
              />
            </label>
          )}
          <label className="text-xs font-medium text-muted-foreground">
            Motivo (obligatorio — queda en el audit)
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="ej. Pedido del cliente: cuenta root sólo almacenada, no se conecta ni rota."
              className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
            />
          </label>
          {create.isError && (
            <Alert variant="destructive">{(create.error as Error).message}</Alert>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button disabled={!valid || create.isPending} onClick={() => create.mutate()}>
            {create.isPending && <Loader2 className="size-3.5 animate-spin" />}
            Excluir
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Baja de una excepción activa (R3). */
export function ComplianceExemptionDeleteDialog({
  exemption,
  onClose,
  tenant,
}: {
  exemption: ComplianceExemption | null;
  onClose: () => void;
  tenant: string;
}) {
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () =>
      api(`/tenants/${tenant}/compliance/exemptions/${encodeURIComponent(exemption!.account_id)}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      invalidateCompliance(qc, tenant, exemption!.account_id);
      toast({ title: "Excepción quitada", description: exemption!.account_id, variant: "info" });
      onClose();
    },
    onError: (e) => toastError("No se pudo quitar la excepción", e),
  });

  return (
    <Dialog open={!!exemption} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogTitle>Quitar excepción de compliance</DialogTitle>
        {exemption && (
          <p className="text-sm">
            La cuenta <span className="font-medium">{exemption.account_id}</span> vuelve a
            evaluarse normal — si sigue con la gestión deshabilitada y vencida, volverá a
            restar del % de compliance.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="destructive" onClick={() => remove.mutate()} disabled={remove.isPending}>
            {remove.isPending && <Loader2 className="size-3.5 animate-spin" />}
            Quitar excepción
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
