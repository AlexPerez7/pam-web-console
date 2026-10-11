# account-bulk-edit — editar varias cuentas a la vez con el mismo valor

- **Estado:** hecha (implementada 2026-09-14, probada por Alex contra Globex; cerrada el 2026-10-07)
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — extra a pedido de un cliente
- **Fecha:** 2026-09-14

## Problema

Globex pidió actualizar el `address` de 5 cuentas de firewall — todas al
mismo valor nuevo. La vista clásica del PVWA permite tildar varias cuentas
con checkbox y editarlas juntas; PAM Web Console sólo tenía edición de a una
(`account.update`).

## Alcance

**Entra:**
- Checkboxes en la grilla de Cuentas (`DataTable` gana soporte de
  selección, controlado por el padre — sobrevive a la paginación
  server-side porque se guarda por id real, no por índice de fila).
- `POST /tenants/{id}/accounts/bulk-update`: aplica el mismo `changes`
  (`address`/`userName`/`name`) a una lista de `account_ids`. Reusa
  `build_account_plan` (dominio de `account.update`) cuenta por cuenta.
- Diálogo "Editar N cuenta(s)": elegir el campo + UN valor nuevo, aplicado
  a todas las seleccionadas.
- Misma feature `account.update` y RBAC `operator+` que la edición
  individual — no es una operación nueva, es la misma en lote.
- El circuit breaker existente de `writeop.run_write`
  (`PAM_WRITE_OBJECT_CAP`, default 25) ya cubre el caso "demasiadas
  cuentas de una vez", sin código nuevo.

**Fuera (explícitamente, por pedido de Alex):**
- Valores **distintos** por cuenta en un solo lote (ej. accountA →
  10.1.1.1, accountB → 10.1.1.2) — eso sigue siendo `account.update` una
  por una. Esta feature es sólo para "el mismo valor a todas".
- Editar `platformAccountProperties` en lote (propiedades custom por
  plataforma) — v1 se limita a los 3 campos top-level ya editables.

## Requisitos

1. **Diff por cuenta.** Dadas 3 cuentas donde 2 tienen un `address`
   distinto al pedido y 1 ya lo tiene, el dry-run muestra 2 filas de diff
   (una por cuenta afectada) — la que ya coincidía no aparece.
2. **Ejecución: sólo se tocan las que cambian.** De 3 cuentas
   seleccionadas, si 1 ya tenía el valor, `client.update_account` se llama
   sólo para las otras 2.
3. **Sin cambios reales → no-op.** Si todas las seleccionadas ya tienen el
   valor pedido, `no_op: true`, sin llamar a CyberArk.
4. **Selección persiste entre páginas.** Tildar cuentas en la página 1,
   cambiar de página y volver — la selección se mantiene (se guarda por
   `account.id`, no por posición en la grilla).
5. **RBAC/feature.** Mismos criterios que `account.update`: `403` si la
   feature está apagada o el rol es `viewer`.

## Diseño técnico

### Backend

- `app/domain/accounts.py`: `build_account_bulk_plan(current, changes)` —
  `current` = `{account_id: account_dict}` (ya resueltos por el `fetch`).
  Llama a `build_account_plan` por cuenta y prefija cada fila del diff con
  el nombre de la cuenta. `Plan.patch` queda siempre vacío a propósito —
  el patch real es por-cuenta (JSON Patch es por recurso) y se recalcula
  en `apply` con la misma `changes`, no hay forma de meter N patches en un
  único `Plan.patch`. `object_count` = cuentas realmente afectadas.
- `app/api/writes.py`: `POST /accounts/bulk-update` —
  `AccountBulkUpdateIn.account_ids` (dedupe preservando orden) +
  `changes`. Mismo `fetch`/`apply`/`run_write` que el resto; si una cuenta
  de la lista no existe, el `fetch` completo falla (404) y no se toca
  ninguna — mismo criterio "fail-fast" que el resto de los `fetch` de
  Fase 2/3 (no es best-effort como el backup masivo de
  `account.reveal.bulk`, que es un caso distinto: ahí cualquier cuenta
  puede fallar sola porque el objetivo es un reporte completo, acá el
  operador eligió activamente estas N cuentas para escribir).

### Frontend

- `src/components/DataTable.tsx`: props opcionales `selectedIds`/
  `onSelectedIdsChange` (controlados por el padre) — agregan una columna
  de checkbox (header con "seleccionar todas las visibles", estado
  indeterminado si hay selección parcial). No rompe compatibilidad: sin
  esas props, la tabla se comporta igual que antes.
- `src/components/DryRunDialog.tsx`: prop nueva `extraBody` — campos
  extra a nivel raíz del body junto a `changes`/`dry_run`/`reason` (acá,
  `account_ids`). Reusable para cualquier futura escritura en lote.
- `src/components/AccountBulkEditDialog.tsx`: selector de campo (Address/
  Usuario/Nombre) + input de valor → `<DryRunDialog>`.
- `pages/Inventory.tsx` (tab Cuentas): botón "Editar N cuenta(s)" en el
  toolbar, visible sólo con `account.update` habilitada + `operator+` +
  al menos una fila tildada.

## Tareas

- [x] `app/domain/accounts.py`: `build_account_bulk_plan`.
- [x] `app/api/writes.py`: `POST /accounts/bulk-update`.
- [x] `src/components/DataTable.tsx`: selección controlada.
- [x] `src/components/DryRunDialog.tsx`: `extraBody`.
- [x] `src/components/AccountBulkEditDialog.tsx` + wiring en
      `Inventory.tsx` (tab Cuentas).
- [x] Tests unit: diff por cuenta, sólo se tocan las que cambian, no-op,
      dedupe de ids, cuenta inexistente aborta todo, RBAC, feature
      apagada, `account_ids` vacío.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] Probar contra Globex antes de cerrar del todo (el caso real que
      lo pidió: 5 cuentas de firewall, mismo `address` nuevo). Probado por
      Alex; cerrada el 2026-10-07.
