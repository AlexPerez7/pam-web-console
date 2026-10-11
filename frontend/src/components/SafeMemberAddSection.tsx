import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search, UserPlus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { invalidateSafe } from "@/lib/invalidate";
import {
  ADMIN_PERMISSION_KEYS,
  matchPreset,
  PERMISSION_GROUPS,
  PERMISSION_LABELS,
  PRESET_OPTIONS,
  PRESETS,
} from "@/lib/permissions";
import { roleAtLeast } from "@/lib/roles";
import type { MemberSearchResponse, MemberSearchResult, TenantSummary } from "@/lib/types";

type Obj = Record<string, unknown>;
const KIND_LABEL = (k: string) => (k === "group" ? "Grupo" : "Usuario");

/** "Agregar miembro" en el detalle de una safe. Sólo si el tenant tiene
 *  `safe.member.add` habilitada y el operador es `operator+`. */
export function SafeMemberAddSection({ tenant, safeId }: { tenant: string; safeId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [manual, setManual] = useState(false);
  const [picked, setPicked] = useState<MemberSearchResult | null>(null);
  const [manualName, setManualName] = useState("");
  const [manualType, setManualType] = useState("User");
  const [manualSearchIn, setManualSearchIn] = useState("Vault");
  const [perms, setPerms] = useState<Record<string, boolean>>(PRESETS.read_only);
  const [expiration, setExpiration] = useState("");
  const [dialog, setDialog] = useState<Obj | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  useEffect(() => {
    const id = setTimeout(() => setDebounced(term.trim()), 300);
    return () => clearTimeout(id);
  }, [term]);

  const search = useQuery({
    queryKey: ["member-search", tenant, safeId, debounced],
    queryFn: () =>
      api<MemberSearchResponse>(
        `/tenants/${tenant}/member-search?q=${encodeURIComponent(debounced)}&safe_id=${encodeURIComponent(safeId)}`,
      ),
    enabled: open && !manual && debounced.length >= 2,
  });

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  const preset = matchPreset(perms);
  const name = manual ? manualName.trim() : (picked?.name ?? "");
  const memberType = manual ? manualType : picked?.kind === "group" ? "Group" : "User";
  const searchIn = manual ? manualSearchIn.trim() || "Vault" : (picked?.searchIn ?? "Vault");
  const grantsAdmin = ADMIN_PERMISSION_KEYS.some((k) => perms[k]);
  const anyPerm = Object.values(perms).some(Boolean);

  function reset() {
    setOpen(false);
    setTerm("");
    setDebounced("");
    setManual(false);
    setPicked(null);
    setManualName("");
    setManualType("User");
    setManualSearchIn("Vault");
    setPerms(PRESETS.read_only);
    setExpiration("");
  }

  function preview() {
    const changes: Obj = {
      member_name: name,
      member_type: memberType,
      search_in: searchIn,
      preset,
      permissions: perms,
    };
    if (expiration) {
      const epoch = Math.floor(new Date(expiration).getTime() / 1000);
      if (!Number.isNaN(epoch)) changes.membership_expiration_date = epoch;
    }
    setDialog(changes);
  }

  if (!open) {
    return (
      <div className="pt-3">
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          <UserPlus className="size-3.5" />
          Agregar miembro
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-border bg-muted/30 p-3">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm font-semibold">Agregar miembro</p>
        <button
          onClick={reset}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Cerrar agregar miembro"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="flex flex-col gap-4">
        {/* 1. principal */}
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Principal</p>
          {picked ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm">
              <span className="min-w-0 truncate">
                <span className="font-medium">{picked.name}</span>
                {picked.displayName ? (
                  <span className="text-muted-foreground"> · {picked.displayName}</span>
                ) : null}
                <span className="text-muted-foreground">
                  {" "}
                  · {KIND_LABEL(picked.kind)} · {picked.source}
                </span>
              </span>
              <button
                onClick={() => setPicked(null)}
                className="shrink-0 text-xs text-sky-600 hover:underline"
              >
                cambiar
              </button>
            </div>
          ) : manual ? (
            <div className="flex flex-col gap-2">
              <Input
                autoFocus
                className="h-9 text-sm"
                placeholder="Nombre exacto del usuario / grupo"
                value={manualName}
                onChange={(e) => setManualName(e.target.value)}
              />
              <div className="flex gap-2">
                <select
                  className="h-9 flex-1 rounded-md border border-input bg-card px-2 text-sm"
                  value={manualType}
                  onChange={(e) => setManualType(e.target.value)}
                >
                  <option value="User">Usuario</option>
                  <option value="Group">Grupo</option>
                  <option value="Role">Rol</option>
                </select>
                <Input
                  className="h-9 flex-1 text-sm"
                  placeholder="Buscar en (Vault / dominio)"
                  value={manualSearchIn}
                  onChange={(e) => setManualSearchIn(e.target.value)}
                />
              </div>
              <button
                onClick={() => setManual(false)}
                className="self-start text-xs text-sky-600 hover:underline"
              >
                ← usar el buscador
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  autoFocus
                  className="h-9 pl-8 text-sm"
                  placeholder="Buscar usuario o grupo…"
                  value={term}
                  onChange={(e) => setTerm(e.target.value)}
                />
              </div>
              {search.isFetching && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" /> buscando…
                </span>
              )}
              {search.data && search.data.results.length === 0 && debounced.length >= 2 && (
                <span className="text-xs text-muted-foreground">Sin resultados.</span>
              )}
              {!!search.data?.results.length && (
                <ul className="max-h-48 divide-y divide-border overflow-y-auto rounded-md border border-border bg-card">
                  {search.data.results.map((r) => (
                    <li key={`${r.kind}:${r.name}`}>
                      <button
                        disabled={r.alreadyMember}
                        onClick={() => {
                          setPicked(r);
                          setManualSearchIn(r.searchIn);
                        }}
                        className="flex w-full flex-col items-start gap-0.5 px-2.5 py-1.5 text-left text-sm hover:bg-muted disabled:opacity-45 disabled:hover:bg-transparent"
                      >
                        <span className="flex w-full items-center justify-between gap-2">
                          <span className="font-medium">{r.name}</span>
                          <span className="shrink-0 text-[11px] text-muted-foreground">
                            {r.alreadyMember ? "ya es miembro" : KIND_LABEL(r.kind)}
                          </span>
                        </span>
                        <span className="text-[11px] text-muted-foreground">
                          {r.displayName ? `${r.displayName} · ` : ""}
                          {r.source}
                          {r.email ? ` · ${r.email}` : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <button
                onClick={() => setManual(true)}
                className="self-start text-xs text-sky-600 hover:underline"
              >
                …o ingresar el nombre a mano
              </button>
            </div>
          )}
        </div>

        {/* 2. permisos — estilo PVWA */}
        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <p className="text-xs font-medium text-muted-foreground">Permisos</p>
            <span className="text-xs text-muted-foreground">
              perfil: <span className="font-medium text-foreground">{PRESET_OPTIONS.find((o) => o.value === preset)?.label}</span>
            </span>
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
        </div>

        {/* 3. vencimiento */}
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Vence
          <Input
            type="date"
            className="h-8 w-auto text-sm"
            value={expiration}
            onChange={(e) => setExpiration(e.target.value)}
          />
          <span>(opcional)</span>
        </label>

        <div className="flex gap-2">
          <Button size="sm" onClick={preview} disabled={!name || !anyPerm}>
            Previsualizar
          </Button>
          <Button variant="ghost" size="sm" onClick={reset}>
            Cancelar
          </Button>
        </div>
      </div>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/safes/${encodeURIComponent(safeId)}/members`}
        changes={dialog ?? {}}
        title="Agregar miembro a la safe"
        onDone={() => {
          reset();
          invalidateSafe(qc, tenant, safeId);
        }}
      />
    </div>
  );
}
