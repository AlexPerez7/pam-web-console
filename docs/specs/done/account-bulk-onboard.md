# account-bulk-onboard — Onboarding masivo de cuentas por CSV

- **Estado:** en curso — implementada (backend + frontend), pendiente
  probar contra un tenant real. v1 síncrono confirmado con Alex
  (2026-09-15), sin cola de jobs real por ahora; tope de 100 filas
  confirmado.
- **Roadmap:** `docs/PLAN.md §7` Fase 4 — "Onboarding masivo por CSV con
  `dry-run` y diff, job abortable"
- **Fecha:** 2026-09-15
- **Base:** `docs/specs/done/plantillas-alta-cuenta-safe.md`
  (`account.create` de a una, wizard estilo PVWA — esta spec reusa su
  dominio para crear N cuentas de una sola subida), `docs/specs/
  account-bulk-edit.md` (mismo espíritu de "operar sobre varias cuentas a
  la vez", pero ahí es UN valor para todas; acá cada fila es una cuenta
  distinta), `docs/specs/account-credentials-backup.md` (precedente real
  de "operación synchronous de varios minutos sobre N cuentas, sin
  abortar por una fila que falla, con nginx `proxy_read_timeout`
  extendido" — mismo patrón que se reusa acá).

## Problema

Onboardear un cliente nuevo (o una tanda de cuentas de una migración)
significa hoy pasar por el wizard de `account.create` una cuenta a la
vez. Es el caso de uso original de PAM Web Console ("un PVWA pero mejor para lo
repetitivo") sin resolver del todo — Fase 2 resolvió el alta de UNA
cuenta con plantilla, Fase 4 lo lleva a onboarding masivo real.

## Decisión de alcance importante: "job abortable" se pospone

El roadmap original pide un "job abortable" — implica una cola de jobs en
background (ARQ + worker), que **PAM Web Console no tiene today** (`docs/PLAN.md`
ya lo marca como bloqueante para el Batch 4 de Reportes y el Dashboard
v3). Construir esa infraestructura de cero sólo para esta feature es
mucho más caro que el resto de la spec junta.

**Propuesta v1: síncrono, sin cola de jobs**, siguiendo el mismo patrón
ya probado en producción por `account.reveal.bulk`
(`docs/specs/account-credentials-backup.md`): una request HTTP que
procesa todas las filas del CSV una por una, sin abortar si una falla
(reporta el resultado por fila al final), con el timeout de nginx ya
extendido a 600s para `/api/`. **Sin cancelación a mitad de camino** — si
hace falta parar, se corta la conexión (el operador cierra la pestaña) y
las cuentas ya creadas hasta ese punto quedan creadas (mismo
comportamiento que cortar el wizard de a una a mitad de un lote manual).
Si el volumen real termina necesitando job de verdad (cancelable, con
progreso persistente), es la entrada natural para meter ARQ por primera
vez — pero eso es una spec propia, no bloquea esta.

**Tope de filas por CSV:** un valor prudente para que la request
síncrona no se vuelva eterna — propongo **100 filas** (a definir con
Alex). Sube el error explícito si el CSV lo supera, antes de tocar
CyberArk.

## Alcance

**Entra:**
- Subir un `.csv` con una cuenta por fila (mismas columnas que el wizard
  de `account.create`: safe, plataforma, usuario, address, nombre
  opcional, secreto opcional, gestión automática, remote machines,
  propiedades libres de plataforma).
- **Previsualizar** (`dry-run`): parsea + valida cada fila (columnas
  obligatorias presentes, la safe existe, la plataforma existe) sin
  crear nada — muestra una tabla fila por fila: OK (con el diff, igual
  que el wizard de a una) o error (con el motivo).
- **Ejecutar**: crea las cuentas fila por fila. Una fila que falla
  **no aborta el resto** (mismo criterio que `account.reveal.bulk`) — al
  final se ve cuántas se crearon y el detalle de las que fallaron.
- Reusa la feature `account.create` que ya existe (mismo riesgo, misma
  operación repetida N veces) — **sin feature-flag nueva**.
- Botón "Importar CSV" en el toolbar de Cuentas, al lado de "Crear
  cuenta".

**Fuera (de este corte):**
- Job real en background, cancelable, con progreso persistente — ver
  arriba.
- Edición masiva desde el mismo CSV (ya existe `account.bulk-update`,
  pensado para UN valor igual a varias cuentas, no para N valores
  distintos — no se mezcla con esto).
- Plantilla CSV **pre-armada por tipo de sistema** (con las propiedades
  sugeridas de cada plataforma, como el wizard de a una) — sumada una
  plantilla **genérica** (ver abajo), la variante por tipo de sistema
  puede esperar a que se pida.
- Integración con Discovery/Accounts Feed — item separado del roadmap de
  Fase 4.

## Requisitos

1. **Columnas del CSV.** Header obligatorio con al menos `safe_name`,
   `platform_id`, `user_name`, `address`. Opcionales: `name`, `secret`,
   `automatic_management_enabled` (`true`/`false`, default `true`),
   `manual_management_reason` (obligatoria si la anterior es `false`),
   `remote_machines`, `access_restricted_to_remote_machines`
   (`true`/`false`). **Cualquier columna no reconocida** se manda como
   propiedad libre de plataforma (`platformAccountProperties`), mismo
   criterio que "Additional Properties" del wizard de a una.
2. **Previsualizar.** Dado un CSV de 30 filas con 2 con la safe mal
   escrita, al previsualizar se ven 28 OK (con su diff) y 2 con error
   ("la safe 'XYZ' no existe"), sin haber tocado CyberArk para crear
   nada.
3. **Ejecutar con fallas parciales.** Dado el mismo CSV, al ejecutar se
   crean las 28 válidas (una por una, cuentas ya creadas por delante no
   se revierten si una posterior falla) y quedan reportadas las 2
   inválidas — igual que la previsualización, no hace falta repetir el
   motivo porque se re-valida en el momento de crear.
4. **Tope de filas.** Un CSV de 150 filas (> 100) se rechaza antes de
   tocar CyberArk, con un mensaje claro.
5. **Secreto nunca en diff/audit.** Igual que `account.create` de a una
   — si la fila trae `secret`, el diff muestra "(definida)", nunca el
   valor.
6. **RBAC / feature.** Sin `account.create` habilitada para el tenant, o
   sin rol `operator+`, no se puede ni previsualizar ni ejecutar.

## Diseño técnico

### CSV parsing (`app/domain/accounts.py`)

- `parse_bulk_csv(content: str) -> list[dict]` — puro, usa el `csv`
  estándar de Python (sin dependencia nueva). Devuelve una lista de
  `changes`-dicts compatibles con `AccountCreateChanges` (mismo shape
  que ya arma el wizard de a una), con un `_row` (número de línea, para
  reportar errores) agregado a cada uno. Columnas no reconocidas pasan a
  `properties`. Valida tipos básicos (`true`/`false` para los booleanos)
  — errores de parseo son por fila, no abortan el CSV entero.

### API (`app/api/writes.py`, multipart — mismo patrón que `platform.import`)

- `POST /tenants/{id}/accounts/bulk-create/preview` — `UploadFile` CSV.
  Por cada fila: valida columnas obligatorias, después
  `client.get_safe`/`client.get_platform_details` (cacheados por
  `safe_name`/`platform_id` dentro del mismo request — un CSV real repite
  la misma safe/plataforma en muchas filas) y arma el diff con
  `plan_account_create`, igual que hace `fetch` en `account_create` hoy.
  Devuelve `{rows: [{row, ok, diff?, target?, error?}], valid, invalid}`.
  Feature `account.create`, RBAC `operator+`. Auditado
  `account.create.bulk.preview` (sólo el resumen, no cada fila).
- `POST /tenants/{id}/accounts/bulk-create` — `UploadFile` CSV +
  `reason` (form field, mínimo 8 caracteres, mismo mínimo que
  `MIN_REASON_LEN`). Re-parsea y re-valida cada fila (no confía en la
  preview: pudo pasar tiempo) y crea las válidas una por una
  (`client.create_account`, mismo `retry_safe=False` que el alta
  individual — no idempotente). Circuit breaker: > 100 filas → 409 antes
  de tocar CyberArk. Devuelve `{created: N, failed: [{row, error}],
  total}`. Auditado **una sola vez** `account.create.bulk` con el
  resumen agregado (created/failed/total/reason) — no un audit por
  cuenta (igual criterio que `account.reveal.bulk`, para no inflar la
  tabla de audit con cientos de filas de una sola operación humana).
- **No pasa por `writeop.run_write`** — el modelo de una sola operación/
  un solo objeto de `run_write` no encaja con N objetos con resultados
  mixtos. Replica a mano lo que aplica: gating por feature, `reason`
  obligatoria para ejecutar, circuit breaker, auditoría (mismo criterio
  ya usado por `credentials_backup.py`).

### Frontend

- `components/AccountBulkCreateDialog.tsx` (nuevo): botón "Importar CSV"
  en el toolbar de `pages/Accounts.tsx` (al lado de
  `AccountCreateWizard`), gateado igual que ese (`account.create` +
  `operator+`).
  - "Descargar plantilla" (agregado a pedido de Alex, 2026-09-15) —
    genera el CSV **client-side** (header + una fila de ejemplo,
    `saveBlob()` que ya existe), sin ida y vuelta al backend: las
    columnas son estáticas, no dependen del tenant.
  - Paso 1: elegir archivo `.csv` (`apiForm()`, ya existe de
    `platform.import`) → "Previsualizar".
  - Paso 2: tabla de resultados (fila / OK-error / diff resumido o
    motivo del error), contador "28 de 30 válidas".
  - Paso 3: razón obligatoria + "Confirmar" → ejecuta → resumen final
    (creadas / fallidas con motivo).
- Sin cambios en `Tenants.tsx` (reusa `account.create`, ninguna feature
  nueva que togglear).

## Tareas

- [x] `app/domain/accounts.py`: `parse_bulk_csv` + tests unit (columnas
      faltantes, tipos inválidos, columnas extra → properties).
- [x] `app/api/writes.py`: `POST /accounts/bulk-create/preview` +
      `POST /accounts/bulk-create` (multipart, `python-multipart` ya es
      dependencia desde `platform.import`). Concurrencia con
      `asyncio.gather` (decisión #3 más abajo, resuelta).
- [x] Tests unit backend (`tests/unit/test_account_bulk_create.py`, 18
      tests — parseo del CSV, preview con filas mixtas OK/error, ejecución
      con fallas parciales, tope de filas, feature off, RBAC).
- [x] `components/AccountBulkCreateDialog.tsx` + botón "Importar CSV" en
      `pages/Accounts.tsx` (al lado de "Crear cuenta"), con "Descargar
      plantilla" (client-side, sin backend).
- [x] `ruff` + `ruff format` + `mypy` + `pytest` (backend) + `lint` + `tsc`
      + `build` (frontend) verdes (2026-09-15).
- [x] `CLAUDE.md` + `docs/PLAN.md §7` (Fase 4).
- [x] Probado contra Globex (2026-09-18): CSV chico con una fila
      inválida a propósito, preview y ejecución confirmados. Spec cerrada.

## Decisiones abiertas

1. ~~¿100 filas es el tope correcto?~~ **Confirmado con Alex (2026-09-15):
   100.**
2. **¿Plantilla CSV descargable?** Quedó fuera de este corte; si el
   primer uso real muestra que arman el CSV a mano y se equivocan de
   columnas seguido, vale la pena sumarla rápido.
3. ~~Concurrencia dentro de la request.~~ **Resuelto:** paralelo con
   `asyncio.gather`, tanto para resolver safes/plataformas únicas
   (deduplicadas primero, para no repetir el mismo GET si el CSV reusa la
   misma safe en varias filas) como para las creaciones — cada fila es
   independiente, el orden de "fila N falló" no depende del orden real de
   ejecución (se ordena por número de fila antes de responder). El
   semáforo de concurrencia de `CyberArkClient` (`cyberark_max_concurrency`,
   8 por defecto) throttlea automáticamente, sin código extra acá.
