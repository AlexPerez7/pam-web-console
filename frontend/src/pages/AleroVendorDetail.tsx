import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Avatar, AleroStatusBadge } from "@/components/AleroBadges";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import type { AleroVendorDetail as AleroVendorDetailType } from "@/lib/types";

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

const INVITED_VENDORS_STATUS_LABEL: Record<string, string> = {
  Activated: "Automática",
  Deactivated: "Desactivada",
  RequiresAdminConfirmation: "Requiere confirmación",
};

export default function AleroVendorDetail() {
  const { vendorId } = useParams<{ vendorId: string }>();
  const [params] = useSearchParams();
  const tenant = params.get("tenant") ?? "";

  const q = useQuery({
    queryKey: ["alero-vendor-detail", tenant, vendorId],
    queryFn: () => api<AleroVendorDetailType>(`/tenants/${tenant}/alero-vendors/${vendorId}`),
    enabled: !!tenant && !!vendorId,
  });
  const v = q.data;
  const fullName = v ? [v.firstName, v.lastName].filter(Boolean).join(" ") : undefined;

  return (
    <div className="flex flex-col gap-4">
      <Link
        to={`/alero-vendors?tenant=${tenant}`}
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Volver a Vendors
      </Link>

      {q.isLoading && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {v && (
        <div className="flex flex-col gap-6 rounded-lg border border-border bg-card p-6">
          <div className="flex items-center gap-4 border-b border-border pb-6">
            <Avatar name={fullName || v.username} size={56} />
            <div className="flex flex-col gap-1">
              <h1 className="text-[22px] font-bold text-navy-900">{dash(fullName || v.username)}</h1>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span>Vendor</span>
                <AleroStatusBadge status={v.status} />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-x-8 gap-y-6 md:grid-cols-2 lg:grid-cols-4">
            <Section title="Detalles del vendor">
              <Field label="Nombre">{dash(v.firstName)}</Field>
              <Field label="Apellido">{dash(v.lastName)}</Field>
              <Field label="Email">{dash(v.email)}</Field>
              <Field label="Teléfono">{dash(v.phoneNumber)}</Field>
              <Field label="Dispositivo">
                {v.phoneModel || v.phoneOs
                  ? `${v.phoneModel ?? ""} ${v.phoneOs ? `(${v.phoneOs})` : ""}`.trim()
                  : "—"}
              </Field>
              <Field label="Comentarios">{dash(v.comments)}</Field>
              <Field label="Empresa">{dash(v.companyName)}</Field>
              <Field label="Versión app">{dash(v.appVersion)}</Field>
            </Section>

            <Section title="Administración">
              <Field label="Invitado por">{dash(v.invitedBy)}</Field>
              <Field label="Estado">{dash(v.status)}</Field>
              <Field label="Email verificado">{fmtDateTime(v.emailConfirmedAt)}</Field>
              <Field label="Aprovisionamiento">{dash(v.provisioningType)}</Field>
              <Field label="Permite unirse sin la app">{v.phoneAndEmailAuth ? "Sí" : "No"}</Field>
              <Field label="Puede invitar vendors">{v.canInvite ? "Sí" : "No"}</Field>
              {v.canInvite && (
                <>
                  <Field label="Activación de invitados">
                    {v.invitedVendorsInitialStatus
                      ? (INVITED_VENDORS_STATUS_LABEL[v.invitedVendorsInitialStatus] ??
                        v.invitedVendorsInitialStatus)
                      : "—"}
                  </Field>
                  <Field label="Máx. vendors a invitar">
                    {v.maxNumOfInvitedVendors ? v.maxNumOfInvitedVendors : "Sin límite"}
                  </Field>
                </>
              )}
            </Section>

            <Section title="Aplicaciones permitidas">
              {v.applications && v.applications.length > 0 ? (
                <div className="flex flex-col gap-2">
                  {v.applications.map((a, i) => (
                    <div key={i} className="flex flex-col">
                      <span className="font-medium text-navy-900">{dash(a.application)}</span>
                      <span className="text-xs text-muted-foreground">{dash(a.site)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
              <Field label="Último login">{fmtDateTime(v.lastLogin)}</Field>
            </Section>

            <Section title="Acceso">
              <Field label="Desde">{fmtDateTime(v.accessStartDate)}</Field>
              <Field label="Hasta">{fmtDateTime(v.accessEndDate)}</Field>
            </Section>
          </div>

          {v.roles && v.roles.length > 0 && (
            <div className="flex flex-col gap-3 border-t border-border pt-6">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Roles
              </h2>
              <div className="flex flex-wrap gap-1.5">
                {v.roles.map((r) => (
                  <Badge key={r} variant="neutral">
                    {r}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
