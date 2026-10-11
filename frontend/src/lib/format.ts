// Helpers de formato para las grillas del inventario.

/** Fecha legible a partir de un epoch (s o ms) o un string ISO. "—" si no hay. */
export function fmtDate(v: unknown): string {
  if (v == null || v === "") return "—";
  const d =
    typeof v === "number"
      ? new Date(v < 1e12 ? v * 1000 : v)
      : new Date(String(v));
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export function dash(v: unknown): string {
  return v == null || v === "" ? "—" : String(v);
}

/** fecha + hora legible (para tooltips). "—" si no hay. */
export function fmtDateTime(v: unknown): string {
  const d =
    typeof v === "number"
      ? new Date(v < 1e12 ? v * 1000 : v)
      : v == null || v === ""
        ? null
        : new Date(String(v));
  if (!d || Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** epoch (s o ms) / ISO -> Date, o null. */
export function toDate(v: unknown): Date | null {
  if (v == null || v === "") return null;
  const d = typeof v === "number" ? new Date(v < 1e12 ? v * 1000 : v) : new Date(String(v));
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "hace 3 min" / "hace 2 h" / fecha absoluta si es viejo. "—" si no hay. */
export function relTime(v: unknown): string {
  const d = toDate(v);
  if (!d) return "—";
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "recién";
  if (min < 60) return `hace ${min} min`;
  if (min < 60 * 24) return `hace ${Math.round(min / 60)} h`;
  return d.toLocaleDateString("es", { year: "numeric", month: "2-digit", day: "2-digit" });
}

const ALERO_ROLE_LABELS: Record<string, string> = {
  TenantAdmin: "Administrator",
  VendorManager: "Vendor Manager",
  User: "User",
};

export function aleroRoleLabel(role: string | undefined | null): string {
  if (!role) return "—";
  return ALERO_ROLE_LABELS[role] ?? role;
}

