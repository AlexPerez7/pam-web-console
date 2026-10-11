import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Unlock } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";

/** "Bloquear/Desbloquear usuario" en el detalle de un usuario de
 *  Identity/SSO. Dos endpoints (`/lock`, `/unlock`), mismo criterio que
 *  `PlatformActivateSection`. Gateado por `identity_user.lock` +
 *  `operator+`. Ver `docs/specs/identity-users.md`. */
export function IdentityUserLockSection({
  tenant,
  userId,
  locked,
}: {
  tenant: string;
  userId: string;
  locked: boolean | undefined;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  const isLocked = !!locked;
  const verb = isLocked ? "Desbloquear" : "Bloquear";
  const path = `/tenants/${tenant}/identity-users/${encodeURIComponent(userId)}/${
    isLocked ? "unlock" : "lock"
  }`;

  return (
    <section className="border-t border-border pt-4">
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        {isLocked ? <Unlock className="size-3.5" /> : <Lock className="size-3.5" />}
        {verb} usuario
      </Button>

      <DryRunDialog
        open={open}
        onClose={() => setOpen(false)}
        path={path}
        changes={{}}
        title={`${verb} usuario de Identity`}
        onDone={() => {
          setOpen(false);
          qc.invalidateQueries({ queryKey: ["identity-user-detail", tenant, userId] });
          qc.invalidateQueries({ queryKey: ["identity-users", tenant] });
        }}
      />
    </section>
  );
}
