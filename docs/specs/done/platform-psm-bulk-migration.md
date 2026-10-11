# Reasignar el PSM de varias plataformas de una

- **Estado:** cerrada — probada contra Globex (2026-09-30)
- **Roadmap:** Fase 3 — extiende `platform.psm.update`
  (`docs/specs/platform-psm-migration.md`)
- **Fecha:** 2026-09-30

## Problema

Globex va a aplicar un parche a uno de sus dos servidores PSM. Mientras
dure, todas las plataformas que hoy apuntan a ese PSM tienen que migrar
al otro. PAM Web Console ya sabía reasignar el PSM de **una** plataforma a la vez
(`platform.psm.update`, con el backup+verificación+rollback automático
que ya evitó un incidente real de CyberArk borrando Connection
Components) — pero mover decenas de plataformas una por una desde la UI
no es viable en el tiempo que dura un parche programado.

## Alcance

**Entra:**
- Selección múltiple de plataformas en `/platforms` (checkboxes en la
  grilla — mismo mecanismo que ya usa la tab Cuentas).
- Un diálogo que previsualiza (un diff por plataforma) y ejecuta la
  reasignación al mismo PSM destino para todas las seleccionadas.
- Cada plataforma se procesa con el MISMO motor de seguridad que la
  reasignación individual — GET completo → mutar sólo 2 campos → PUT →
  releer y verificar → rollback automático si algo más cambió. Una
  plataforma que falla NO aborta el resto del lote.
- Tope de plataformas por lote: reusa `PAM_WRITE_OBJECT_CAP` (default
  50), igual que el resto de las operaciones en lote de PAM Web Console.

**Queda afuera:**
- Deshacer el lote entero de una — si hace falta revertir, es plataforma
  por plataforma (cada una ya tiene su propio botón "Cambiar" en el
  detalle).
- Programar la migración para una fecha/hora futura — se ejecuta al
  toque, como el resto de las escrituras de PAM Web Console.

## Requisitos

1. **Preview sin tocar CyberArk.** Dado un conjunto de `platform_ids` y un
   `psm_server_id` destino, `POST .../platforms/bulk-psm-update/preview`
   devuelve un diff por plataforma (`from`/`to` del PSM actual) sin
   escribir nada. Una plataforma que ya está en el PSM destino, o que no
   existe, queda marcada `ok: false` con el motivo, sin bloquear el resto
   del preview.
2. **Ejecución independiente por plataforma.** `POST .../platforms/
   bulk-psm-update` reasigna cada plataforma válida con el mismo patrón
   de `platform.psm.update` (`_apply_platform_psm_update`, compartido).
   Si CyberArk borra algo más que el PSM al guardar (bug conocido), esa
   plataforma revierte sola y queda en `failed`; las demás siguen.
3. **Confirmación reforzada manual.** Sin `Plan.confirm_phrase` (no
   aplica a N objetos): el body exige `confirm` == el `psm_server_id`
   destino exacto, además de `reason` (mínimo 8 caracteres). Un `confirm`
   que no coincide devuelve 422 sin tocar CyberArk, auditado
   `result="denied"`.
4. **Mismo RBAC que el cambio individual.** `approver+` — no se sube a
   `platform_admin` (a diferencia de `account.move.bulk`, que sí escala
   porque cada cuenta pierde historial de forma irreversible): acá cada
   plataforma ya lleva su propia verificación y rollback, el lote no
   suma riesgo estructural nuevo.
5. **Auditado una vez por lote.** `platform.psm.update.bulk` con el
   detalle (`psm_server_id`, `total`, `updated`, `failed`, `failed_rows`)
   — más los `platform.psm.rollback_failed` individuales si algún
   rollback también falla (mismo criterio que hoy).

## Diseño técnico

- `app/api/writes.py`: se extrae `_apply_platform_psm_update()` del
  `apply` de `platform_psm_update` (el endpoint individual pasa a
  llamarla también) — corazón compartido entre el endpoint de una
  plataforma y el de varias, sin duplicar la lógica de
  verificación/rollback.
- `POST /tenants/{id}/platforms/bulk-psm-update/preview` — lecturas en
  paralelo (`asyncio.gather`, como `account_bulk_move_preview`: sin
  escritura en `session` dentro de cada tarea, es seguro).
- `POST /tenants/{id}/platforms/bulk-psm-update` — **secuencial, sin
  `asyncio.gather`** (como `account_bulk_move`): cada plataforma puede
  necesitar auditar+commitear un `platform.psm.rollback_failed` sobre la
  misma `AsyncSession`, y eso no es seguro en paralelo.
- Reusa `find_target_platform`/`plan_platform_psm` de
  `app/domain/platforms.py`, sin funciones de dominio nuevas — el diff
  del preview sale de llamar `plan_platform_psm(pid)(psm, {...})`
  directo, igual que el endpoint individual.
- Frontend: `components/PlatformBulkPsmDialog.tsx` (preview → tabla de
  resultados → confirmar, calcado de `AccountBulkMoveDialog.tsx`, con
  `PsmServerField` para elegir el destino). `pages/Platforms.tsx` suma
  selección de filas (`DataTable` `selectedIds`/`onSelectedIdsChange`,
  compartida entre los `DataTable` de cada grupo colapsable de
  `systemType` — cada "seleccionar todo" sólo toca las filas visibles de
  SU grupo) + una barra flotante inferior con "Mover de PSM", visible
  sólo si el operador es `approver+` en el tenant (mismo criterio que el
  botón "Cambiar" del detalle individual).
- Acciones nuevas en `app/core/audit_catalog.py`:
  `platform.psm.update.bulk` / `.bulk.preview` (categoría "Plataformas").

## Tareas

- [x] `_apply_platform_psm_update()` compartida.
- [x] `POST .../platforms/bulk-psm-update/preview`.
- [x] `POST .../platforms/bulk-psm-update`.
- [x] Acciones nuevas en `audit_catalog.py`.
- [x] `PlatformBulkPsmDialog.tsx` + selección de filas en `Platforms.tsx`.
- [x] Tests backend (`test_platform_bulk_psm.py`).
- [x] Backend: ruff+mypy verdes; **pytest no se corrió** (mismo límite de
      entorno que el resto de esta sesión — sin Python 3.12/Docker
      locales). Frontend: lint+tsc+build verdes.
- [x] Probar contra Globex antes del parche real: mover 2-3 plataformas
      de prueba entre los dos PSM y confirmar que no se pierde ningún
      Connection Component.
