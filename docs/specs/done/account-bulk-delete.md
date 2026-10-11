# account-bulk-delete

- **Estado:** hecha (probada en el server, 2026-10-01)
- **Roadmap:** Fase 3, escritura de alto riesgo — "Eliminar varias cuentas"
- **Fecha:** 2026-10-01

## Problema

La barra flotante de la página Cuentas ya permite, con varias cuentas
seleccionadas, rotarlas, editarlas y moverlas de Safe, pero no eliminarlas.
Hoy el borrado es sólo individual (`DELETE /accounts/{id}`). Un cliente pidió
eliminar varias cuentas de una, y hacerlo una por una desde la UI es inviable
con volúmenes de decenas de cuentas.

## Alcance

Entra:
- Eliminar N cuentas de un tenant de una vez, desde la selección múltiple de
  la página Cuentas (botón "Eliminar" en la barra flotante).
- Previsualización por cuenta antes de ejecutar.
- Backup cifrado por cuenta antes de borrarla (mismo mecanismo que
  `account.delete`).

Queda afuera:
- Borrado de Safes en lote (`safe.delete` sigue siendo individual).
- Aprobación de un segundo operador sobre el lote (ver "Decisiones").
- Restauración en lote desde backups (`account.restore` sigue individual).

## Requisitos

1. **Preview sin tocar CyberArk.** `POST /tenants/{id}/accounts/bulk-delete/preview`
   con `account_ids` devuelve, por cuenta, `name`, `safeName`, `userName`,
   `address` y un `ok`/`error`. Una cuenta inexistente o sin acceso aparece
   con `ok: false` y no bloquea al resto.
   - Dado 3 ids de los que uno no existe, cuando se previsualiza, entonces
     `total=3`, `valid=2`, `invalid=1`.
2. **Ejecución cuenta por cuenta, secuencial.** `POST
   /tenants/{id}/accounts/bulk-delete` con `account_ids`, `reason` y
   `confirm`. Por cada cuenta: recuperar la contraseña, guardar el
   `AccountBackup`, borrar. **Si el backup falla, esa cuenta NO se borra** y
   queda en `failed`. Una cuenta que falla no aborta el lote.
   - Dado 5 cuentas donde el borrado de la tercera falla en CyberArk, cuando
     se ejecuta, entonces `deleted=4`, `failed=[la tercera]`, y la tercera
     igual conserva su backup.
3. **Confirmación reforzada.** `reason` obligatoria (mínimo
   `MIN_REASON_LEN` caracteres). `confirm` debe ser exactamente
   `ELIMINAR <N>`, con N = cantidad de cuentas del lote (no la cantidad de
   válidas). Si no coincide: 422 y audit `result="denied"`.
4. **RBAC `platform_admin`**, mismo techo que `account.move.bulk`. Quien no lo
   sea recibe 403 y el botón no se muestra.
5. **Tope por lote** `write_object_cap` (50). Si se supera: 409.
6. **Auditoría.** Un registro por lote (`account.delete.bulk`, con total,
   borradas, fallidas y `failed_rows`) y uno por la previsualización
   (`account.delete.bulk.preview`). Se agregan a `audit_catalog.py`. Cada
   backup queda en `account_backups` con `deleted_by` y la razón.
7. **Frontend.** Botón "Eliminar" (ícono + `aria-label`) en la barra flotante
   sólo para `platform_admin`. Diálogo con el mismo flujo que
   `AccountBulkMoveDialog`: previsualizar → tabla de cuentas → razón →
   escribir `ELIMINAR N` → ejecutar → resultado con fallidas. Aviso explícito:
   se pierde el historial nativo de CyberArk de cada cuenta; la contraseña
   actual queda en el backup cifrado y se puede restaurar desde Backups.

## Diseño técnico

- Backend, en `app/api/writes.py`: dos endpoints nuevos
  (`account_bulk_delete_preview`, `account_bulk_delete`) calcados de
  `account_bulk_move*`. La lógica por cuenta se extrae del `apply` de
  `account_delete` a un helper `_delete_account_with_backup(client, session,
  *, tenant_id, op, account, reason)` compartido con el endpoint individual,
  para que haya un solo camino de "backup antes que borrado".
- **Secuencial, no `asyncio.gather`:** `save_account_backup` hace
  `session.commit()` y una `AsyncSession` no admite uso concurrente (mismo
  motivo que `account_bulk_move`). El preview sí es en paralelo (sólo
  lecturas).
- Schemas: `AccountBulkDeletePreviewIn {account_ids}` y
  `AccountBulkDeleteIn(+ reason, confirm)`.
- Frontend: `components/AccountBulkDeleteDialog.tsx`, `onDelete` opcional en
  la barra flotante de `pages/Accounts.tsx` (junto a `canBulkMove`), tipos en
  `lib/types.ts`.
- Tests (`tests/unit/test_account_bulk_delete.py`): preview con cuenta
  inexistente, ejecución mixta con fallo parcial, backup que falla no borra,
  confirmación incorrecta, cap, RBAC (403 a `approver`), auditoría.

## Decisiones

- **Sólo `platform_admin`, sin segunda aprobación.** `platform_admin` ejecuta
  directo según `docs/specs/dual-approval.md`; los lotes no pasan por
  `run_write`, igual que `account.move.bulk`. Si más adelante `approver+`
  necesita borrar en lote, habría que extender `app/core/approvals.py` a
  solicitudes de lote — fuera de esta spec.
- **Frase `ELIMINAR N`** en vez de un nombre: no hay un nombre único para N
  objetos, y incluir la cantidad obliga a mirar cuántas se están borrando.

## Tareas

- [x] Extraer `_delete_account_with_backup` y usarlo en `account_delete`.
- [x] Endpoints `bulk-delete/preview` y `bulk-delete` + schemas.
- [x] `account.delete.bulk(.preview)` en `audit_catalog.py`.
- [x] Tests unitarios.
- [x] `AccountBulkDeleteDialog.tsx` + botón en la barra flotante + tipos.
- [x] ruff, ruff format, mypy, pytest; lint, tsc y build del frontend.
- [x] Probar en el server con cuentas de prueba (borrar 2-3 y restaurar una
      desde Backups) antes de usarlo con el cliente.
- [x] Mover la spec a `done/` y actualizar `PLAN.md` y `ESTADO_ACTUAL.md`.
