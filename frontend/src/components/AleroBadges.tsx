import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Paleta de acento de la marca (design system propio, ver index.css) —
 *  usada para los avatares de iniciales, determinística por nombre para
 *  que la misma persona siempre tenga el mismo color. */
const AVATAR_COLORS = [
  "bg-line-cloud",
  "bg-line-identity",
  "bg-line-consulting",
  "bg-line-data",
  "bg-line-transformation",
  "bg-line-awareness",
  "bg-navy-500",
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

function initials(name: string | undefined | null): string {
  const clean = (name ?? "").trim();
  if (!clean) return "?";
  const parts = clean.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Avatar circular con iniciales, color determinístico por nombre. */
export function Avatar({ name, size = 32 }: { name: string | undefined | null; size?: number }) {
  const label = initials(name);
  const color = AVATAR_COLORS[hashString(label) % AVATAR_COLORS.length];
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white",
        color,
      )}
      style={{ width: size, height: size, fontSize: size * 0.38 }}
      title={name ?? undefined}
    >
      {label}
    </span>
  );
}

/** Estado de Vendors/Users de Alero -- mismo vocabulario en ambos recursos
 *  (`Activated` / `Deactivated` / `PendingActivation`), confirmado contra
 *  Globex real (2026-09-21). */
export function AleroStatusBadge({ status }: { status: string | undefined | null }) {
  const variant: BadgeProps["variant"] =
    status === "Activated"
      ? "success"
      : status === "Deactivated"
        ? "danger"
        : status === "PendingActivation"
          ? "warning"
          : "neutral";
  return <Badge variant={variant}>{status ?? "—"}</Badge>;
}

/** Vigencia de acceso de un vendor (`expirationStatus`) -- sólo se vio
 *  "InTimeframe" contra datos reales; cualquier otro valor se marca como
 *  advertencia (vencido o fuera de ventana) hasta confirmar el resto del
 *  vocabulario. */
export function AleroExpirationBadge({ status }: { status: string | undefined | null }) {
  if (!status) return <span className="text-muted-foreground">—</span>;
  const variant: BadgeProps["variant"] = status === "InTimeframe" ? "success" : "warning";
  return <Badge variant={variant}>{status}</Badge>;
}
