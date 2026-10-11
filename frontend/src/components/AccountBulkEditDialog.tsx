import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { PlatformField } from "@/components/PlatformField";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Field = "address" | "userName" | "name" | "platformId" | "automaticManagementEnabled" | "remoteMachines";

const FIELD_LABEL: Record<Field, string> = {
  address: "Address",
  userName: "Usuario",
  name: "Nombre",
  platformId: "Plataforma",
  automaticManagementEnabled: "Gestión automática (CPM)",
  remoteMachines: "Remote machine access",
};

/** Editar el mismo campo a un grupo de cuentas tildadas en la grilla, con
 *  UN valor igual para todas — equivalente al tilde-múltiple + Editar de
 *  la vista clásica del PVWA. Sólo sirve cuando el valor nuevo es el mismo
 *  para todas las seleccionadas (ej. apuntar 5 cuentas de firewall a la
 *  misma address nueva); si cada cuenta necesita un valor distinto, se
 *  edita una por una. Ver docs/specs/account-bulk-edit.md. */
export function AccountBulkEditDialog({
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
  const [field, setField] = useState<Field>("address");
  const [value, setValue] = useState("");
  const [restrict, setRestrict] = useState(false);
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  const open = accountIds.length > 0;

  return (
    <>
      <Sheet open={open && dialog === null} onOpenChange={(o) => !o && onClose()}>
        <SheetContent className="flex w-full max-w-3xl flex-col gap-0 p-0 sm:max-w-[calc(100vw-16rem)]">
          <SheetTitle className="border-b border-border px-6 py-4 text-lg">
            Editar {accountIds.length} cuenta(s)
          </SheetTitle>
          <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-6">
            <p className="text-sm text-muted-foreground">
              El valor nuevo se aplica igual a las {accountIds.length} cuentas seleccionadas. Si
              necesitan valores distintos entre sí, edítelas una por una desde su detalle.
            </p>
            <div className="text-sm font-medium text-muted-foreground">
              Campo
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(Object.keys(FIELD_LABEL) as Field[]).map((f) => (
                  <Button
                    key={f}
                    type="button"
                    size="sm"
                    variant={field === f ? "default" : "outline"}
                    className="h-8"
                    onClick={() => {
                      setField(f);
                      setValue("");
                      setRestrict(false);
                    }}
                  >
                    {FIELD_LABEL[f]}
                  </Button>
                ))}
              </div>
            </div>
            <label className="text-sm font-medium text-muted-foreground">
              Valor nuevo
              {field === "platformId" ? (
                <div className="mt-1.5 font-normal">
                  <PlatformField tenant={tenant} value={value} onChange={setValue} />
                </div>
              ) : field === "automaticManagementEnabled" ? (
                <Select value={value} onValueChange={setValue}>
                  <SelectTrigger aria-label="Gestión automática de la contraseña" className="mt-1.5 h-9 font-normal">
                    <SelectValue placeholder="Seleccione..." />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="true">Habilitar rotación (Sí)</SelectItem>
                    <SelectItem value="false">Deshabilitar rotación (No)</SelectItem>
                  </SelectContent>
                </Select>
              ) : field === "remoteMachines" ? (
                <div className="mt-1.5 flex flex-col gap-3 font-normal">
                  <div className="flex items-center justify-between gap-2 rounded-md border border-border p-3">
                    <label htmlFor="bulk-restrict-remote" className="cursor-pointer text-sm font-medium text-muted-foreground">
                      Limit access to these machines only
                    </label>
                    <Switch
                      id="bulk-restrict-remote"
                      checked={restrict}
                      onCheckedChange={(c: boolean) => setRestrict(c)}
                    />
                  </div>
                  <textarea
                    className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
                    rows={4}
                    placeholder="10.1.2.1&#10;10.1.2.2"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                  />
                </div>
              ) : (
                <Input
                  className="mt-1.5 h-9 font-normal"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder={`ej. ${field === "address" ? "10.1.2.99" : "valor nuevo"}`}
                />
              )}
            </label>
            {field === "platformId" && (
              <Alert variant="warning" className="mt-2">
                Cambiar la plataforma no valida ni completa las propiedades que la plataforma nueva
                requiere — revisar cada cuenta después del cambio, o el CPM puede empezar a fallar
                su gestión.
              </Alert>
            )}
          </div>
          <div className="mt-auto flex justify-end gap-2 border-t border-border bg-muted/20 p-4">
            <Button variant="outline" onClick={onClose}>
              Cancelar
            </Button>
            <Button
              disabled={!value.trim() && field !== "remoteMachines"}
              onClick={() => {
                if (field === "automaticManagementEnabled") {
                  setDialog({ automaticManagementEnabled: value === "true" });
                } else if (field === "remoteMachines") {
                  const newRm = value
                    .split("\n")
                    .map((s) => s.trim())
                    .filter(Boolean)
                    .join(";");
                  setDialog({
                    remote_machines: newRm,
                    access_restricted_to_remote_machines: restrict,
                  });
                } else {
                  setDialog({ [field]: value.trim() });
                }
              }}
            >
              Previsualizar
            </Button>
          </div>
        </SheetContent>
      </Sheet>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/accounts/bulk-update`}
        changes={dialog ?? {}}
        extraBody={{ account_ids: accountIds }}
        title={`Editar ${FIELD_LABEL[field]} en ${accountIds.length} cuenta(s)`}
        onDone={() => {
          setDialog(null);
          setValue("");
          onDone();
        }}
      />
    </>
  );
}
