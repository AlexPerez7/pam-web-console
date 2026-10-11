import type { OperatorRoleName } from "./types";

// misma jerarquía que app/core/rbac.py
const RANK: Record<OperatorRoleName, number> = {
  viewer: 0,
  operator: 1,
  approver: 2,
  tenant_admin: 3,
  platform_admin: 4,
};

export function roleAtLeast(
  role: OperatorRoleName | null | undefined,
  min: OperatorRoleName,
): boolean {
  if (!role) return false;
  return RANK[role] >= RANK[min];
}

/** Nombre legible (español) de cada rol, para la UI. */
export const ROLE_LABELS: Record<OperatorRoleName, string> = {
  viewer: "Lectura",
  operator: "Operador",
  approver: "Aprobador",
  tenant_admin: "Admin. de tenant",
  platform_admin: "Admin. de plataforma",
};

export function roleLabel(role: string | null | undefined): string {
  return (role && ROLE_LABELS[role as OperatorRoleName]) || role || "";
}
