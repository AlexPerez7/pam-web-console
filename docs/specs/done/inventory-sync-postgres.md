# inventory-sync-postgres — filtro por columna server-side en Cuentas

- **Estado:** hecha — probada contra Globex (2026-09-16)
- **Roadmap:** `docs/PLAN.md §7` Fase 1, ítem pendiente "filtro por columna en
  modo servidor" (ver "Filtros server-side de Cuentas" en Fase 1 y
  `docs/specs/done/filtros-server-cuentas.md`)
- **Fecha:** 2026-09-16

## Problema

La grilla de Cuentas corre en **modo servidor** (paginación/orden/búsqueda
contra CyberArk, necesario porque un tenant puede tener miles de cuentas) —
a diferencia de Plataformas/Usuarios/Grupos/Sesiones, que corren en modo
cliente y sí tienen filtro por columna (`DataTable.tsx`:
`enableColumnFilter: !server && ...` lo deshabilita explícitamente en modo
servidor, porque TanStack Table sólo puede filtrar filas que ya están
cargadas en el navegador).

La API Gen2 de CyberArk (`/API/Accounts`) sólo soporta filtrar server-side
por `safeName` (`eq`) y `modificationTime` (`gt`) vía el parámetro `filter`
— confirmado en `client.accounts_page`/`accounts_filter`. El resto de
columnas visibles en la grilla (Plataforma, Usuario, Address, estado de
gestión) no tiene equivalente en la API real: no hay forma de pedirle a
CyberArk "cuentas con `platformId=X`" server-side.

Safes tiene el mismo límite en teoría, pero ya está resuelto de otra forma
(`safes_rows_cached`: todas las safes de un tenant de una vez, cacheadas en
Redis — cardinalidad manejable) y no entra en esta spec.

## Enfoque elegido: copia local en Postgres, sync on-demand con TTL

Mismo patrón que ya usa el Dashboard (`PAM_DASHBOARD_CACHE_TTL`) y
`safes_rows_cached`/`platforms_rows_cached`: al pedir la grilla filtrada, si
la copia local tiene más de `PAM_ACCOUNTS_SYNC_TTL` segundos, se refresca
completa (síncrono, dentro del mismo request) antes de responder. Se
descartan dos alternativas:

- **Cron/systemd timer periódico**: es, literalmente, el ítem "sync nocturno
  de inventario" que `docs/PLAN.md` Fase 5 ya tiene anotado como idea futura
  de "Jobs programados". Adelantarlo acá mezclaría el prerequisito chico de
  esta spec (que la grilla de Cuentas pueda filtrar por columna) con una
  pieza de infraestructura de alcance mayor (jobs programados en general,
  con su propia decisión de scheduler/alertas de falla). Queda como mejora
  futura compatible: el mecanismo de sync (`sync_accounts`) es el mismo,
  sólo cambia quién lo dispara.
- **Botón "Actualizar inventario" manual**: más explícito, pero deja la
  puerta abierta a que un operador filtre contra datos viejos sin saberlo si
  nadie lo aprieta. El TTL automático da una cota de frescura sin que el
  operador tenga que pensarlo, igual que ya pasa con el Dashboard.

## Alcance

**Entra:**
- Tabla nueva `account_snapshots` (Postgres) — copia local de metadata de
  cuentas por tenant, con columnas indexadas para lo que hoy no se puede
  filtrar server-side: `platform_id`, `user_name`, `address`. Guarda también
  `safe_name`/`modification_time` (redundante con lo que ya filtra CyberArk,
  pero simplifica combinar filtros en una sola query local) + el objeto
  completo en una columna `raw` (JSON) para no perder nada y no tener que
  migrar el schema cada vez que se quiera exponer una columna nueva.
- `app/core/inventory_sync.py`: `sync_accounts(tenant_id, client, session)`
  — pagina **todas** las cuentas del tenant (loop sobre `client.accounts_page`
  sin filtro hasta agotar `count`), upsert masivo, y borra del snapshot las
  filas cuyo `account_id` no vino en esta pasada (cuentas borradas en
  CyberArk desaparecen de la copia local en la siguiente sync).
- El endpoint **existente** `GET /tenants/{id}/accounts` gana tres params
  opcionales nuevos: `platform_id`, `user_name`, `address`. Si **ninguno**
  está presente, el comportamiento es exactamente el de hoy (búsqueda/orden/
  `safe_name`/`modified_after` en vivo contra CyberArk — cero riesgo para lo
  que ya funciona). Si **alguno** está presente, la query se resuelve contra
  `account_snapshots` (que también sabe filtrar por `search`/`safe_name`, así
  que combina todo en una query local), disparando `sync_accounts` primero
  si la copia está vencida.
- `DataTable`/`ServerMode` (frontend): hoy `enableColumnFilter` se
  deshabilita en bloque para toda grilla en modo servidor. Pasa a ser
  configurable por columna (`Column.serverFilterable?: boolean`) — sólo
  Plataforma/Usuario/Address lo declaran en `Accounts.tsx`. El filtro de esas
  columnas viaja al backend vía el mecanismo `extra` que `useServerGrid` ya
  tiene (mismo patrón que `safe_name` hoy).
- Timestamp "Actualizado hace Xs" en la grilla cuando está sirviendo desde el
  snapshot (mismo criterio de transparencia que el Dashboard con su caché).

**Fuera (a propósito):**
- Sync de Safes/Platforms/Users/Groups — no tienen este problema hoy.
- Cron/scheduler periódico — ver arriba, Fase 5.
- Cualquier escritura leyendo del snapshot — `account.update`/`delete`/etc.
  siguen 100% en vivo contra CyberArk vía `writeop.run_write`. El snapshot es
  sólo lectura/filtrado de grilla, nunca la fuente de un `fetch` de escritura.
- Export CSV / búsqueda cross-tenant usando el snapshot — quedan como están
  (en vivo). Spec futura si hace falta.
- Secretos: el snapshot no guarda ni expone nada que pase por
  `retrieve_password` — sólo metadata ya visible hoy en el listado.

## Requisitos

1. **Filtrar por columna en Cuentas.** Dado un tenant con >100 cuentas, al
   escribir en el filtro de la columna Plataforma, la grilla muestra sólo
   las cuentas con ese `platformId`, sin traer todo el inventario al
   navegador ni perder la paginación.
2. **Frescura acotada y visible.** Los datos servidos desde el snapshot
   tienen como máximo `PAM_ACCOUNTS_SYNC_TTL` segundos de antigüedad, con
   un indicador en la UI (mismo patrón que el Dashboard).
3. **Degradación si CyberArk está lento/caído durante una sync.** Si
   `sync_accounts` falla (timeout, error de CyberArk), se sirve la copia
   vieja igual (si existe) con un aviso, en vez de romper la grilla. Si no
   hay copia previa (primera vez), error claro — mismo criterio que hoy.
4. **Sin filtros nuevos, cero cambio de comportamiento.** Una request a
   `/accounts` sin `platform_id`/`user_name`/`address` se resuelve exactamente
   igual que hoy (en vivo contra CyberArk) — este endpoint no puede volverse
   más lento ni menos fresco para el caso ya soportado.
5. **Todo sigue auditado.** La consulta del operador sigue auditando
   `account.list` igual que hoy (con `detail.filters` incluyendo los campos
   nuevos cuando se usan). El proceso de sync en sí audita una acción
   separada `account.sync` (atribuido al operador cuya request lo disparó,
   `result=ok`/`error`, `detail={count, duration_ms}`).
6. **Aislamiento por tenant.** `account_snapshots` tiene `tenant_id` en la PK
   compuesta y en todos los índices — ninguna query cruza tenants.
7. **Selección de filas para bulk-edit no se rompe.** La selección por id
   real de `DataTable` (ya sobrevive a la paginación server-side) sigue
   funcionando igual sin importar si la página vino del snapshot o de
   CyberArk en vivo — el `id` es el mismo en ambos casos.

## Diseño técnico

### `app/models/account_snapshot.py` (nuevo)

```python
class AccountSnapshot(Base, Mixin):
    __tablename__ = "account_snapshots"
    tenant_id: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(primary_key=True)
    platform_id: Mapped[str | None] = mapped_column(index=True)
    user_name: Mapped[str | None] = mapped_column(index=True)
    address: Mapped[str | None] = mapped_column(index=True)
    safe_name: Mapped[str | None]
    modification_time: Mapped[int | None]
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime]
```

Índices compuestos `(tenant_id, platform_id)`, `(tenant_id, user_name)`,
`(tenant_id, address)` — los tres campos nuevos que se pueden filtrar.

### Migración `0009_account_snapshots.py`

Tabla + índices, `downgrade` que la dropea. Sin trigger especial (a
diferencia de `audit_log`, esto no es append-only — se pisa en cada sync).

### `app/core/inventory_sync.py` (nuevo)

- `sync_accounts(tenant_id, client, session) -> int` (devuelve cantidad
  sincronizada): pagina `client.accounts_page(offset=..., limit=1000)` en
  loop hasta `offset >= count`, upsert masivo por lote
  (`insert(...).on_conflict_do_update(index_elements=[tenant_id, account_id], ...)`,
  Postgres-only — ya lo es todo PAM Web Console), guarda `synced_at=now()`.
- Al final, borra filas de `account_snapshots` cuyo `tenant_id` matchea y
  `account_id` no está en el set recién sincronizado (cuentas borradas en
  CyberArk).
- `last_synced_at(tenant_id, session) -> datetime | None`: `MAX(synced_at)`
  del tenant, o `None` si nunca sincronizó.

### `app/api/inventory.py` — `accounts()` (modificado)

```python
platform_id: str | None = None,
user_name: str | None = None,
address: str | None = None,
```

Si los tres son `None`: camino actual, sin cambios. Si alguno está
presente: `_accounts_from_snapshot(...)` — chequea `last_synced_at`, si
venció el TTL (`get_settings().accounts_sync_ttl`, nuevo setting, default
300s) llama `sync_accounts` primero (auditando `account.sync`), después
arma la query local (SQLAlchemy `select(AccountSnapshot).where(...)`,
combinando `platform_id`/`user_name`/`address`/`safe_name`/`search` — este
último con `ILIKE` sobre `user_name`/`address`/`account_id`, aproximación
razonable al `search` real de CyberArk) con el mismo `offset`/`limit`/`sort`
de siempre. Responde con el mismo shape `{tenant, count, offset, limit,
accounts}` — el frontend no distingue de dónde vino.

### Frontend

- `components/DataTable.tsx`: `Column<T>.serverFilterable?: boolean` — en
  modo servidor, sólo las columnas que lo declaran habilitan su input de
  filtro; al tipear, en vez de filtrar local llaman
  `server.onChange({ columnFilters: {...} })` (nuevo campo opcional en
  `ServerMode`, mismo mecanismo debounced que ya tiene `search`).
- `pages/Accounts.tsx`: columnas Plataforma/Usuario/Address con
  `serverFilterable: true`; `useServerGrid` (ya soporta `extra`) les pasa
  el valor a la URL igual que hoy hace con `safe_name`.
- Indicador "Actualizado hace Xs" cuando la respuesta trae un
  `synced_at` (nuevo campo opcional en la respuesta del endpoint, sólo
  presente cuando vino del snapshot).

### `app/config.py`

`accounts_sync_ttl: int = 300` (settings, prefijo `PAM_`).

## Tareas

- [x] Modelo `AccountSnapshot` + migración `0009_account_snapshots.py`.
- [x] `app/core/inventory_sync.py`: `sync_accounts` + `last_synced_at` +
      tests unit (paginado completo, upsert, borrado de huérfanos) —
      mismo criterio de corte de página que `client.get_all` (página más
      chica que lo pedido, no depende de `count`); portable SQLite/Postgres
      (sin `ON CONFLICT`, los tests unit corren contra SQLite in-memory).
- [x] `app/api/inventory.py`: params nuevos en `accounts()`, camino
      snapshot vs camino actual sin tocar, auditoría `account.sync`
      (proceso) separada de `account.list` (consulta, con
      `detail.source="snapshot"`). Degrada sirviendo la copia vieja si la
      sync falla y ya había una copia previa.
- [x] `app/config.py`: `accounts_sync_ttl` (default 300s).
- [x] Frontend: `Column.serverFilterable`, `ServerMode.columnFilters` +
      estado propio en `DataTable.tsx` (no reusa el `columnFilters` de
      TanStack, que en modo servidor no filtra nada) con el mismo debounce
      de 300ms que la búsqueda.
- [x] `pages/Accounts.tsx`: Usuario/Address/Plataforma marcadas
      `serverFilterable`, traducidas al query param snake_case
      (`COL_TO_PARAM`) + indicador "datos de hace Xs"
      (`lib/format.ts::relTime`, ya existía, reusado tal cual).
- [x] Tests unit backend (11 nuevos, `test_inventory_sync.py` +
      `test_accounts_snapshot_filter.py`) + `ruff`/`ruff format`/`mypy`/
      `pytest` (349 tests) verdes contra el venv del proyecto.
- [x] `lint`/`tsc`/`build` verdes.
- [x] Migración `0009` corrida contra Postgres real (stack local) — falta
      correrla en el server (parte normal del deploy: `git pull &&
      sudo systemctl reload pam`, el entrypoint corre `alembic upgrade
      head` solo).
- [x] Probado contra Globex (2026-09-16, 1894 cuentas del tenant): primera
      sync completa ~6-8s (dentro de lo razonable para el TTL default de
      300s). Filtro parcial por Plataforma confirmado ("Win" -> 548
      resultados), combinación de dos filtros a la vez (Usuario + Plataforma
      -> 114) sin re-sincronizar dentro del TTL, y orden por columna
      (Address) funcionando contra el snapshot. En el camino se encontró y
      corrigió que `platform_id` comparaba exacto en vez de parcial (ver
      commit `16ef59b`) — inconsistente con `user_name`/`address`, y
      confundía en un input de texto libre.
