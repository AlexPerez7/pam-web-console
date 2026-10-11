import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { invalidateAccount } from "@/lib/invalidate";

type Mode = "vault" | "next";

const LABEL: Record<Mode, string> = {
  vault: "Cambiar sólo en la Vault",
  next: "Especificar próxima contraseña (CPM)",
};

const HINT: Record<Mode, string> = {
  vault:
    "Actualiza sólo el valor guardado en CyberArk, SIN tocar el sistema destino. Usar únicamente si la contraseña real ya se cambió por fuera de CyberArk — si no coincide, la próxima autenticación con esta cuenta va a fallar.",
  next: "El CPM va a usar este valor en su PRÓXIMO cambio automático — no cambia nada ahora.",
};

const PATH: Record<Mode, string> = {
  vault: "change-in-vault",
  next: "set-next-password",
};

/** Diálogo "Definir contraseña" — versión controlada de
 *  `AccountPasswordSection`, ahora con el `mode` fijo (elegido desde el
 *  dropdown "Más" del panel de detalle en vez de un selector interno de 2
 *  botones). */
export function AccountPasswordDialog({
  open,
  onClose,
  tenant,
  accountId,
  mode,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  tenant: string;
  accountId: string;
  mode: Mode;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [newPassword, setNewPassword] = useState("");
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (open) setNewPassword("");
  }, [open]);

  function close() {
    setNewPassword("");
    setDialog(null);
    onClose();
  }

  return (
    <>
      <Dialog open={open && dialog === null} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogTitle>{LABEL[mode]}</DialogTitle>
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted-foreground">{HINT[mode]}</p>
            <Input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Nueva contraseña"
              className="h-8 text-sm"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              Cancelar
            </Button>
            <Button disabled={!newPassword} onClick={() => setDialog({ new_password: newPassword })}>
              Previsualizar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/accounts/${encodeURIComponent(accountId)}/${PATH[mode]}`}
        changes={dialog ?? {}}
        title={LABEL[mode]}
        onDone={() => {
          close();
          invalidateAccount(qc, tenant, accountId);
          onDone();
        }}
      />
    </>
  );
}
