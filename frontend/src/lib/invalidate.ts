import type { QueryClient } from "@tanstack/react-query";

/** Invalida todo lo que puede quedar stale tras una escritura sobre una cuenta:
 *  el detalle, la actividad, el rastro de audit y la grilla del inventario.
 *  Repite el detalle + actividad a los 8 s: las operaciones del CPM son
 *  asíncronas y el resultado no está listo al instante. */
export function invalidateAccount(qc: QueryClient, tenant: string, accountId: string): void {
  const account = [
    ["account-detail", tenant, accountId],
    ["account-activities", tenant, accountId],
  ];
  for (const key of [...account, ["audit", tenant, accountId], ["inv", tenant, "accounts"]]) {
    qc.invalidateQueries({ queryKey: key });
  }
  setTimeout(() => {
    for (const key of account) qc.invalidateQueries({ queryKey: key });
  }, 8000);
}

/** Invalida lo que queda stale tras agregar un miembro a una safe. */
export function invalidateSafe(qc: QueryClient, tenant: string, safeId: string): void {
  for (const key of [
    ["safe-members", tenant, safeId],
    ["audit", tenant, safeId],
  ]) {
    qc.invalidateQueries({ queryKey: key });
  }
}

/** Invalida lo que queda stale tras una escritura sobre un usuario del Vault. */
export function invalidateUser(qc: QueryClient, tenant: string, userId: string): void {
  for (const key of [
    ["user-detail", tenant, userId],
    ["audit", tenant, userId],
    ["inv", tenant, "users"],
  ]) {
    qc.invalidateQueries({ queryKey: key });
  }
}

/** Invalida lo que queda stale tras una escritura sobre un grupo del Vault. */
export function invalidateGroup(qc: QueryClient, tenant: string, groupId: string): void {
  for (const key of [
    ["group-members", tenant, groupId],
    ["audit", tenant, groupId],
    ["inv", tenant, "groups"],
  ]) {
    qc.invalidateQueries({ queryKey: key });
  }
}
