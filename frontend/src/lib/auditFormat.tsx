import { Badge } from "@/components/ui/badge";
import type { AuditKind } from "@/lib/types";

const KIND_LABELS: Record<AuditKind, string> = {
  consulta: "consulta",
  cambio: "cambio",
  sensible: "sensible",
  acceso: "acceso",
};

export function resultBadge(result: string) {
  if (result === "ok") return <Badge variant="success">ok</Badge>;
  if (result === "denied") return <Badge variant="warning">denegado</Badge>;
  if (result === "locked") return <Badge variant="warning">bloqueado</Badge>;
  if (result === "pending_approval") return <Badge variant="info">pendiente</Badge>;
  return <Badge variant="danger">error</Badge>;
}

export function kindBadge(kind: AuditKind) {
  const variant =
    kind === "sensible"
      ? "danger"
      : kind === "acceso"
        ? "warning"
        : kind === "cambio"
          ? "info"
          : "neutral";
  return <Badge variant={variant}>{KIND_LABELS[kind]}</Badge>;
}
