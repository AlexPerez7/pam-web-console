import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, Shield, User, UserPlus, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { IdentityRoleMemberAdd } from "@/components/IdentityRoleMemberAdd";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { IdentityRoleMembersResponse, IdentityRoleRow, TenantSummary } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = { User: "Usuario", Group: "Grupo", Role: "Rol" };

function TypeIcon({ type }: { type?: string | null }) {
  const cls = "size-4 shrink-0 text-muted-foreground";
  if (type === "Group") return <Users className={cls} aria-hidden="true" />;
  if (type === "Role") return <Shield className={cls} aria-hidden="true" />;
  return <User className={cls} aria-hidden="true" />;
}

/**
 * Detalle de un rol de Identity: descripción + miembros (usuarios, grupos y
 * roles), como la pestaña "Members" del rol en Identity Administration.
 * Los miembros se piden recién al abrir el panel (una llamada a CyberArk
 * por rol, no una por fila de la grilla). Con `operator+`, "Agregar
 * miembros" (`IdentityRoleMemberAdd`).
 */
export function IdentityRoleDetailPanel({
  tenant,
  role,
  typeLabel,
  onClose,
}: {
  tenant: string;
  role: IdentityRoleRow | null;
  typeLabel: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("");
  const [adding, setAdding] = useState(false);
  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const canAdd = roleAtLeast(t.data?.my_role, "operator");
  const roleId = role?.id != null ? String(role.id) : "";

  const q = useQuery({
    queryKey: ["identity-role-members", tenant, roleId],
    queryFn: () =>
      api<IdentityRoleMembersResponse>(
        `/tenants/${tenant}/identity-roles/${encodeURIComponent(roleId)}/members`,
      ),
    enabled: !!roleId,
    staleTime: 60_000,
  });

  const members = useMemo(() => {
    const all = q.data?.members ?? [];
    const f = filter.trim().toLowerCase();
    return f ? all.filter((m) => (m.name ?? "").toLowerCase().includes(f)) : all;
  }, [q.data, filter]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const m of q.data?.members ?? []) c[m.type ?? "User"] = (c[m.type ?? "User"] ?? 0) + 1;
    return c;
  }, [q.data]);

  return (
    <Sheet
      open={!!role}
      onOpenChange={(o) => {
        if (!o) {
          setFilter("");
          setAdding(false);
          onClose();
        }
      }}
    >
      <SheetContent className="max-w-2xl">
        {role && (
          <>
            <div className="pr-6">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Rol de Identity
              </p>
              <SheetTitle className="mt-0.5 text-lg">{role.name ?? "—"}</SheetTitle>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Badge variant="info">{typeLabel}</Badge>
                {role.organization && <Badge>{role.organization}</Badge>}
              </div>
              {role.description && (
                <p className="mt-3 text-sm text-muted-foreground">{role.description}</p>
              )}
            </div>

            <section className="flex min-h-0 flex-col gap-3 border-t border-border pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-navy-900">
                  Miembros
                  {q.data && (
                    <span className="ml-1.5 font-normal text-muted-foreground">
                      ({q.data.count.toLocaleString("es")})
                    </span>
                  )}
                </h3>
                {canAdd && !adding && roleId && (
                  <Button size="sm" onClick={() => setAdding(true)}>
                    <UserPlus className="size-4" />
                    Agregar miembros
                  </Button>
                )}
              </div>
              {q.data && q.data.count > 0 && (
                <p className="-mt-1 text-xs text-muted-foreground">
                  {Object.entries(counts)
                    .map(
                      ([k, n]) =>
                        `${n} ${(TYPE_LABELS[k] ?? k).toLowerCase()}${n === 1 ? "" : "s"}`,
                    )
                    .join(" · ")}
                </p>
              )}

              {adding && (
                <IdentityRoleMemberAdd
                  tenant={tenant}
                  roleId={roleId}
                  roleName={role.name ?? roleId}
                  onCancel={() => setAdding(false)}
                  onDone={() => {
                    setAdding(false);
                    qc.invalidateQueries({ queryKey: ["identity-role-members", tenant, roleId] });
                    qc.invalidateQueries({
                      queryKey: ["identity-role-candidates", tenant, roleId],
                    });
                  }}
                />
              )}

              {role.roleType === "Script" && (
                <p className="text-xs text-muted-foreground">
                  Rol dinámico: su membresía la calcula un script en Identity, por lo que la lista
                  puede venir incompleta o vacía.
                </p>
              )}

              {q.isLoading && (
                <div className="flex flex-col gap-2">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-9 w-full" />
                  ))}
                </div>
              )}
              {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

              {q.data && q.data.count === 0 && (
                <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                  Este rol no tiene miembros.
                </p>
              )}

              {q.data && q.data.count > 0 && (
                <>
                  {q.data.count > 8 && (
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder="Filtrar miembros"
                        aria-label="Filtrar miembros"
                        className="h-9 pl-8"
                      />
                    </div>
                  )}
                  <div className="overflow-hidden rounded-md border border-border">
                    <div className="grid grid-cols-[minmax(0,1fr)_7rem] border-b border-border bg-muted/40 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      <span>Login name</span>
                      <span>Tipo</span>
                    </div>
                    <ul>
                      {members.map((m, i) => (
                        <li
                          key={m.id ?? `${m.name}-${i}`}
                          className="grid grid-cols-[minmax(0,1fr)_7rem] items-center border-b border-border px-3 py-2 text-sm last:border-b-0"
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            <TypeIcon type={m.type} />
                            <span className="truncate" title={m.name ?? undefined}>
                              {m.name ?? "—"}
                            </span>
                          </span>
                          <span className="text-muted-foreground">
                            {TYPE_LABELS[m.type ?? ""] ?? m.type ?? "—"}
                          </span>
                        </li>
                      ))}
                      {members.length === 0 && (
                        <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                          Ningún miembro coincide.
                        </li>
                      )}
                    </ul>
                  </div>
                </>
              )}
            </section>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
