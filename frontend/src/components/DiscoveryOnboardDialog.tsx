import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { PlatformField } from "@/components/PlatformField";
import { SafeField } from "@/components/SafeField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { DiscoveredAccount } from "@/lib/types";

/** Onboardea una cuenta descubierta al Vault vía el endpoint REAL de
 *  Discovery (`POST .../discovered-accounts/{id}/onboard`) -- no el
 *  workaround del primer corte, que reusaba `account.create` con un body
 *  que no coincidía y apuntaba a una ruta inexistente. `<DryRunDialog>`
 *  genérico (preview -> razón -> confirmar), mismo patrón que
 *  `AccountCreateWizard`. */
export function DiscoveryOnboardDialog({
  tenant,
  account,
  open,
  onOpenChange,
}: {
  tenant: string;
  account: DiscoveredAccount | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [platformId, setPlatformId] = useState("");
  const [safeName, setSafeName] = useState("");
  const [secret, setSecret] = useState("");
  const [resetSecret, setResetSecret] = useState(true);
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    if (!open) {
      setPlatformId("");
      setSafeName("");
      setSecret("");
      setResetSecret(true);
      setConfirmed(false);
    }
  }, [open]);

  if (!account) return null;

  const username = String(account.identifiers?.username ?? "");
  const address = String(account.identifiers?.address ?? "");

  const changes: Record<string, unknown> = {
    safe_name: safeName,
    platform_id: platformId,
    reset_secret: resetSecret,
  };
  if (secret) changes.secret = secret;

  return (
    <>
      <Dialog open={open && !confirmed} onOpenChange={(o) => !o && onOpenChange(false)}>
        <DialogContent>
          <DialogTitle>Onboard cuenta descubierta</DialogTitle>
          <p className="text-sm text-muted-foreground">
            Añadir {username} en {address} al Vault.
          </p>

          <div className="flex flex-col gap-4 py-2">
            <div className="space-y-2">
              <Label>Plataforma</Label>
              <PlatformField tenant={tenant} value={platformId} onChange={setPlatformId} />
            </div>

            <div className="space-y-2">
              <Label>Safe</Label>
              <SafeField tenant={tenant} value={safeName} onChange={setSafeName} />
            </div>

            <div className="space-y-2">
              <Label>Contraseña inicial (opcional si el CPM va a reconciliar)</Label>
              <Input
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="Dejar en blanco para que el CPM la gestione"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={resetSecret}
                onChange={(e) => setResetSecret(e.target.checked)}
              />
              Rotar la contraseña al onboardear (recomendado)
            </label>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button disabled={!platformId || !safeName} onClick={() => setConfirmed(true)}>
              Previsualizar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DryRunDialog
        open={confirmed}
        onClose={() => setConfirmed(false)}
        path={`/tenants/${tenant}/discovered-accounts/${account.id}/onboard`}
        changes={changes}
        title="Onboard cuenta descubierta"
        onDone={() => {
          onOpenChange(false);
          qc.invalidateQueries({ queryKey: ["discovery", tenant] });
          qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
        }}
      />
    </>
  );
}
