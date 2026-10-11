export type CpmAction = "change" | "verify" | "reconcile";

export const CPM_LABEL: Record<CpmAction, string> = {
  change: "Cambiar contraseña ahora",
  verify: "Verificar",
  reconcile: "Reconciliar",
};

export function isInProcess(status?: string): boolean {
  return String(status ?? "").toLowerCase().replace(/\s/g, "") === "inprocess";
}
