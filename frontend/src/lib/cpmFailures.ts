import type { AccountActivity } from "@/lib/types";

// change/verif/reconcil -- mismas palabras clave que clasifican una
// operación del CPM en `classify()` de `activityClassify.ts`.
const CPM_KEYWORDS = ["verif", "reconcil", "change", "cambio"];

/** Actividades de una cuenta cuya `Action` indica una falla DEL CPM
 *  (cambio, verificación o reconciliación), más recientes primero. No
 *  alcanza con que el `Action` diga "fail": CyberArk también registra
 *  fallas de SESIÓN PSM (ej. "PSMSR280E ... has stopped unexpectedly",
 *  componentes que se cuelgan, timeouts) que no tienen nada que ver con
 *  la gestión de la contraseña -- si una de esas es más reciente que la
 *  última falla real del CPM, tapaba el motivo correcto (bug real
 *  encontrado el 2026-09-24 armando `account-inventory-full`, mismo
 *  criterio replicado en `app/reports/account_inventory.py`, ver
 *  `docs/specs/done/batch4-reports.md`). Usado por `LastCpmErrorCell`
 *  (lista de Troubleshooting) y `TroubleshootingAccountPanel`
 *  (detalle). */
export function cpmFailures(activities: AccountActivity[]): AccountActivity[] {
  return activities
    .filter((a) => {
      const action = (a.Action ?? "").toLowerCase();
      return action.includes("fail") && CPM_KEYWORDS.some((k) => action.includes(k));
    })
    .sort((a, b) => (b.Date ?? 0) - (a.Date ?? 0));
}
