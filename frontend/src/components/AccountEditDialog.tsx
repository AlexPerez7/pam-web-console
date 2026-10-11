import { useQueryClient, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { PlatformField } from "@/components/PlatformField";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { invalidateAccount } from "@/lib/invalidate";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { SafeField } from "@/components/SafeField";
import type { PlatformsResponse } from "@/lib/types";

const FIELDS = [
  { key: "name", label: "Nombre" },
  { key: "address", label: "Address" },
  { key: "userName", label: "Usuario" },
] as const;

const PLATFORM_FIELD = "platformId";

type Obj = Record<string, unknown>;

/** Diálogo "Editar metadata" — versión controlada (abierta desde el botón
 *  "Editar" del panel de detalle). La lógica es la misma que antes tenía
 *  `AccountEditSection`; sólo cambió el envoltorio (ahora Sheet en vez de
 *  Dialog) y quién decide si mostrarlo (el panel, no este componente). */
export function AccountEditDialog({
  open,
  onClose,
  tenant,
  accountId,
  account,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  tenant: string;
  accountId: string;
  account: Obj;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const { operator } = useAuth();
  const isPlatformAdmin = operator?.role === "platform_admin";
  const [form, setForm] = useState<Record<string, string>>({});
  const [dialog, setDialog] = useState<Obj | null>(null);
  const [actionType, setActionType] = useState<"update" | "move">("update");

  const platforms = useQuery({
    queryKey: ["inv", tenant, "platforms"],
    queryFn: () => api<PlatformsResponse>(`/tenants/${tenant}/platforms`),
    staleTime: 5 * 60_000,
  });

  const originalPlatformId = String(account[PLATFORM_FIELD] ?? "");
  const originalSystemType = platforms.data?.platforms?.find(
    (p) => p.general?.id === originalPlatformId
  )?.general?.systemType;

  const originalSafeName = String(account.safeName ?? "");
  // Mover de Safe es una operación aparte (clona + borra, ver account.move
  // en el backend) — no se puede combinar con una edición de metadata
  // normal en el mismo request sin que uno de los dos cambios se pierda.
  // Mientras el Safe elegido difiera del original, el resto del form queda
  // deshabilitado.
  const safeChanged = isPlatformAdmin && (form.safeName ?? "") !== "" && form.safeName !== originalSafeName;

  useEffect(() => {
    if (open) {
      const sm = account.secretManagement as Record<string, unknown> | undefined;
      const rma = account.remoteMachinesAccess as Record<string, unknown> | undefined;
      const rmList = String(rma?.remoteMachines ?? "")
        .split(";")
        .map((s) => s.trim())
        .filter(Boolean)
        .join("\n");
      setForm({
        ...Object.fromEntries(FIELDS.map((f) => [f.key, String(account[f.key] ?? "")])),
        [PLATFORM_FIELD]: String(account[PLATFORM_FIELD] ?? ""),
        safeName: String(account.safeName ?? ""),
        automaticManagementEnabled: sm?.automaticManagementEnabled === false ? "false" : "true",
        remote_machines: rmList,
        access_restricted_to_remote_machines: rma?.accessRestrictedToRemoteMachines ? "true" : "false",
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function preview() {
    // Mover de Safe es una operación completamente aparte (clona + borra) —
    // si el Safe cambió, el resto del form ya está deshabilitado en el
    // render, así que acá se manda SOLO el cambio de Safe. Nunca se
    // combinan los dos endpoints en un mismo request.
    if (safeChanged) {
      setActionType("move");
      setDialog({ safe_name: form.safeName });
      return;
    }

    setActionType("update");
    const changes: Obj = {};
    for (const f of FIELDS) {
      const v = form[f.key] ?? "";
      if (v !== String(account[f.key] ?? "")) changes[f.key] = v;
    }
    const p = form[PLATFORM_FIELD] ?? "";
    if (p && p !== String(account[PLATFORM_FIELD] ?? "")) changes[PLATFORM_FIELD] = p;

    const sm = account.secretManagement as Record<string, unknown> | undefined;
    const oldAm = sm?.automaticManagementEnabled === false ? "false" : "true";
    if (form.automaticManagementEnabled !== oldAm) {
      changes.automaticManagementEnabled = form.automaticManagementEnabled === "true";
    }

    const rma = account.remoteMachinesAccess as Record<string, unknown> | undefined;
    const oldRm = String(rma?.remoteMachines ?? "")
      .split(";")
      .map((s) => s.trim())
      .filter(Boolean)
      .join(";");
    const newRm = (form.remote_machines ?? "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean)
      .join(";");
    if (newRm !== oldRm) {
      changes.remote_machines = newRm;
    }
    const oldRestricted = rma?.accessRestrictedToRemoteMachines ? "true" : "false";
    if ((form.access_restricted_to_remote_machines ?? "false") !== oldRestricted) {
      changes.access_restricted_to_remote_machines = form.access_restricted_to_remote_machines === "true";
    }

    setDialog(changes);
  }

  function close() {
    setForm({});
    setDialog(null);
    onClose();
  }

  return (
    <>
      <Sheet open={open && dialog === null} onOpenChange={(o) => !o && close()}>
        <SheetContent className="flex w-full max-w-3xl flex-col gap-0 p-0 sm:max-w-[calc(100vw-16rem)]">
          <SheetTitle className="border-b border-border px-6 py-4 text-lg">
            {safeChanged ? "Mover cuenta de Safe" : "Editar metadata"}
          </SheetTitle>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
            {isPlatformAdmin && (
              <label className="text-sm font-medium text-muted-foreground">
                Safe (platform_admin)
                <div className="mt-1.5 font-normal">
                  <SafeField
                    tenant={tenant}
                    value={form.safeName ?? ""}
                    onChange={(v) => setForm((s) => ({ ...s, safeName: v }))}
                  />
                </div>
              </label>
            )}
            {safeChanged && (
              <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                Cambiar el Safe es una operación aparte — crea una copia de la cuenta en el
                Safe nuevo y borra la original, no una edición de metadata. Por eso el resto
                del formulario queda deshabilitado: para editar esos otros campos, dejá el
                Safe como estaba y hacelo en una pasada separada.
              </p>
            )}
            <div
              className={cn(
                "flex flex-col gap-4",
                safeChanged && "pointer-events-none opacity-40",
              )}
            >
              {FIELDS.map((f) => (
                <label key={f.key} className="text-sm font-medium text-muted-foreground">
                  {f.label}
                  <Input
                    className="mt-1.5 font-normal"
                    value={form[f.key] ?? ""}
                    onChange={(e) => setForm((s) => ({ ...s, [f.key]: e.target.value }))}
                    disabled={safeChanged}
                  />
                </label>
              ))}
              <label className="text-sm font-medium text-muted-foreground">
                Plataforma
                <div className="mt-1.5 font-normal">
                  <PlatformField
                    tenant={tenant}
                    value={form[PLATFORM_FIELD] ?? ""}
                    onChange={(v) => setForm((s) => ({ ...s, [PLATFORM_FIELD]: v }))}
                    systemType={originalSystemType}
                  />
                </div>
              </label>
              <label className="text-sm font-medium text-muted-foreground">
                Gestión automática (CPM)
                <Select
                  value={form.automaticManagementEnabled}
                  onValueChange={(v) => setForm((s) => ({ ...s, automaticManagementEnabled: v }))}
                  disabled={safeChanged}
                >
                  <SelectTrigger aria-label="Gestión automática de la contraseña" className="mt-1.5 h-8 w-full text-sm font-normal">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="true">Sí (Activa)</SelectItem>
                    <SelectItem value="false">No (Inactiva)</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <div className="mt-2 flex flex-col gap-3 border-t border-border pt-4">
                <span className="text-sm font-medium text-muted-foreground">
                  Remote machine access (opcional)
                </span>
                <div className="flex items-center justify-between gap-2 rounded-md border border-border p-3">
                  <label htmlFor="edit-restrict-remote" className="cursor-pointer text-sm font-medium text-muted-foreground">
                    Limit access to these machines only
                  </label>
                  <Switch
                    id="edit-restrict-remote"
                    checked={form.access_restricted_to_remote_machines === "true"}
                    onCheckedChange={(c: boolean) =>
                      setForm((s) => ({ ...s, access_restricted_to_remote_machines: c ? "true" : "false" }))
                    }
                    disabled={safeChanged}
                  />
                </div>
                <textarea
                  className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm font-normal"
                  rows={4}
                  placeholder="10.1.2.1&#10;10.1.2.2"
                  value={form.remote_machines ?? ""}
                  onChange={(e) => setForm((s) => ({ ...s, remote_machines: e.target.value }))}
                  disabled={safeChanged}
                />
              </div>
            </div>
          </div>
          <div className="mt-auto flex justify-end gap-2 border-t border-border bg-muted/20 p-4">
            <Button variant="ghost" onClick={close}>
              Cancelar
            </Button>
            <Button onClick={preview}>Previsualizar</Button>
          </div>
        </SheetContent>
      </Sheet>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/accounts/${encodeURIComponent(accountId)}/${actionType}`}
        changes={dialog ?? {}}
        title={actionType === "move" ? "Mover cuenta de Safe" : "Editar metadata de la cuenta"}
        onDone={() => {
          close();
          invalidateAccount(qc, tenant, accountId);
          onDone();
        }}
      />
    </>
  );
}
