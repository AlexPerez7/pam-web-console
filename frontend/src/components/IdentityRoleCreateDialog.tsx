import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";

type Obj = Record<string, unknown>;

/** Botón + diálogo "Agregar rol" del toolbar de Identity > Roles — espeja el
 *  "Add Role" de Identity Administration: nombre, descripción, organización
 *  (sólo lectura) y tipo. Crea un rol **estático y vacío**: los miembros se
 *  agregan después desde el panel del rol. El tipo dinámico (`Script`) queda
 *  fuera de alcance. Sólo para `operator+`. Ver
 *  `docs/specs/done/identity-role-create.md`. */
export function IdentityRoleCreateDialog({ tenant }: { tenant: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dialog, setDialog] = useState<Obj | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  function reset() {
    setOpen(false);
    setName("");
    setDescription("");
  }

  function preview() {
    const changes: Obj = { name: name.trim() };
    if (description.trim()) changes.description = description.trim();
    setDialog(changes);
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Plus className="size-3.5" />
        Agregar rol
      </Button>

      <Dialog open={open} onOpenChange={(o) => !o && reset()}>
        <DialogContent className="max-w-md">
          <DialogTitle>Agregar rol</DialogTitle>
          <div className="flex flex-col gap-3">
            <label className="text-xs text-muted-foreground">
              Nombre *
              <Input
                className="mt-1 h-8 text-sm"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Descripción
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
              />
            </label>
            <div className="text-xs text-muted-foreground">
              Organización
              <p className="mt-1 text-sm text-foreground">—</p>
            </div>
            <label className="text-xs text-muted-foreground">
              Tipo de rol
              <Select value="static" disabled>
                <SelectTrigger className="mt-1 h-8 text-sm" aria-label="Tipo de rol">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="static">Static</SelectItem>
                  <SelectItem value="dynamic" disabled>
                    Dynamic
                  </SelectItem>
                </SelectContent>
              </Select>
            </label>
            <p className="text-xs text-muted-foreground">
              El rol se crea vacío y sin derechos administrativos. Agregue miembros después, desde
              el detalle del rol.
            </p>

            <Button size="sm" className="self-start" disabled={!name.trim()} onClick={preview}>
              Previsualizar
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/identity-roles`}
        changes={dialog ?? {}}
        title="Agregar rol de Identity"
        onDone={() => {
          reset();
          setDialog(null);
          qc.invalidateQueries({ queryKey: ["identity-roles", tenant] });
        }}
      />
    </>
  );
}
