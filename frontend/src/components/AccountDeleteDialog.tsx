import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { invalidateAccount } from "@/lib/invalidate";

/** Diálogo "Borrar cuenta" — versión controlada de `AccountDeleteSection`
 *  (Fase 3). Guarda una copia de seguridad completa (incluida la
 *  contraseña vigente) antes de borrar — ver
 *  `docs/specs/account-safe-delete.md` y la página "Backups" para
 *  recuperarla. */
export function AccountDeleteDialog({
  open,
  onClose,
  tenant,
  accountId,
  accountName,
  onDeleted,
}: {
  open: boolean;
  onClose: () => void;
  tenant: string;
  accountId: string;
  accountName: string;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  return (
    <>
      <Dialog open={open && dialog === null} onOpenChange={(o) => !o && onClose()}>
        <DialogContent>
          <DialogTitle>Borrar cuenta</DialogTitle>
          <p className="text-sm">
            ¿Borrar la cuenta <span className="font-medium">{accountName}</span>? Se guarda una
            copia de seguridad completa (incluida la contraseña vigente) antes de borrar.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>
              Cancelar
            </Button>
            <Button variant="destructive" onClick={() => setDialog({})}>
              Continuar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/accounts/${encodeURIComponent(accountId)}`}
        method="DELETE"
        changes={dialog ?? {}}
        title="Borrar cuenta"
        onDone={() => {
          setDialog(null);
          onClose();
          qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
          invalidateAccount(qc, tenant, accountId);
          onDeleted();
        }}
      />
    </>
  );
}
