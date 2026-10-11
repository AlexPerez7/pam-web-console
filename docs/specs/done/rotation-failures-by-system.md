# rotation-failures-by-system — Excel de cuentas con problemas de rotación por tipo de sistema

- **Estado:** hecha (aprobada e implementada el 2026-10-07; probada por Alex contra Globex el mismo día)
- **Roadmap:** `docs/PLAN.md §7` — Reportes (transversal, read-only)
- **Fecha:** 2026-10-07

## Problema

Un cliente pidió un Excel con las cuentas que tienen **problemas de
rotación**, separado por tipo de sistema: una pestaña con las cuentas de
plataformas Windows, otra con las Unix, etc. También quiere una **Portada**
con el porcentaje de cuentas con problemas de cada sistema sobre el total
de ese sistema (por ejemplo, "Windows: 40 % — 120 de 300").

`non-rotating-accounts` ya calcula qué cuentas no rotan y por qué, pero
devuelve una sola tabla en CSV, sin el total por sistema (el denominador
del porcentaje) y sin un formato que se pueda entregar al cliente.

## Alcance

**Entra:**

- Un reporte nuevo en el catálogo de Reportes, `rotation-failures-by-system`
  (categoría Cuentas, `heavy=True`, igual que `non-rotating-accounts`).
- Exportación a **Excel (`.xlsx`)** con:
  - **Portada:** tenant, fecha de generación, criterio aplicado y una tabla
    por tipo de sistema (total de cuentas, cuentas con problemas, %), más
    una fila de total general.
  - **Una pestaña por tipo de sistema** que tenga al menos una cuenta con
    problemas, con el detalle de esas cuentas.
- En pantalla (Reportes), la misma tabla de detalle que el resto del
  catálogo y una columna "Sistema".
- `format=xlsx` en el export genérico de reportes, disponible sólo para los
  reportes que declaran un generador de Excel. Hoy, sólo este.

**Fuera:**

- Gráficos dentro del Excel (se pueden agregar después).
- Envío automático o programado al cliente.
- Logo o colores del cliente en la Portada: se usa un formato neutro con la
  marca de PAM Web Console.
- Cambiar `non-rotating-accounts`: sigue igual.

## Decisiones propuestas (confirmar al aprobar)

| Tema | Propuesta | Por qué |
|---|---|---|
| ¿Qué es "con problemas de rotación"? | **Errores + vencidas** (decisión de Alex): error del CPM (`secretManagement.status` = Failure), deshabilitada por el CPM tras fallar (`DisabledPasswordByCPM` / `manualManagementReason` `(CPM)…`) o **gestionada pero vencida** (`ActiveOverdue`: superó el plazo de rotación de su plataforma, o `Tenant.compliance_stale_days` si no define uno — mismo cálculo que el Dashboard) | Son las que tienen un problema real de rotación. Una cuenta deshabilitada a mano por el cliente no es un error de rotación. |
| Tipo de sistema | `general.systemType` de la plataforma de la cuenta (`Windows`, `*NIX`, `Database`, `Network Device`, `Website`, `Cloud Service`, `Application`, …). Si la plataforma no aparece → "Sin clasificar" | Es la clasificación que ya usa CyberArk (y el ITR). No hay que mantener un mapeo propio por cliente. |
| Denominador del % | Todas las cuentas del tipo de sistema, **sin** las cuentas internas de CyberArk ni las llaves SSH | Las internas (PSMConnect, PluginManagerUser…) no son credenciales del cliente, y las llaves SSH no rotan por diseño: inflarían o distorsionarían el total. |
| Exclusiones | Cuentas internas de CyberArk y llaves SSH excluidas siempre del numerador y del denominador. Las deshabilitadas por un usuario cuentan en el total pero nunca como "con problemas" | El reporte va al cliente: tiene que mostrar sus cuentas reales. |
| Desglose en la Portada | Por sistema: total, con error del CPM, vencidas (sin error), total con problemas y % | El cliente ve si el problema es de errores o de cuentas que el CPM no está rotando a tiempo. Una cuenta con error y vencida cuenta como error. |
| Motivo del último error | Incluido (columna "Motivo del último error", desde `Activities`), con el mismo parámetro Sí/No que `non-rotating-accounts` | Es lo que el cliente necesita para corregir. Sólo se consulta para las cuentas con problemas. |
| Orden de las pestañas | Por cantidad de cuentas con problemas, de mayor a menor | Lo más grave primero. |
| Nombre de las pestañas | El `systemType` tal cual, saneado para Excel (sin `/ \ ? * [ ] :`, máx. 31 caracteres; `*NIX` → `Unix`) | Excel no acepta esos caracteres en el nombre de una hoja. |

## Requisitos

1. **Pestaña por sistema.** Dado un tenant con 300 cuentas Windows (120 con
   error del CPM) y 50 Unix (ninguna con error), cuando se exporta el Excel,
   entonces hay una pestaña "Windows" con 120 filas y **no** hay pestaña
   "Unix".
2. **Portada con porcentaje.** En el mismo caso, la Portada muestra
   "Windows — 300 — 120 — 40,0 %" y "Unix — 50 — 0 — 0,0 %" (el sistema
   aparece aunque no tenga problemas, para que el cliente vea que está
   bien), más el total general "350 — 120 — 34,3 %".
3. **Criterio.** Una cuenta deshabilitada por un usuario y vencida, sin
   error del CPM, no aparece. Una cuenta deshabilitada por el CPM tras
   agotar reintentos sí aparece, y una gestionada que superó el plazo de su
   plataforma sin error también (como "Vencida").
4. **Exclusiones.** Las cuentas internas de CyberArk y las llaves SSH no
   cuentan en el total ni en las pestañas. La Portada informa cuántas se
   dejaron fuera.
5. **Sin clasificar.** Una cuenta cuya plataforma no está en
   `/API/Platforms` va a "Sin clasificar" y se cuenta igual.
6. **Seguridad.** Sólo lectura, sin secretos (ni contraseñas ni valores de
   llaves). Rol `viewer+`; generarlo se audita como
   `report.rotation-failures-by-system` (igual que el resto del catálogo) y
   la descarga del Excel queda en el audit log con `format=xlsx`.
7. **Formato inválido.** `format=xlsx` en un reporte sin generador de Excel
   → `422`.

## Diseño técnico

### Backend

- `app/reports/rotation_failures_by_system.py`:
  - Reutiliza de `non_rotating_accounts.py`: `is_cyberark_internal`,
    `_is_cpm_failure`, `_is_cpm_disabled` (pasan a ser públicas),
    `needs_last_error` / `attach_last_errors` y `all_account_activities`.
  - `compute_by_system(accounts, platforms, cpm_disabled_ids, *,
    default_stale_days)` (pura): devuelve las filas (con `systemType`) y
    el resumen por sistema `{system: {total, failing}}` + excluidas. Va en
    `ReportResult.meta["by_system"]` para que viaje con el resultado cacheado
    del job.
  - `_run`: `list_accounts`, `list_platforms` y el savedFilter
    `DisabledPasswordByCPM` en paralelo; después, el último error para las
    filas.
- `app/reports/xlsx.py` (nuevo): `build_rotation_failures_xlsx(data,
  tenant_name)` con openpyxl (ya es dependencia): Portada con formato
  (encabezado, % con formato de porcentaje, ancho de columnas), una hoja por
  sistema con encabezados en negrita, filtros automáticos y paneles
  inmovilizados.
- `app/reports/base.py`: `Report.xlsx: Callable[[dict, str], bytes] | None`
  y `"formats"` en `to_dict()` (`["csv", "json"]` + `"xlsx"` si existe).
- `app/api/reports.py` `export_report`: acepta `format=xlsx` cuando el
  reporte lo declara; reutiliza el resultado cacheado del job (igual que el
  CSV) y lo arma en un hilo (`asyncio.to_thread`).

### Frontend

- `Reports.tsx`: botón **"Exportar Excel"** junto a "Exportar CSV" cuando
  `report.formats` incluye `xlsx`.
- La tabla en pantalla agrega la columna "Sistema". La Portada sólo está en
  el Excel; en pantalla, la meta muestra el resumen por sistema.

### Riesgos

- **`systemType` vacío o inesperado** en plataformas de algunos tenants:
  cae en "Sin clasificar"; se confirma con Globex al probar.
- **Tiempo:** el último error es un request por cuenta con problemas (igual
  que `non-rotating-accounts`, ya medido contra un tenant real). Con el
  parámetro en "No" termina en segundos.

## Tareas

- [x] Aprobación de la spec (decisiones de la tabla).
- [x] Helpers públicos en `non_rotating_accounts.py` sin cambiar su
      comportamiento.
- [x] `rotation_failures_by_system.py` + registro en `REGISTRY` + audit
      catalog.
- [x] `xlsx.py` + `Report.xlsx` + `format=xlsx` en el export.
- [x] Tests: agrupación y %, criterio (CPM vs. usuario), exclusiones, "Sin
      clasificar", nombres de hoja, Excel abre con openpyxl y tiene las
      hojas esperadas, `422` en otro reporte.
- [x] Frontend: botón "Exportar Excel" + columna Sistema.
- [x] Probar contra Globex y revisar el Excel con el cliente. Probado por
      Alex el 2026-10-07: funciona bien.
- [x] Docs: `ARCHITECTURE.md`, `PLAN.md` §7, `docs/plan/reportes.md`.
