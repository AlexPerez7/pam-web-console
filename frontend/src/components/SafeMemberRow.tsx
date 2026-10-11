import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { invalidateSafe } from "@/lib/invalidate";
import {
  ADMIN_PERMISSION_KEYS,
  ALL_PERMISSION_KEYS,
  matchPreset,
  PERMISSION_GROUPS,
  PERMISSION_LABELS,
  PRESET_LABELS,
  PRESET_OPTIONS,
  PRESETS,
} from "@/lib/permissions";
import { roleAtLeast } from "@/lib/roles";
import type { SafeMember, TenantSummary } from "@/lib/types";

type Obj = Record<string, unknown>;

function toDateInput(epoch?: number | null): string {
  return epoch ? new Date(epoch * 1000).toISOString().slice(0, 10) : "";
}

/** Una fila de la lista "Miembros" del detalle de una safe. Sólo lectura por
 *  defecto; agrega "Editar" / "Quitar" (Fase 3, `safe.member.update` /
 *  `safe.member.remove`) si el tenant las tiene habilitadas y el operador es
 *  `approver+`. Ver `docs/specs/safe-member-edit.md`. */
export function SafeMemberRow({
  tenant,
  safeId,
  member,
}: {
  tenant: string;
  safeId: string;
  member: SafeMember;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"view" | "edit" | "remove">("view");
  const currentPerms = (member.permissions ?? {}) as Record<string, boolean>;
  const [perms, setPerms] = useState<Record<string, boolean>>(currentPerms);
  const [expiration, setExpiration] = useState(toDateInput(member.membershipExpirationDate));
  const [dialog, setDialog] = useState<Obj | null>(null);
  const [dialogMethod, setDialogMethod] = useState<"PUT" | "DELETE">("PUT");

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const name = member.memberName ?? "—";
  const path = `/tenants/${tenant}/safes/${encodeURIComponent(safeId)}/members/${encodeURIComponent(name)}`;
  const granted = ALL_PERMISSION_KEYS.filter((k) => currentPerms[k]);
  const roleLabel = PRESET_LABELS[matchPreset(currentPerms)] ?? "Custom";

  const canUpdate = roleAtLeast(t.data?.my_role, "approver");
  const canRemove = roleAtLeast(t.data?.my_role, "approver");

  const preset = matchPreset(perms);
  const grantsAdmin = ADMIN_PERMISSION_KEYS.some((k) => perms[k]);
  const anyPerm = Object.values(perms).some(Boolean);

  function resetEdit() {
    setMode("view");
    setPerms(currentPerms);
    setExpiration(toDateInput(member.membershipExpirationDate));
  }

  function previewUpdate() {
    const changes: Obj = { preset, permissions: perms };
    if (expiration) {
      const epoch = Math.floor(new Date(expiration).getTime() / 1000);
      if (!Number.isNaN(epoch)) changes.membership_expiration_date = epoch;
    }
    setDialogMethod("PUT");
    setDialog(changes);
  }

  return (
    <li className="flex flex-col gap-1 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-sm font-medium">
          {name}
          <span className="ml-1.5 text-xs font-normal text-muted-foreground">
            {member.memberType ?? ""}
          </span>
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
            {roleLabel}
          </span>
          {mode === "view" && canUpdate && (
            <button
              onClick={() => setMode("edit")}
              className="text-muted-foreground hover:text-foreground"
              title="Editar permisos"
            >
              <Pencil className="size-3.5" />
            </button>
          )}
          {mode === "view" && canRemove && (
            <button
              onClick={() => setMode("remove")}
              className="text-muted-foreground hover:text-destructive"
              title="Quitar de la safe"
            >
              <Trash2 className="size-3.5" />
            </button>
          )}
        </div>
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {granted.length ? granted.map((k) => PERMISSION_LABELS[k] ?? k).join(" · ") : "sin permisos"}
        {member.membershipExpirationDate ? ` — vence ${fmtDate(member.membershipExpirationDate)}` : ""}
      </p>

      {mode === "edit" && (
        <div className="mt-2 rounded-lg border border-border bg-muted/30 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-semibold">Editar permisos</p>
            <button
              onClick={resetEdit}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Cerrar edición de permisos"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {PRESET_OPTIONS.filter((o) => o.value !== "custom").map((o) => (
              <button
                key={o.value}
                title={o.hint}
                onClick={() => setPerms(PRESETS[o.value])}
                className={
                  "rounded-full border px-2.5 py-0.5 text-xs transition-colors " +
                  (preset === o.value
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:bg-muted")
                }
              >
                {o.label}
              </button>
            ))}
          </div>
          <div className="grid gap-x-4 gap-y-3 rounded-md border border-border bg-card p-2.5 sm:grid-cols-2">
            {PERMISSION_GROUPS.map((g) => (
              <div key={g.title}>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {g.title}
                </p>
                <div className="flex flex-col gap-1">
                  {g.keys.map((k) => (
                    <label key={k} className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        className="size-3.5 accent-primary"
                        checked={!!perms[k]}
                        onChange={() => setPerms((s) => ({ ...s, [k]: !s[k] }))}
                      />
                      {PERMISSION_LABELS[k] ?? k}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {grantsAdmin && (
            <Alert variant="warning" className="mt-2">
              Incluye "Gestionar la safe" / "Gestionar miembros": el miembro podrá cambiar la
              configuración y los accesos de la safe.
            </Alert>
          )}
          <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
            Vence
            <input
              type="date"
              className="h-8 rounded-md border border-input bg-card px-2 text-sm"
              value={expiration}
              onChange={(e) => setExpiration(e.target.value)}
            />
            <span>(opcional)</span>
          </label>
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={previewUpdate} disabled={!anyPerm}>
              Previsualizar
            </Button>
            <Button variant="ghost" size="sm" onClick={resetEdit}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      {mode === "remove" && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
          <p className="text-sm">
            ¿Quitar a <span className="font-medium">{name}</span> de esta safe?
          </p>
          <div className="flex gap-2">
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                setDialogMethod("DELETE");
                setDialog({});
              }}
            >
              Continuar
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setMode("view")}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={path}
        method={dialogMethod}
        changes={dialog ?? {}}
        title={dialogMethod === "DELETE" ? "Quitar miembro de la safe" : "Editar permisos del miembro"}
        onDone={() => {
          setDialog(null);
          setMode("view");
          invalidateSafe(qc, tenant, safeId);
        }}
      />
    </li>
  );
}
