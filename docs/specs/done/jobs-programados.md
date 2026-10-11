# jobs-programados

- **Estado:** hecha (2026-09-23) — probado en el server, los 5 tenants
  terminan la corrida sin excepción (umbrella/lab fallan por
  motivos reales ajenos a PAM Web Console, ver abajo, y quedan auditados sin
  frenar al resto). El `worker` quedaba marcado `unhealthy` en
  `docker compose ps` pese a andar bien (confirmado en logs: procesa
  jobs sin problema) — el `HEALTHCHECK` del `Dockerfile`, compartido con
  `api`, pega a `localhost:8000/health`, y el worker no sirve HTTP.
  Corregido deshabilitando el healthcheck heredado en
  `docker-compose.yml` (la recuperación ante un crash real la sigue
  cubriendo `restart: unless-stopped`). Cierra el único pendiente que
  quedaba abierto de esta spec.
- **Roadmap:** Fase 5, "Jobs programados"
- **Fecha:** 2026-09-22

## Problema

PAM Web Console no tiene ninguna forma de ejecutar código fuera del ciclo
request/response. Varias features ya cerradas quedaron con un límite
documentado por esto: el sync de `account_snapshots` sólo corre
on-demand con TTL (nadie lo dispara si nadie abre la grilla de Cuentas
de ese tenant), el onboarding masivo por CSV es síncrono (una request
larga, sin poder abortar a mitad de camino), las campañas de rotación no
tienen historial persistido (se recalcula en vivo cada vez que se abre
la campaña). El stack ya elegía **ARQ + Redis** para esto desde el plan
original (`docs/PLAN.md §4.2`) — nunca se instaló.

## Alcance

**v1 (esta spec): sólo la infraestructura + UN job real, el sync
nocturno de inventario.** Decisión de Alex (2026-09-22): no sumar
reporte ITR automático ni alertas en el mismo corte — validar el
pipeline completo (worker, cron, audit, manejo de errores) con un job
que ya existe y es de bajo riesgo (sólo lee de CyberArk, nunca escribe)
antes de construir jobs nuevos encima.

Explícitamente afuera de esta spec (quedan en el roadmap de Fase 5 para
un corte aparte, una vez que el pipeline esté probado):

- Reporte ITR mensual automático (necesita decidir entrega: ¿se guarda
  para descargar desde Reportes, se manda por mail, Slack?).
- Alertas de deriva de configuración (cuenta nueva sin gestión, safe sin
  CPM, componente caído, sesión fuera de horario).
- Job abortable para onboarding masivo por CSV (reemplazar el v1
  síncrono actual).
- Historial persistido de campañas de rotación.
- Panel en PAM Web Console para ver jobs en curso/histórico (`arq` expone su propio
  estado en Redis; v1 se conforma con verlo en logs + el audit log
  existente).

## Requisitos

1. **El worker corre como contenedor propio, mismo código que `api`.**
   - Dado el `docker-compose.yml` actual (servicios `db`, `redis`, `api`,
     `web`), cuando se agrega `worker: build: ./backend` con
     `command: arq app.jobs.worker.WorkerSettings`,
   - Entonces el worker se levanta con el mismo `Dockerfile`/imagen que
     `api` (sin duplicar dependencias), comparte `PAM_DB_URL`/
     `PAM_REDIS_URL`/el secret store montado, y queda cubierto por
     `restart: unless-stopped` igual que el resto.

2. **El sync nocturno recorre TODOS los tenants, uno no rompe a los
   demás.**
   - Dado que son 5 tenants (`globex`, `umbrella`, `acme`,
     `lab`, `initech`),
   - Cuando corre el cron job `sync_all_tenants_accounts`,
   - Entonces abre un `tenant_client` nuevo por tenant (mismo
     aislamiento que el resto de PAM Web Console — nunca comparte un cliente entre
     tenants), llama a `inventory_sync.sync_accounts` ya existente, y un
     tenant que falla (login caído, CyberArk no responde) se audita como
     error y NO aborta el resto — mismo criterio que
     `system_health._compute_snapshot`.

3. **Corre una vez por noche, horario configurable.**
   - Dado `PAM_INVENTORY_SYNC_CRON_HOUR` (default `2`, hora del servidor
     — Ubuntu ya corre en la zona horaria real, sin conversión UTC
     manual),
   - Cuando llega esa hora,
   - Entonces ARQ dispara el job vía `cron()` en `WorkerSettings`, sin
     necesitar un cron del sistema operativo aparte.

4. **Cada corrida queda auditada.**
   - Dado que hoy `audit.record()` exige `operator`/`role` (strings
     libres, sin FK — ver `app/models/audit.py`),
   - Cuando el worker corre un job,
   - Entonces audita con `operator="system"`, `role="system"` (convención
     nueva, primera vez que PAM Web Console audita algo sin un operador humano
     real detrás) — una fila por tenant (`action="account.sync"`, mismo
     `action` que ya usa el sync on-demand) + un resumen si hace falta
     debug agregado (queda en logs, no en el audit log — el audit es por
     tenant, no por corrida completa).

5. **No compite con el sync on-demand ni lo duplica.**
   - Dado que `inventory_sync.sync_accounts` ya es la única función que
     hace el trabajo real (usada hoy por `GET /accounts` con TTL),
   - Cuando corre el cron nocturno,
   - Entonces reusa la misma función sin cambios — el cron nocturno sólo
     le ahorra al primer operador del día la espera del sync inicial; el
     TTL on-demand sigue funcionando igual para todo el resto del día.

## Diseño técnico

**Dependencias.** `arq` nuevo en `backend/requirements.txt`.

**`docker-compose.yml`.** Servicio `worker` nuevo:
```yaml
worker:
  build: ./backend
  restart: unless-stopped
  env_file: .env
  environment:
    PAM_DB_URL: postgresql+asyncpg://pam:${POSTGRES_PASSWORD}@db:5432/pam
    PAM_REDIS_URL: redis://redis:6379/0
  depends_on:
    db:
      condition: service_healthy
    redis:
      condition: service_healthy
  volumes:
    - ./secrets:/run/secrets:ro
  command: ["arq", "app.jobs.worker.WorkerSettings"]
```
Redis sigue **efímero** (`--save "" --appendonly no`, sin cambios): un
cron job se reprograma solo en el próximo tick si Redis se reinicia a
mitad de camino, no hace falta persistencia para este alcance.

**`app/jobs/` (paquete nuevo).**
- `worker.py` — `WorkerSettings` (`redis_settings` desde
  `PAM_REDIS_URL`, `cron_jobs=[cron(sync_all_tenants_accounts,
  hour=settings.inventory_sync_cron_hour, minute=0)]`,
  `on_startup`/`on_shutdown` si hace falta inicializar logging igual que
  `app/core/logging.py`).
- `inventory.py` — `async def sync_all_tenants_accounts(ctx) -> dict`:
  itera `select(Tenant)`, abre `tenant_client(tenant.id, session)` por
  cada uno (misma función que ya usa el resto de la app —
  `app/core/tenants.py`, sin duplicar), llama a
  `inventory_sync.sync_accounts`, commitea, audita
  `operator="system"`/`role="system"` (ok o error), captura
  `CyberArkError` por tenant sin abortar el loop (mismo patrón que
  `system_health._compute_snapshot`/`search.py`).

**`app/config.py`.** `inventory_sync_cron_hour: int = 2` (settings ya
usa prefijo `PAM_`, sin cambios al mecanismo).

**Sesión de DB fuera de un request.** El worker no tiene el `get_session`
de FastAPI — usa `app.db.SessionLocal()` directo (ya existe, es el
`async_sessionmaker` del que `get_session` es sólo un wrapper con
try/finally para inyección de dependencias).

**Operación en el server.** `infra/pam-web-console.service` ya hace
`docker compose up -d --build` (todo el stack) — el `worker` nuevo queda
cubierto sin tocar el `.service`. Se documenta el comando de logs
(`docker compose logs -f worker`) en `CLAUDE.md`.

**Por qué NO Celery.** Ya evaluado y descartado en el plan original
(`docs/PLAN.md §4.2`): ARQ es async-nativo (encaja con el resto del
proyecto, que es 100% `asyncio`), no necesita un broker aparte (usa el
Redis que ya existe) y el volumen de jobs esperado (sync nocturno +,
más adelante, unos pocos jobs bulk/reportes) no justifica la complejidad
operativa de Celery.

## Tareas

- [x] `arq` en `requirements.txt`.
- [x] `app/jobs/worker.py` + `app/jobs/inventory.py`.
- [x] `app/config.py`: `inventory_sync_cron_hour`.
- [x] `docker-compose.yml`: servicio `worker`.
- [x] Tests unitarios de `sync_all_tenants_accounts` (un tenant que falla
      no aborta el resto, cada tenant se audita, reusa
      `inventory_sync.sync_accounts` sin reimplementar lógica) — 4 en
      `tests/unit/test_jobs_inventory.py`.
- [x] `CLAUDE.md`: sección "Operación en el server" con
      `docker compose logs -f worker`; entrada en "Estado actual".
- [x] Backend (ruff+format+mypy+pytest, 518 tests tras los fixes de
      abajo) verde. Sin cambios de frontend.
- [x] `manage.py sync-accounts-now` — corre el job a mano (llama a
      `sync_all_tenants_accounts` directo, sin depender de que el
      worker/ARQ estén arriba) para poder probarlo en el server sin
      esperar al cron real.
- [x] Probado en el server (2026-09-23): primera corrida de
      `sync-accounts-now` destapó dos bugs reales de una — ver "Bugs
      reales encontrados en el server" abajo. Corregidos, con test de
      regresión, y **re-corridos en el server: los 5 tenants terminan
      sin excepción.** umbrella/lab siguen fallando, pero por
      motivos reales ajenos a PAM Web Console (ver abajo), no por una excepción sin
      atrapar — quedan auditados como error sin frenar a globex/
      acme/initech.
- [ ] Confirmar que el contenedor `worker` (cron real, no el `manage.py`
      manual) levanta sano en el próximo `docker compose up -d --build`.

## Bugs reales encontrados en el server (2026-09-23)

Primera corrida real de `sync-accounts-now` contra los 5 tenants —
umbrella falló por credenciales inválidas (real, no bug de PAM Web Console) y el
proceso entero murió después con un `AttributeError` en otro tenant,
sin llegar a sincronizar a los que quedaban en el loop:

1. **`accounts_page`/`safes_page` (`app/cyberark/client.py`) asumían que
   la respuesta siempre era un dict.** Si CyberArk devuelve un body
   vacío en una 2xx, `_request` lo traduce a `None` (comportamiento
   documentado, usado por endpoints DELETE/204) — pero estos dos métodos
   hacían `data.get(...)` directo, sin chequear. Nunca se había disparado
   antes porque el sync on-demand (`GET /accounts`) sólo corre para
   tenants que alguien mira desde la grilla; el sync nocturno toca TODOS
   de una, incluidos los que nadie había ejercitado así. Fix: guardia
   `isinstance(data, dict)` -> `UpstreamError` (recuperable, ya
   manejado por todos los llamadores) en vez de `AttributeError` crudo.
   Este bug también afectaba al sync on-demand en producción — no es
   exclusivo del job nuevo.
2. **`_sync_one_tenant` sólo atrapaba `CyberArkError`/`AuthError`/
   `SecretStoreError`.** Un error no prescrito (como el `AttributeError`
   de arriba) rompía la excepción hasta la raíz de
   `sync_all_tenants_accounts`, matando el `for` entero — exactamente lo
   que el Requisito 2 de esta spec decía que NO debía pasar. Fix: último
   `except Exception` ancho a propósito (mismo criterio que
   `app/api/search.py` `_search_one` — acá el costo de un bug no
   previsto es mucho más caro, aborta la corrida completa del cron, no
   sólo un request).

2 tests de regresión nuevos (`test_accounts_page_body_vacio_levanta_
upstream_error`/`test_safes_page_body_vacio_levanta_upstream_error` en
`tests/unit/test_cyberark_client.py`) + 1 en
`tests/unit/test_jobs_inventory.py`
(`test_un_error_no_previsto_en_un_tenant_no_aborta_el_resto`).

**Segunda corrida en el server, ya con el fix (2026-09-23, mismo día):**
los 5 tenants terminan sin excepción. Dos siguen en `error`, pero por
motivos reales, no por el bug — quedan documentados acá para no
reabrirlos como si fueran de PAM Web Console:
- **umbrella:** `access_denied — invalid client creds or client not
  allowed`. Credenciales del service account rechazadas por CyberArk —
  hay que revisar/rotar `client_id`/`client_secret` de umbrella en el
  secret store, fuera del alcance de esta spec.
- **lab:** `Get Accounts -> respuesta inesperada: None` — ya no
  crashea (ahora es un `UpstreamError` auditado), pero sigue sin poder
  sincronizar. **Investigado a fondo (2026-09-23):** corriendo
  `seed-tenants` (el `pvwa_url` de `seed/tenants.json` nunca se había
  aplicado en el server, gap ya conocido de `system-health.md`) el login
  OAuth pasó a funcionar (`Last Login` real en Identity Administration),
  pero `GET /API/Accounts` sigue fallando — ahora con un `302` (antes
  parecía un `200` vacío; `client.py` sólo loguea `status_code`, no
  seguía redirects). El `Location` del redirect apunta al **login
  interactivo de Identity** (`{identity_tenant_url}/login?redirectUrl=
  ...API/Accounts...`), no a un 401/403 — es decir, el gateway de PVWA
  de ese tenant no reconoce el Bearer token como sesión de API válida y
  cae al flujo de browser. Descartado que sea permisos: el usuario de
  servicio (`apiuser@pamlab`) tiene roles de admin completos
  (incl. `Privilege Cloud Administrators`, `System Administrator`) y
  Safe access total. Tampoco es un "Web App"/cliente OAuth mal
  configurado en Identity Administration (ese catálogo es para
  integraciones SAML/OIDC de terceros, no para el flujo ISPSS de
  client-credentials que usa `app/cyberark/auth.py`). **Conclusión:**
  parece un problema de vinculación entre ese Privilege Cloud puntual
  (`umbrellatest.cyberark.cloud`, el subdominio real detrás del alias
  `pamlab.cyberark.cloud`) y el tenant de Identity, del lado de
  CyberArk — no algo resoluble desde la configuración de PAM Web Console ni desde
  self-service en el portal. **Decisión (Alex, mismo día): abrir caso de
  soporte con CyberArk** (síntoma: "Bearer token OAuth válido, pero
  `GET /API/Accounts` responde 302 redirigiendo al login de Identity") y
  dejar `lab` fuera de alcance mientras tanto — es el tenant lab
  propio, sin urgencia de cliente real. El sync nocturno ya lo maneja
  sin romper el resto (`error` auditado, los otros 4 tenants sincronizan
  bien). De paso, `client.py` ahora loguea `location` además de
  `content-type`/`content-length` cuando una 2xx viene con body vacío —
  mejora general de observabilidad, no específica de este caso.
