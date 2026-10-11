# Spec — Reporte "Cuentas que no rotan"

**Estado:** cerrada — implementada (2026-09-28), probada contra un tenant real (2026-09-30).
**Reporte:** `non-rotating-accounts` (categoría Cuentas, `heavy=True`).

## Problema

Un cliente pidió un reporte de las cuentas que **no están rotando**, por
cualquier motivo (error del CPM, gestión automática deshabilitada, vencidas,
etc.), y poder personalizarlo al generarlo. Por ejemplo, que no cuente las
cuentas con gestión automática deshabilitada a propósito ni las llaves SSH,
que tampoco rotan y no deben contarse.

Hasta ahora esa información estaba repartida:

- **Compliance** (`/compliance`) muestra un motivo por vez y no exporta.
- **Troubleshooting** cubre sólo errores del CPM y no exporta.
- `account-inventory` exporta todas las cuentas, pero sin filtros ni el
  criterio de vencimiento según la política de la plataforma.

## Alcance

- Un reporte nuevo en Reportes, con tabla y "Exportar CSV" (lo mismo que el
  resto del catálogo).
- Un tipo de parámetro nuevo, `multiselect`, que queda disponible para
  cualquier reporte.
- Fuera de alcance: programarlo o enviarlo por correo (jobs programados
  por reporte), y guardar filtros como plantillas por cliente.

## Requisitos

### R1 — Una fila por cuenta que no rota, con todos sus motivos

Una cuenta entra al reporte si cumple al menos uno de estos motivos:

| Código | Etiqueta | Fuente |
|---|---|---|
| `CpmFailure` | Error de rotación/verificación (CPM) | `secretManagement.status` = Failure |
| `PolicyFailures` | Incumple la política (CyberArk) | savedFilter `PolicyFailures` |
| `ActiveOverdue` | Gestionada pero vencida | `analysis.account_compliance` |
| `DisabledStale` | Gestión deshabilitada y vencida | `analysis.account_compliance` |
| `DisableByUser` | Gestión deshabilitada por un usuario | savedFilter `DisabledPasswordByUser` |
| `DisableByCPM` | Gestión deshabilitada por el CPM | savedFilter `DisabledPasswordByCPM` |
| `Locked` | Bloqueada | savedFilter `Locked` |

"Vencida" usa el mismo criterio que el Dashboard y Compliance: el umbral de
la plataforma de la cuenta (`requirePasswordChangeEveryXDays`) o, si la
plataforma no lo define, `Tenant.compliance_stale_days`.

- *Escenario:* una cuenta con status Failure que además está vencida
  → una sola fila, con "Motivo(s)" = los dos.

### R2 — Filtros al generarlo

- **Motivos** (multiselect): si no se elige ninguno, entran todos.
- **Excluir** (multiselect):
  - `cyberark_internal` (2026-09-28, tras probarlo contra Globex):
    cuentas del propio CyberArk y de sus componentes, que no son
    credenciales del cliente. Se reconocen por el usuario
    (`PluginManagerUser`, `PSMConnect`, `PSMAdminConnect`, `PSMApp_*`,
    `PSMGw_*`, `PSMPApp_*`, `PSMPGw_*`, `PVWAAppUser`, `PVWAGWUser`,
    `PasswordManager*`) o porque están en un safe interno (`System`,
    `VaultInternal`, `Notification Engine`, `PSM*` del producto,
    `PasswordManager*`, `PVWA*`, etc.). Las cuentas de AWS o de sitios web
    del cliente **no** entran acá: son cuentas reales que no rotan.
  - `user_disabled`: gestión automática deshabilitada **por un usuario**.
  - `cpm_disabled`: gestión deshabilitada **por el CPM** tras fallar
    (savedFilter `DisabledPasswordByCPM` o `manualManagementReason` con el
    prefijo `(CPM)`).
  - `ssh_keys`: llaves SSH (`secretType` = `key`).
- **Safe** y **Plataforma**: coincidencia exacta, sin distinguir
  mayúsculas y minúsculas.
- *Escenario:* se excluye "gestión deshabilitada (por un usuario)"
  → una cuenta que el CPM deshabilitó tras agotar reintentos **sigue
  apareciendo**. Es un error de rotación real, no una decisión del
  cliente. Para ocultarla también, hay que excluir `cpm_disabled`.
- La meta del resultado informa cuántas cuentas se excluyeron por cada
  criterio (`excluidas_<código>`).

### R3 — Motivo real del último error

- Parámetro "Motivo del último error": Sí (por defecto) / No.
- Con Sí, se consulta `Activities` **sólo** para las cuentas del resultado
  con una señal de falla del CPM (`CpmFailure` o `DisableByCPM`), no para
  todo el tenant. Columnas: tipo de falla (Cambio/Verificación/
  Reconciliación), fecha y motivo.
- Mismo criterio que `account-inventory-full` (`last_cpm_failure`, función
  compartida): las fallas de sesión PSM no tapan la falla real del CPM.
- Una cuenta cuya consulta de `Activities` falla queda sin ese dato y se
  cuenta en `ultimo_error_sin_datos`, sin abortar el reporte.

### R4 — Seguridad

Sólo lectura, sin secretos. Rol `viewer+`, auditado como
`report.non-rotating-accounts`, igual que el resto del catálogo.

## Diseño técnico

- `app/reports/non_rotating_accounts.py`: `compute_non_rotating` (pura) +
  `attach_last_errors` (pura) + `_run` (4 savedFilters + Accounts +
  Platforms en paralelo, y después el fan-out de `Activities`).
- `heavy=True`: corre como job ARQ (`run_heavy_report`). Con
  "Motivo del último error" = No, el job termina en segundos.
- `Report.tenant_params` (nuevo, en `app/reports/base.py`): configuración
  del tenant que el reporte necesita además del cliente. En este caso es
  `compliance_stale_days`. Se suma a los parámetros sólo al llamar a
  `run()`, tanto en `app.api.reports._compute` como en el job, y no entra
  en la cache key ni en el audit.
- `Param.type = "multiselect"` + `Param.option_labels`: los valores viajan
  como una cadena separada por comas (`?motivos=A,B`) y se leen con
  `base.multi_values()`, que descarta los valores desconocidos.
- `last_cpm_failure()` se extrajo de `account_inventory.py` para
  compartirla. El comportamiento de `account-inventory-full` no cambia.
- Frontend: `components/MultiSelectField.tsx` (DropdownMenu con
  checkboxes, no se cierra al tildar, "Limpiar selección"). `Reports.tsx`
  lo usa para cualquier parámetro `multiselect`. El `select` también
  muestra `option_labels` si vienen.

## Tareas

- [x] `multiselect` + `option_labels` + `tenant_params` en `app/reports/base.py`
- [x] `last_cpm_failure` compartida en `account_inventory.py`
- [x] Reporte `non-rotating-accounts` registrado en `REGISTRY`
- [x] `run_params` en `app.api.reports._compute` y `app.jobs.reports`
- [x] Tests (`tests/unit/test_non_rotating_accounts.py`)
- [x] `MultiSelectField.tsx` + soporte en `Reports.tsx`
- [x] Probar contra un tenant real: confirmar `secretType` = `key` en las
      llaves SSH reales y medir cuánto tarda con el último error incluido
