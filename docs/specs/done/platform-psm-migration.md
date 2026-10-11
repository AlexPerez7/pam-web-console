# platform-psm-migration — reasignar el PSM de una plataforma

- **Estado:** hecha, **probada contra Globex (2026-09-24)** — funcionó
  bien, sin necesitar el rollback automático (o si lo necesitó, resolvió
  solo sin que se notara, que es el objetivo)
- **Roadmap:** `docs/PLAN.md §7` Fase 3 — extra a pedido de Alex
- **Fecha:** 2026-09-14

## Problema

Un compañero de Alex escribió un script para migrar el PSM asignado a un
conjunto de plataformas y, al no revisarlo bien, se disparó un **bug
conocido de la API de CyberArk**
(`community.cyberark.com/s/article/RESTAPI-Update-PSM-deletes-connectors-settings-on-target-platforms`):
el `PUT` de "Update session management policy of platform" reemplaza el
objeto `PrivilegedSessionManagement` **completo**, no hace merge. Si el
body sólo trae `{PSMServerId, PSMServerName}`, se borra `PSMConnectors` —
que es donde viven los "Override User Parameters" (ej. `PSMRemoteMachine`
de las plataformas de cuentas de dominio) — dejando esas cuentas
inconectables.

Alex ya resolvió esto con cuidado en `Platform_PSM_Migration.ps1`
(probado en producción): GET del objeto completo → backup → mutar sólo
los 2 campos → PUT → releer y verificar que nada más cambió → si algo
más cambió, revertir automáticamente al original. Esta spec lleva ese
mismo patrón a PAM Web Console.

## Alcance

**Entra:**
- `PUT /tenants/{id}/platforms/{platform_id}/psm`: reasigna el PSM de una
  plataforma con el patrón GET-completo → mutar 2 campos → PUT → verificar
  → rollback automático.
- `GET /tenants/{id}/platforms/{platform_id}/psm`: PSM actual de una
  plataforma puntual (no viene en el `GET /platforms` genérico — hace
  falta "Get target platforms", que trae el ID numérico interno que exige
  la API de PSM).
- `GET /tenants/{id}/psm-servers`: PSM configurados en el tenant, para el
  selector (mismo patrón que `/cpm-names`).
- Sección "Gestión de sesiones (PSM)" en el detalle de una plataforma
  (`DetailDrawer` gana soporte de `platformId`).
- Feature `platform.psm.update`, opt-in, apagada por defecto. RBAC
  `approver+` + confirmación reforzada (`confirm` = `platform_id`) —
  afecta a TODAS las cuentas de esa plataforma de una sola vez, mayor
  blast radius que cualquier otra operación de Fase 2/3 hasta ahora.

**Fuera:**
- Procesar varias plataformas en un solo pedido (el script original acepta
  un CSV) — v1 es una plataforma a la vez desde la UI. Si hace falta lote,
  se agrega después reusando el mismo endpoint.
- Backup en disco/DB de `PSMConnectors` — no hace falta: el objeto
  original queda en memoria durante todo el request (fetch → apply) y se
  usa directamente para el rollback si hace falta; si el rollback en sí
  falla, se audita completo (ver Requisito 4).
- El "Get platforms" genérico (`GET /API/Platforms/{id}/`) — confirmado en
  el script que sólo trae la política de gestión de contraseñas, no
  Connection Components; no sirve como backup adicional.

## Requisitos

1. **Dry-run trae `confirm_phrase`.** El diff muestra `PSM: <actual> ->
   <nuevo>`, `confirm_phrase = platform_id`, sin tocar CyberArk.
2. **Ejecución OK: PSMConnectors preservado.** El PUT real manda el objeto
   completo con sólo `PSMServerId`/`PSMServerName` mutados.
3. **Bug detectado → rollback automático.** Si tras el PUT la relectura
   muestra algo distinto de `PSMServerId`/`PSMServerName`, se hace un
   segundo PUT con el objeto original completo. La respuesta es `502` con
   un mensaje claro: la plataforma NO quedó migrada.
4. **Rollback también falla → crítico, pero no se pierde el objeto.** Se
   audita `platform.psm.rollback_failed` con el objeto original completo
   en el `detail` (JSON, sin secretos) ANTES de responder el error — el
   objeto original queda recuperable desde el audit aunque el request
   termine en 502.
5. **RBAC/confirmación/feature** — mismos criterios que el resto de
   Fase 3.

## Diseño técnico

### Backend

- `app/cyberark/client.py`: `list_target_platforms()` (`GET
  /API/Platforms/Targets/` — trae el ID numérico + el
  `PrivilegedSessionManagement.PSMServerId` actual), `get_platform_psm`,
  `update_platform_psm` (`GET`/`PUT
  .../Platforms/Targets/{id}/PrivilegedSessionManagement/`).
- `app/domain/platforms.py` (módulo nuevo): `find_target_platform`
  (resuelve `PlatformID` string → objeto con el ID numérico),
  `normalize_psm` (anula `PSMServerId`/`PSMServerName` para comparar si
  algo más cambió), `plan_platform_psm(platform_id)` (factory de
  `build_plan`, mismo patrón que `plan_cpm` en `app/domain/accounts.py`).
- `app/api/writes.py`: `platform_psm_update` — el `apply` hace el PUT,
  releé, compara con `normalize_psm`, y si hace falta, intenta el
  rollback (con su propio audit crítico si falla). No usa `Plan.patch`
  (el PUT real no es JSON Patch); el diff/confirm_phrase sí viajan en el
  `Plan` como el resto de Fase 3.
- `app/api/inventory.py`: `GET .../platforms/{id}/psm` (lectura, rol
  `viewer`) y `GET .../psm-servers` (reusa `client.list_psm_servers()`,
  ya existente para el reporte `psm_balance`).

### Frontend

- `src/components/PlatformPsmSection.tsx`: PSM actual + botón "Cambiar" →
  `PsmServerField` (selector, mismo patrón que `CpmField`) →
  `<DryRunDialog method="PUT">`, con el warning del bug conocido siempre
  visible al editar.
- `DetailDrawer.tsx` gana `platformId` (además de `accountId`/`safeId`/
  etc.), `PlatformsTab` en `Inventory.tsx` ahora pasa `tenant` +
  `platformId`.

## Tareas

- [x] `app/cyberark/client.py`: `list_target_platforms`, `get_platform_psm`,
      `update_platform_psm`.
- [x] `app/domain/platforms.py`: `find_target_platform`, `normalize_psm`,
      `plan_platform_psm`.
- [x] `app/core/features.py`: `platform.psm.update`.
- [x] `app/api/writes.py`: `PUT /platforms/{id}/psm`.
- [x] `app/api/inventory.py`: `GET /platforms/{id}/psm`, `GET /psm-servers`.
- [x] `src/components/PsmServerField.tsx` + `PlatformPsmSection.tsx`.
- [x] `DetailDrawer.tsx` + `Inventory.tsx` (tab Plataformas): wiring.
- [x] `pages/Tenants.tsx`: la feature nueva en `FEATURES`.
- [x] Tests unit: dominio (find/normalize/plan), dry-run, ejecución OK,
      bug detectado + rollback OK, rollback falla (crítico, audit con el
      objeto original), plataforma inexistente, RBAC, feature apagada.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [ ] Probar contra Globex antes de cerrar del todo — idealmente
      reproduciendo el bug conocido contra una plataforma de prueba para
      confirmar que el rollback automático funciona en un tenant real, no
      sólo contra el fake del test.

## Addendum (2026-09-14): grilla agrupada + columna PSM

Pedido de Alex sobre capturas del "Platform Management" nativo del PVWA:
agrupar el tab Plataformas por `systemType` en secciones colapsables
(Windows/`*NIX`/Database/...), y mostrar el PSM asignado como columna en
vez de tener que abrir el detalle de cada una.

- [x] `platforms_rows_cached()` (`app/api/inventory.py`) le agrega
      `psmServerId` a cada fila usando `client.list_target_platforms()`
      (tolerante si falla — degrada sin el dato, no rompe el listado).
      `platform.psm.update` invalida esta caché (`pam:platforms:{id}`) al
      ejecutar, igual que `safe.update` con `pam:safes:{id}`.
- [x] `pages/Inventory.tsx`: `PlatformGroup` (sección colapsable por
      `systemType`, cada una con su propia `DataTable`) reemplaza la
      tabla plana única; columna "PSM" nueva en `platformCols`.
- [x] Búsqueda única arriba de todos los grupos (no una por grupo) — filtra
      `id`/`name`/`systemType`/`platformType`/`psmServerId` en las
      plataformas de TODOS los tipos antes de agrupar; con una búsqueda
      activa, los grupos con resultados se fuerzan abiertos (si no, habría
      que expandir grupo por grupo para ver qué matcheó). `PlatformGroup`
      pasó a ser controlado por el padre (`open`/`onToggle`) en vez de
      manejar su propio estado, para poder forzarlo así.
- [x] `lint` + `tsc` + `build` verdes. No se pudo verificar visualmente en
      navegador (sin Docker local para levantar el stack).

