import { useQuery } from "@tanstack/react-query";
import { Check, Loader2, Plus, Search, Shield, User, X } from "lucide-react";
import { useEffect, useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { IdentityRoleCandidate, IdentityRoleCandidatesResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const MIN_CHARS = 2;

/**
 * "Agregar miembros" dentro del detalle de un rol de Identity: busca
 * usuarios y roles de Identity, se eligen uno o varios y se agregan de una
 * vez con el flujo habitual (previsualizar → razón → confirmar). Sólo suma
 * miembros, nunca quita. Gateado por `operator+` en el panel que lo monta.
 */
export function IdentityRoleMemberAdd({
  tenant,
  roleId,
  roleName,
  onCancel,
  onDone,
}: {
  tenant: string;
  roleId: string;
  roleName: string;
  onCancel: () => void;
  onDone: () => void;
}) {
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [selected, setSelected] = useState<IdentityRoleCandidate[]>([]);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(term.trim()), 300);
    return () => clearTimeout(id);
  }, [term]);

  const enabled = debounced.length >= MIN_CHARS;
  const q = useQuery({
    queryKey: ["identity-role-candidates", tenant, roleId, debounced],
    queryFn: () =>
      api<IdentityRoleCandidatesResponse>(
        `/tenants/${tenant}/identity-roles/${encodeURIComponent(roleId)}/candidates?q=${encodeURIComponent(debounced)}`,
      ),
    enabled,
    staleTime: 30_000,
  });

  const isSelected = (c: IdentityRoleCandidate) =>
    selected.some((s) => s.id === c.id && s.type === c.type);

  function toggle(c: IdentityRoleCandidate) {
    setSelected((cur) =>
      isSelected(c) ? cur.filter((s) => !(s.id === c.id && s.type === c.type)) : [...cur, c],
    );
  }

  const results = q.data?.results ?? [];

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-navy-900">Agregar miembros</h4>
        <Button
          variant="ghost"
          size="icon"
          onClick={onCancel}
          aria-label="Cancelar agregar miembros"
        >
          <X className="size-4" />
        </Button>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          autoFocus
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          placeholder="Buscar usuario o rol (nombre, login o correo)"
          aria-label="Buscar usuario o rol de Identity"
          className="h-9 bg-card pl-8"
        />
      </div>

      <div className="max-h-64 overflow-y-auto rounded-md border border-border bg-card">
        {!enabled ? (
          <p className="px-3 py-6 text-center text-sm text-muted-foreground">
            Escriba al menos {MIN_CHARS} letras.
          </p>
        ) : q.isFetching && !q.data ? (
          <p className="flex justify-center px-3 py-6">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-label="Buscando" />
          </p>
        ) : q.isError ? (
          <p className="px-3 py-6 text-center text-sm text-danger-ink">
            {(q.error as Error).message}
          </p>
        ) : results.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-muted-foreground">Sin resultados.</p>
        ) : (
          <ul>
            {results.map((c) => {
              const sel = isSelected(c);
              return (
                <li key={`${c.type}:${c.id}`} className="border-b border-border last:border-b-0">
                  <button
                    type="button"
                    disabled={c.alreadyMember}
                    onClick={() => toggle(c)}
                    aria-pressed={sel}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors",
                      c.alreadyMember ? "cursor-not-allowed opacity-60" : "hover:bg-muted/50",
                      sel && "bg-accent",
                    )}
                  >
                    {c.type === "Role" ? (
                      <Shield
                        className="size-4 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                    ) : (
                      <User className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    )}
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate">{c.name}</span>
                      {(c.displayName || c.email) && (
                        <span className="truncate text-xs text-muted-foreground">
                          {[c.displayName, c.email].filter(Boolean).join(" · ")}
                        </span>
                      )}
                    </span>
                    {c.alreadyMember ? (
                      <span className="text-xs text-muted-foreground">Ya es miembro</span>
                    ) : sel ? (
                      <Check className="size-4 shrink-0 text-success-ink" aria-hidden="true" />
                    ) : (
                      <Plus className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((s) => (
            <Badge key={`${s.type}:${s.id}`} variant="info" className="gap-1">
              {s.type === "Role" ? "Rol: " : ""}
              {s.name}
              <button
                type="button"
                onClick={() => toggle(s)}
                aria-label={`Quitar ${s.name} de la selección`}
                className="ml-0.5 rounded-sm opacity-70 hover:opacity-100"
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancelar
        </Button>
        <Button size="sm" disabled={selected.length === 0} onClick={() => setConfirming(true)}>
          Agregar {selected.length > 0 ? `${selected.length} ` : ""}
          {selected.length === 1 ? "miembro" : "miembros"}
        </Button>
      </div>

      <DryRunDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        path={`/tenants/${tenant}/identity-roles/${encodeURIComponent(roleId)}/members`}
        changes={{
          members: selected.map((s) => ({ id: s.id, name: s.name, type: s.type })),
        }}
        title={`Agregar miembros a ${roleName}`}
        onDone={() => {
          setConfirming(false);
          setSelected([]);
          setTerm("");
          onDone();
        }}
      />
    </div>
  );
}
