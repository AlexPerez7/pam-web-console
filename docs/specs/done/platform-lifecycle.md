# platform-lifecycle — activar / desactivar / duplicar / borrar una plataforma

- **Estado:** **probada de punta a punta contra Globex (2026-09-24)** —
  duplicar, activar, desactivar y borrar, todo funcionó bien
- **Roadmap:** `docs/PLAN.md §7` Fase 3, ítem "Activar/desactivar/duplicar/
  importar plataformas" (recorte: **importar queda afuera** — sube/parsea
  un `.zip` de configuración, alcance de UI mucho más grande y sin pedido
  concreto; se abre spec propia si hace falta).
- **Fecha:** 2026-09-14

## Problema

PAM Web Console hoy sólo puede editar el PSM de una plataforma (`platform.psm.update`).
El "Platform Management" del PVWA clásico permite además activar/
desactivar, duplicar y borrar una plataforma — Alex pidió sumar el resto
del CRUD (menos importar) para acercar PAM Web Console a la paridad del PVWA clásico.

## Alcance

**Entra**, tres operaciones nuevas, cada una con su propio nivel de riesgo:

| Operación | Feature | RBAC | Confirmación reforzada |
|---|---|---|---|
| Activar / Desactivar | `platform.activate` | `approver+` | Sí (`confirm` = `platform_id`) |
| Duplicar | `platform.duplicate` | `operator+` | No |
| Borrar | `platform.delete` | `approver+` | Sí (`confirm` = `platform_id`) |

**Fuera:**
- Importar plataforma (`.zip`).
- Activar/desactivar/duplicar/borrar **en lote** — sin caso de uso real
  hoy, a diferencia de `account.update`.

## Investigación: endpoints

`docs.cyberark.com` bloqueó el fetch automatizado también para estas
páginas (404 con user-agent de navegador, a diferencia de "Update
Account"). Verificado contra el código fuente del módulo open-source
`psPAS` (mismo criterio que `account-password-manual.md`):

```
POST   /API/Platforms/targets/{ID}/activate          Enable-PASPlatform
POST   /API/Platforms/targets/{ID}/deactivate         Disable-PASPlatform
POST   /API/Platforms/targets/{ID}/duplicate          Copy-PASPlatform
       body: {"name": "...", "description": "..."}
DELETE /API/Platforms/targets/{ID}                    Remove-PASPlatform
POST   /API/Platforms/{PlatformID}/Export             Export-PASPlatform
       (usa el PlatformID STRING, no el numérico; devuelve el
       paquete .zip como bytes)
```

- `{ID}` (activar/desactivar/duplicar/borrar) es el **ID numérico
  interno** de "Get target platforms" — igual que `platform.psm.update`,
  vía `find_target_platform()`.
- `Export` usa el `PlatformID` **string** (`WinDomain`), no el numérico —
  inconsistencia real de la API de CyberArk, no un typo.
- Los cuatro requieren CyberArk **11.4+** (`Assert-VersionRequirement` de
  `psPAS`); no verificable desde acá — si algún tenant está en una versión
  menor, CyberArk devuelve 404/501 y queda documentado al probar.
- `psPAS` no valida el estado actual antes de activar/desactivar — es un
  no-op seguro del lado de CyberArk.

## Diseño técnico

### Activar / Desactivar (sin cambios respecto a la primera versión de esta spec)

- `client.activate_target_platform(numeric_id)` /
  `deactivate_target_platform(numeric_id)`.
- `domain/platforms.py`: `plan_platform_activation(platform_id,
  target_active: bool)` — factory igual a `plan_cpm(action)`. No-op si
  `current["Active"]` ya coincide; si no, diff `active: from -> to` +
  warning direccional + `confirm_phrase=platform_id`.
- Endpoints `POST .../activate` / `.../deactivate`, `ConfirmedActionIn`,
  RBAC `_approver`.

### Duplicar

Riesgo bajo — sólo crea un objeto nuevo, no toca ninguna plataforma ni
cuenta existente (se revierte borrando la copia). Mismo nivel que
`account.create`/`safe.create`: `operator+`, **sin** confirmación
reforzada.

- `client.duplicate_target_platform(numeric_id, body: dict) -> dict` —
  `POST /API/Platforms/targets/{id}/duplicate`, `retry_safe=False` (no
  idempotente: un timeout puede haber creado la copia igual).
- `domain/platforms.py`: `build_platform_duplicate_plan(current, changes)`
  — `changes = {name, description?}`; valida que `name` no esté vacío y
  sea distinto del `platform_id` origen; `Plan.effect` = "Se crea la
  plataforma **{name}**, copia de **{platform_id}**."; sin diff de campos
  (es alta, no edición) — mismo estilo que `plan_account_create`.
- `POST /tenants/{id}/platforms/{platform_id}/duplicate` — body
  `{name, description?, dry_run, reason}` (`WriteActionIn` + los dos
  campos nuevos), RBAC `_operator`. `fetch` resuelve `numeric_id`. `apply`
  llama a `duplicate_target_platform` e invalida
  `pam:platforms:{tenant_id}`.
- Frontend: botón "Duplicar" en `PlatformPsmSection`'s hermano nuevo o
  directo en la fila de la grilla de Plataformas (`PlatformGroup` en
  `Inventory.tsx`) — a definir en la revisión de UI durante la
  implementación; funcionalmente un diálogo simple (nombre + descripción)
  → `<DryRunDialog>`.

### Borrar

El más riesgoso de los tres: borra la **definición** de la plataforma, no
sólo cuentas. A diferencia de `safe.delete` (donde el admin puede
recrear una safe vacía a mano sin perder nada), recrear una plataforma
borrada a mano en el PVWA es tedioso y propenso a error si no se guardó su
configuración — así que, a diferencia de la decisión de Fase 3 de "el
`before` del audit alcanza para reconstruir a mano", acá SÍ conviene un
backup real: CyberArk mismo ofrece "Export Platform" (paquete `.zip`) para
esto.

- **Guardia de uso, igual criterio que `safe.delete`:** el `fetch` escanea
  `client.list_accounts()` (todo el inventario del tenant, ~2000 cuentas,
  única forma: la API Gen2 no filtra por `platformId` server-side) y
  cuenta cuántas tienen `platformId == <la plataforma>`. Si hay ≥ 1, 422
  ("la plataforma tiene N cuenta(s); cámbielas de plataforma o bórrelas
  antes") **sin tocar CyberArk**. Este escaneo sólo corre para esta
  operación puntual (no en cada carga de página).
- **Backup antes de borrar** (mismo orden que `account.delete`: guardar
  ANTES, nunca al revés): `client.export_platform(platform_id)` (bytes del
  `.zip`) → nueva tabla `platform_backups` (migración `0006`): `id,
  tenant_id, platform_id, numeric_id, package (bytea), size_bytes,
  deleted_by, created_at`. Sin cifrado `age` — a diferencia de
  `AccountBackup`, el paquete de una plataforma es política/config del
  CPM, no un secreto de cliente. Se commitea ANTES de llamar
  `delete_target_platform` (si el borrado falla después, el backup igual
  queda).
- `client.delete_target_platform(numeric_id)` — `DELETE
  /API/Platforms/targets/{id}`.
- `domain/platforms.py`: `build_platform_delete_plan(current, _changes)` —
  `current = {"platform": {...}}` (el target-platform, ya validado sin
  cuentas por el `fetch`); diff = la plataforma completa `from -> None`;
  `confirm_phrase = platform_id` (mismo patrón que
  `build_safe_delete_plan`/`build_account_delete_plan`).
- `DELETE /tenants/{id}/platforms/{platform_id}` — `ConfirmedActionIn`,
  RBAC `_approver`.
- `app/api/backups.py` (reutiliza el router existente, no uno nuevo):
  `GET /tenants/{id}/platform-backups` (listar, sin el contenido —
  `tenant_admin+`, mismo techo que los backups de cuentas) + `GET
  .../platform-backups/{id}/download` (devuelve el `.zip` crudo,
  `apiBlob` + `saveBlob` en el frontend — no hay nada que revelar/descifrar,
  a diferencia del reveal de cuentas).
- Frontend: página `AccountBackups.tsx` gana una segunda sección/tab
  "Plataformas borradas" (mismo tenant-scoping y gate `tenant_admin+` que
  ya tiene la página) — o un componente hermano
  `PlatformBackups.tsx` si separar las tablas queda más simple; a definir
  en la implementación.

## Requisitos

1. **Desactivar / activar** — igual que la v1 de esta spec (ver tabla).
2. **Duplicar sin tocar el original.** Dado `WinDomain` (activa, con su
   política), cuando se duplica con `name: "WinDomain-Copia"`, entonces se
   crea una plataforma nueva y `WinDomain` queda intacta (mismo `Active`,
   mismo PSM, etc.) — no hay diff sobre el origen.
3. **Borrar bloqueado si está en uso.** Dada una plataforma con 3 cuentas,
   cuando se pide borrarla, entonces 422 antes de llamar a CyberArk, sin
   backup ni borrado.
4. **Borrar con backup.** Dada una plataforma sin cuentas, cuando se
   confirma el borrado, entonces PAM Web Console guarda el `.zip` de export ANTES de
   llamar a `DELETE`, y el backup queda accesible aunque el `DELETE`
   falle después.
5. **Confirmación reforzada en Activar/Desactivar/Borrar**, no en
   Duplicar (mismo criterio de riesgo que Fase 2 vs Fase 3).

## Tareas

- [x] `client.py`: `activate_target_platform`, `deactivate_target_platform`,
      `duplicate_target_platform`, `delete_target_platform`,
      `export_platform`.
- [x] `domain/platforms.py`: `plan_platform_activation`,
      `build_platform_duplicate_plan`, `build_platform_delete_plan`.
- [x] Alembic `0006`: tabla `platform_backups`.
- [x] `writes.py`: 4 endpoints (`activate`, `deactivate`, `duplicate`,
      `DELETE`) + invalidar `pam:platforms:{tenant_id}` en las 4 (gap que
      ya existía en `platform.psm.update`, se corrige de paso ahí también).
- [x] `backups.py`: `GET /platform-backups` + `.../download`.
- [x] `features.py` **y** `Tenants.tsx`: `platform.activate`,
      `platform.duplicate`, `platform.delete` en las DOS listas (gotcha ya
      documentado).
- [x] Frontend: `PlatformActivateSection.tsx` (activar/desactivar),
      diálogo "Duplicar" (dónde exactamente, a definir en la
      implementación), `SafeDeleteSection`-style "Borrar plataforma",
      sección de backups de plataformas borradas.
- [x] Tests unit: no-op de activar/desactivar, diff de duplicar, 422 de
      borrar con cuentas, backup-antes-que-borrado, `confirm_phrase` en
      activar/desactivar/borrar y su ausencia en duplicar.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] Probado contra Globex (2026-09-24) — `duplicate`/`delete` sobre
      una plataforma de prueba (duplicada y después importada de nuevo,
      ver `platform-import.md`), sin cuentas reales; `activate`/
      `deactivate` también confirmados, las dos direcciones.
- [x] Botón "Actualizar" agregado a `pages/Platforms.tsx` (2026-09-24, a
      pedido de Alex en la misma prueba) — mismo patrón que
      `pages/Sessions.tsx`, `q.refetch()` simple.
