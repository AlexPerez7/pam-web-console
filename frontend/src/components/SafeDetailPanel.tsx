import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";
import { SafeDeleteSection } from "@/components/SafeDeleteSection";
import { SafeEditSection } from "@/components/SafeEditSection";
import { SafeMemberAddSection } from "@/components/SafeMemberAddSection";
import { SafeMemberRow } from "@/components/SafeMemberRow";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { fmtDate, fmtDateTime, relTime } from "@/lib/format";
import type { Safe, SafeMembersResponse } from "@/lib/types";

type Tab = "detalles" | "miembros";
type Obj = Record<string, unknown>;

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </label>
      <div className="break-words text-sm">{value}</div>
    </div>
  );
}

/** Panel de detalle de una safe, **inline** al lado de la tabla (no un
 *  Sheet flotante) — calcado del layout de "Safes" del PVWA nuevo: lista
 *  angosta + detalle con tabs Detalles/Miembros. Editar/Borrar quedan
 *  arriba, siempre visibles. Ver captura del 2026-09-15 (Alex). */
export function SafeDetailPanel({
  tenant,
  safe,
  onClose,
  onDeleted,
}: {
  tenant: string;
  safe: Safe;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [tab, setTab] = useState<Tab>("detalles");
  const safeId = safe.safeUrlId ?? "";

  const members = useQuery({
    queryKey: ["safe-members", tenant, safeId],
    queryFn: () =>
      api<SafeMembersResponse>(`/tenants/${tenant}/safes/${encodeURIComponent(safeId)}/members`),
    enabled: !!safeId,
  });

  const retention =
    safe.numberOfDaysRetention != null
      ? `${safe.numberOfDaysRetention} días`
      : safe.numberOfVersionsRetention != null
        ? `${safe.numberOfVersionsRetention} versiones`
        : "—";

  return (
    <div className="flex min-w-0 flex-1 flex-col rounded-lg border border-border bg-card">
      <div className="flex items-start justify-between gap-2 border-b border-border p-4">
        <h2 className="min-w-0 break-words text-lg font-bold text-navy-900">
          {safe.safeName ?? safeId}
        </h2>
        <button
          onClick={onClose}
          className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Cerrar"
        >
          <X className="size-4" />
        </button>
      </div>

      {safeId && (
        <div className="flex flex-wrap items-start gap-4 border-b border-border px-4 py-3">
          <SafeEditSection tenant={tenant} safeId={safeId} safe={safe as unknown as Obj} />
          <SafeDeleteSection
            tenant={tenant}
            safeId={safeId}
            safeName={safe.safeName ?? safeId}
            onDeleted={onDeleted}
          />
        </div>
      )}

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-4 mt-3 w-fit">
          <TabsTrigger value="detalles">Detalles</TabsTrigger>
          <TabsTrigger value="miembros">
            Miembros{members.data ? ` · ${members.data.members.length}` : ""}
          </TabsTrigger>
        </TabsList>

        <div className="flex-1 overflow-y-auto p-4">
          <TabsContent value="detalles" className="mt-0 grid grid-cols-2 gap-x-5 gap-y-3.5">
            <Field label="Nombre" value={safe.safeName ?? "—"} />
            <Field label="Descripción" value={safe.description || "—"} />
            <Field label="CPM asignado" value={safe.managingCPM || "—"} />
            <Field label="Retención" value={retention} />
            <Field
              label="OLAC"
              value={<Badge variant={safe.olacEnabled ? "success" : "neutral"}>{safe.olacEnabled ? "sí" : "no"}</Badge>}
            />
            <Field
              label="Purga automática"
              value={<Badge variant={safe.autoPurgeEnabled ? "success" : "neutral"}>{safe.autoPurgeEnabled ? "sí" : "no"}</Badge>}
            />
            <Field
              label="Creada"
              value={
                safe.creationTime ? (
                  <>
                    {fmtDate(safe.creationTime)}{" "}
                    <span className="text-muted-foreground">· {relTime(safe.creationTime)}</span>
                  </>
                ) : (
                  "—"
                )
              }
            />
            <Field
              label="Última modificación"
              value={safe.lastModificationTime ? fmtDateTime(safe.lastModificationTime) : "—"}
            />
          </TabsContent>

          <TabsContent value="miembros" className="mt-0 flex flex-col gap-3">
            {members.isLoading && (
              <div className="flex flex-col gap-2">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
              </div>
            )}
            {members.isError && (
              <Alert variant="destructive">{(members.error as Error).message}</Alert>
            )}
            {members.data &&
              (members.data.members.length === 0 ? (
                <p className="text-sm text-muted-foreground">Sin miembros visibles.</p>
              ) : (
                <ul className="divide-y divide-border rounded-lg border border-border">
                  {members.data.members.map((m, i) => (
                    <SafeMemberRow key={m.memberName ?? i} tenant={tenant} safeId={safeId} member={m} />
                  ))}
                </ul>
              ))}
            <SafeMemberAddSection tenant={tenant} safeId={safeId} />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
