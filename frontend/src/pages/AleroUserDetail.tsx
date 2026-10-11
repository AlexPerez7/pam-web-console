import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Avatar, AleroStatusBadge } from "@/components/AleroBadges";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";
import { aleroRoleLabel, dash, fmtDateTime } from "@/lib/format";
import type { AleroUserDetail as AleroUserDetailType } from "@/lib/types";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      <div className="flex flex-col gap-3 text-sm">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="break-words font-medium text-navy-900">{children}</span>
    </div>
  );
}

export default function AleroUserDetail() {
  const { userId } = useParams<{ userId: string }>();
  const [params] = useSearchParams();
  const tenant = params.get("tenant") ?? "";

  const q = useQuery({
    queryKey: ["alero-user-detail", tenant, userId],
    queryFn: () => api<AleroUserDetailType>(`/tenants/${tenant}/alero-users/${userId}`),
    enabled: !!tenant && !!userId,
  });
  const u = q.data;
  const fullName = u ? [u.firstName, u.lastName].filter(Boolean).join(" ") : undefined;

  return (
    <div className="flex flex-col gap-4">
      <Link
        to={`/alero-users?tenant=${tenant}`}
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Volver a Usuarios
      </Link>

      {q.isLoading && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {u && (
        <div className="flex flex-col gap-6 rounded-lg border border-border bg-card p-6">
          <div className="flex items-center gap-4 border-b border-border pb-6">
            <Avatar name={fullName || u.username} size={56} />
            <div className="flex flex-col gap-1">
              <h1 className="text-[22px] font-bold text-navy-900">{dash(fullName || u.username)}</h1>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span>{aleroRoleLabel(u.role)}</span>
                <AleroStatusBadge status={u.status} />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-x-8 gap-y-6 md:grid-cols-2 lg:grid-cols-3">
            <Section title="Detalles">
              <Field label="Nombre">{dash(u.firstName)}</Field>
              <Field label="Apellido">{dash(u.lastName)}</Field>
              <Field label="Usuario">{dash(u.username)}</Field>
              <Field label="Teléfono">{dash(u.phoneNumber)}</Field>
              <Field label="Dispositivo">
                {u.phoneModel || u.phoneOS
                  ? `${u.phoneModel ?? ""} ${u.phoneOS ? `(${u.phoneOS})` : ""}`.trim()
                  : "—"}
              </Field>
            </Section>

            <Section title="Administración">
              <Field label="Rol">{aleroRoleLabel(u.role)}</Field>
              <Field label="Estado">{dash(u.status)}</Field>
              {u.invitedBy && <Field label="Invitado por">{u.invitedBy}</Field>}
              <Field label="Versión app">{dash(u.appVersion)}</Field>
            </Section>

            <Section title="Acceso">
              <Field label="Último login">{fmtDateTime(u.lastLogin)}</Field>
            </Section>
          </div>
        </div>
      )}
    </div>
  );
}
