# audit-page

- **Estado:** cerrada — Tanda 1 verificada en el server el 2026-09-29;
  tandas 2 y 3 el 2026-10-07 en `pam-standby`.
- **Roadmap:** extra (mejora de la página Auditoría, `pages/Audit.tsx`)
- **Fecha:** 2026-09-29

## Problema

PAM Web Console audita todas las acciones, incluidas las lecturas (cada consulta del
detalle, del dashboard, de la actividad). En la página `/audit` lo
importante —una contraseña revelada, un borrado, un acceso denegado— queda
enterrado entre cientos de consultas. Además no se puede filtrar por
operador, fecha ni resultado; el filtro de acción exige conocer el código
interno (`account.reveal`); hay un tope de 500 registros sin paginación; y
el detalle es JSON crudo con el target como ID (`46_1`). Tampoco hay forma
de entregarle a un cliente evidencia de quién tocó sus cuentas.

## Alcance

Entra, en tres tandas que se pueden entregar por separado:

1. **Señal sobre ruido:** lecturas ocultas por defecto, resumen de eventos
   sensibles, filtros nuevos y paginación.
2. **Legibilidad:** acciones en español, target con nombre, detalle con
   antes/después, operador y target clickeables.
3. **Evidencia:** exportar lo filtrado (CSV/JSON) y una verificación de
   integridad más informativa.

Queda afuera:

- Alertas activas (correo, notificaciones) ante eventos sensibles — spec
  aparte, necesita jobs y un canal de salida.
- Cambios al modelo `AuditLog` o a la cadena de hashes: todo sale de la
  tabla actual, **sin migración**. `_payload()`/`verify_chain()` no se
  tocan.
- Retención/archivado de registros viejos.
- El gap conocido de las filas sin `tenant_id` (base de conocimiento) que
  un operador sin `platform_admin` no ve: se mantiene el criterio de
  alcance actual (`rbac.audit_tenant_ids`).

## Requisitos

### Tanda 1 — señal sobre ruido

**R1. Clasificación de acciones.** Cada `action` se clasifica en un tipo:
`consulta` (lectura sin secretos: `*.list`, `*.get`, `*.view`,
`*.search`, `*.activities`, `*.summary`, `*.members`, `report.*`,
`dashboard.view`, `auth.refresh`, …), `cambio` (escrituras contra CyberArk
o contra la configuración de PAM Web Console), `sensible` (expone un secreto o
contenido sensible: `account.reveal`, `account.reveal.history`,
`account.reveal.bulk`, `account.backup.reveal`, `recording.play*`,
`platform.backup.download`) y `acceso` (`auth.login*`, `auth.logout`,
`auth.refresh.reuse`, `operator.*`). Una acción desconocida se trata como
`cambio` (se muestra; nunca se oculta algo por no conocerlo).
- Dado un registro `account.list`, cuando se consulta `/audit` sin
  `include_reads`, entonces no aparece.
- Dado un registro `foo.bar` (acción nueva sin clasificar), entonces
  aparece siempre.

**R2. Lecturas ocultas por defecto.** La página arranca mostrando sólo
`cambio`, `sensible` y `acceso`, más cualquier registro con resultado
distinto de `ok` (una lectura denegada o con error sí interesa).
Interruptor "Incluir consultas" para verlas.
- Dado un `account.get` con `result="denied"`, entonces aparece aunque las
  consultas estén ocultas.

**R3. Resumen de eventos sensibles.** Tarjetas arriba de la tabla con los
conteos del período elegido (24 h por defecto): contraseñas reveladas,
grabaciones descargadas, backups de credenciales, accesos denegados,
errores, rollbacks fallidos (`*.rollback_failed`), inicios de sesión
fallidos y aprobaciones pendientes. Clic en una tarjeta aplica el filtro
correspondiente. Los conteos respetan el alcance de tenants del operador.
- Dado un operador `tenant_admin` sólo de Globex, entonces los conteos
  no incluyen eventos de otros tenants.

**R4. Filtros nuevos.** Además de tenant/target:
- operador (lista de los operadores presentes en el alcance);
- rango de fechas (desde/hasta) con atajos 24 h / 7 días / 30 días;
- resultado (`ok`, `denied`, `error`, `pending_approval`, `locked`);
- "Incluir previsualizaciones" (dry-run), apagado por defecto;
- acción por **categoría** (Cuentas, Safes, Plataformas, Contraseñas y
  secretos, Sesiones y grabaciones, Identity, Alero, Reportes, Acceso a
  PAM Web Console, Administración) en lugar de texto libre. Se conserva la búsqueda
  por código exacto para usos avanzados.

Los filtros viven en la URL (`?operator=&since=&result=…`) para poder
compartir un enlace a una vista filtrada.

**R5. Paginación.** Sin el tope actual de 500: paginación por cursor
(`before_id`), 100 por página, botón "Cargar más". Cursor por `id` y no por
offset: la tabla es append-only y el `id` es monótono, así que una página
nunca se corre aunque entren registros nuevos mientras se navega.

### Tanda 2 — legibilidad

**R6. Acciones en español.** "Reveló la contraseña" en lugar de
`account.reveal`, con el código visible en el tooltip. Catálogo único en el
backend (junto con la clasificación de R1), expuesto al frontend.

**R7. Target con nombre.** Cuando el `detail` del registro ya trae un
nombre legible (`name`, `userName@address`, `safeName`, `platform_id`), la
columna lo muestra; si no, el ID. **No** se consulta CyberArk para
resolver nombres (sería una llamada por fila y el objeto puede ya no
existir).

**R8. Detalle legible.** El panel de un registro muestra, en este orden: la
razón (`reason`) destacada; el antes/después como tabla si hay `diff` o
`before`/`after`; el efecto (`effect`); las cuentas que fallaron si hay
`failed`; la aprobación (solicitante, aprobador, motivo) si hubo; el
`request_id` con un enlace "ver todo el mismo request". El JSON crudo queda
al final, colapsado.

**R9. Navegación.** Clic en un operador o en un target filtra la tabla por
ese valor.

### Tanda 3 — evidencia

**R10. Exportar.** "Exportar" descarga lo filtrado (mismos filtros, sin
paginación, tope de 50.000 filas) en CSV o JSON. El archivo incluye al
inicio el resultado de la verificación de integridad y el rango de `id`
exportado. La exportación se audita (`audit.export`, con los filtros en el
`detail`). Mismo RBAC que leer el audit (`require_audit_reader`).
- Dado un filtro tenant=globex, desde=2026-09-01, entonces el CSV sólo
  tiene filas de ese tenant y período, y la primera línea dice si la cadena
  está íntegra.

**R11. Verificación de integridad más clara.** Además de íntegra/rota:
cuántos registros se verificaron, el rango de `id`, cuándo se verificó y,
si se rompe, el registro exacto (con un enlace para abrirlo).

## Diseño técnico

**Backend**

- `app/core/audit_catalog.py` (nuevo, puro): `classify(action) -> kind`,
  `label(action) -> str`, `category(action) -> str`, `CATEGORIES`. Reglas
  por prefijo/sufijo + tabla explícita para las excepciones. Tests
  unitarios que recorren todas las `action=` del código (grep en el test)
  para que ninguna acción nueva quede sin etiqueta sin que se note.
- `GET /audit` gana: `operator`, `since`, `until` (ISO 8601), `result`
  (repetible), `category`, `include_reads` (default `false`),
  `include_dry_run` (default `false`), `before_id`. `limit` pasa a 1–200
  (default 100). Responde `{"items": [...], "next_before_id": int|null}`.
  **Cambio de contrato:** hoy devuelve una lista; lo consumen `Audit.tsx`
  y `AccountDetailPanel.tsx` (vía `GET /tenants/{id}/audit`, que **no**
  cambia). Se actualiza `Audit.tsx` en el mismo cambio.
- Cada fila gana `kind`, `category`, `label` y `target_label` (R7),
  calculados al serializar.
- `GET /audit/summary?since=&until=&tenant_id=` → conteos de R3 (una
  consulta `GROUP BY action, result` acotada al alcance).
- `GET /audit/operators` → operadores distintos en el alcance (para el
  filtro).
- `GET /audit/export?format=csv|json&…filtros` (R10), usando
  `app.core.csv_export`. Auditado `audit.export`.
- `GET /audit/verify` suma `checked`, `first_id`, `last_id`,
  `verified_at` (R11); `verify_chain` se extiende para devolver el conteo
  sin cambiar cómo calcula el hash.
- Filtro de lecturas en SQL: `action` se clasifica en Python, así que el
  endpoint arma la lista de acciones de tipo `consulta` presentes
  (`SELECT DISTINCT action`, barato con el índice existente) y la excluye
  con `NOT IN`, sumando `OR result != 'ok'` (R2).
- Índices: `ts` y `action` ya existen; `operator` y `tenant_id` también.
  No hace falta migración.

**Frontend**

- `pages/Audit.tsx` se reescribe: resumen (R3) → barra de filtros (R4,
  estado en la URL con `useSearchParams`) → `DataTable` con "Cargar más"
  (R5) → panel de detalle (`AuditEntryPanel.tsx`, nuevo, R8).
- `ExportButton` del audit (R10) con el mismo patrón `apiBlob` →
  `saveBlob` que `ExportCsvButton.tsx`.
- `lib/types.ts`: `AuditEntry` gana `kind`/`category`/`label`/
  `target_label`; tipos nuevos para la página y el resumen.

**Principios**

- Sólo lectura: no toca CyberArk ni modifica `audit_log`.
- Alcance por tenant: toda consulta nueva pasa por
  `rbac.audit_tenant_ids`, igual que `GET /audit` hoy.
- Todo auditado: `audit.export` se registra; consultar la página no suma
  un registro por cada filtro (hoy tampoco lo hace, y agregarlo generaría
  exactamente el ruido que esta spec quiere sacar).

## Tareas

### Tanda 1
- [x] `app/core/audit_catalog.py` + tests (incluido el que recorre todas
      las acciones del código, `test_audit_catalog.py`).
- [x] `GET /audit`: filtros nuevos, `include_reads`, cursor, respuesta
      `{items, next_before_id}` + tests (alcance por tenant incluido,
      `test_audit_page.py`). De paso, `action` pasa a repetible (como
      `result`) para que las tarjetas del resumen puedan filtrar por más
      de una acción a la vez.
- [x] `GET /audit/summary` y `GET /audit/operators` + tests.
- [x] `Audit.tsx`: resumen (tarjetas clickeables), filtros en la URL,
      "Incluir consultas", "Incluir previsualizaciones", "Cargar más".
      `pending_approvals` no sale de `audit_log`: la tarjeta no filtra,
      queda para linkear a `/approvals` en un fast-follow chico.

### Tanda 2
- [x] Etiquetas en español (`audit_catalog.label()`, con test que fuerza
      traducir toda `action` estática nueva) y `target_label` en cada fila
      (`_target_label()` en `app/api/audit.py`; hoy sólo un puñado de
      acciones dejan un nombre legible en `detail` — el resto cae al ID de
      siempre, ningún cambio de comportamiento).
- [x] `AuditEntryPanel.tsx` con razón, error, antes/después (tabla armada
      desde `diff` o desde `before`/`after`), efecto, cuentas fallidas,
      aprobación, `request_id` ("ver todo el mismo request" — nuevo filtro
      `GET /audit?request_id=`, que de paso muestra todo sin el filtro de
      ruido de Tanda 1) y JSON colapsado.
- [x] Clic en operador/target filtra (en la tabla y en el panel).

### Tanda 3
- [x] `GET /audit/export` (CSV/JSON, tope 50.000, auditado `audit.export`
      con los filtros en el `detail`) + tests. Filtros compartidos con
      `GET /audit` vía `_filtered_stmt()` (refactor sin cambio de
      comportamiento en `recent()`).
- [x] `GET /audit/verify` ampliado (`checked`/`first_id`/`last_id`/
      `verified_at`, `app.core.audit.ChainVerification`) + tests
      (`manage.py verify-audit` y `test_audit.py` actualizados al nuevo
      tipo de retorno).
- [x] Botón "Exportar" (CSV/JSON) y verificación ampliada en la página,
      con un link para abrir el registro roto si la cadena está rota
      (`openById`, vía `before_id=id+1&limit=1`, sin endpoint nuevo).

### Cierre
- [x] Backend (ruff+format+mypy+pytest) y frontend (lint+tsc+build) verdes.
- [x] Probar en el server con datos reales; mover la spec a
      `docs/specs/done/` y actualizar `CLAUDE.md`. 2026-10-07: acciones en
      español con tooltip, target legible, detalle, filtros por clic y
      verificación en la página probados por Alex; export CSV verificado
      (`audit.export` registrado, el archivo empieza con "Cadena de hashes:
      ROTA a partir del registro #447", lo esperado por el fork histórico).
      El JSON no se descargó en la prueba (mismo endpoint, cubierto por
      tests). Al probar se corrigió la atribución: los `auth.refresh`
      rechazados quedaban con operador "?" y varios comandos de consola
      como "system" o sin auditar (`18eb138`).
