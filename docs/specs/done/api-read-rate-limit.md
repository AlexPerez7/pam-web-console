# api-read-rate-limit

- **Estado:** cerrada — implementada y probada en el server (2026-09-30)
- **Roadmap:** Refuerzo de seguridad (fuera de fases de producto — ver
  `docs/security.md`), ítem "rate-limiting del resto de la API (no sólo
  login)" (`docs/security.md` § Pendiente de desarrollar)
- **Fecha:** 2026-09-23

## Problema

Hoy solo dos superficies de PAM Web Console tienen rate-limit: `/auth/login`
(`docs/specs/done/login-hardening.md`) y las escrituras vía
`writeop.run_write` (por `(operador, tenant)`). Los endpoints de
**lectura** — inventario, búsqueda, dashboards, reportes, exports,
recordings, compliance, system-health, Discovery, Alero, ITR — no tienen
ningún techo.

El riesgo no es "abuso anónimo" (todo pasa por `current_operator`, ya
autenticado), sino dos escenarios reales para una consola PAM:

1. **Un script o bug del lado del operador** (loop mal armado, extensión
   de navegador reintentando, un `curl` en loop) puede machacar un
   endpoint de lectura sin ningún freno.
2. **Blast radius compartido:** todos los operadores de un tenant pegan
   contra el **mismo service account** de CyberArk
   (`app.core.tenants.tenant_client`, un `CyberArkClient` nuevo por
   request pero las mismas credenciales). Un cliente descontrolado puede
   saturar ese service account y disparar el rate-limit **de CyberArk**
   — que sí puede terminar en un lockout temporal de credenciales,
   afectando a **todos** los operadores de ese tenant, no solo al que
   generó el tráfico.

## Alcance

**Entra:**
- Un rate-limit general por `(operador, tenant)` para requests `GET` a
  rutas `/tenants/{tenant_id}/...` — cubre inventario, dashboards,
  compliance, system-health de un tenant, recordings, ITR, backups,
  credentials-backup, reports por tenant.
- Un rate-limit general por `operador` (sin tenant, más generoso) para
  rutas `GET` cross-tenant o sin tenant en el path: `/search/*`,
  `/system-health` (el listado cross-tenant), `/knowledge-base`,
  `/reports` (catálogo), `/operators`, `/tenants` (el listado), `/audit`.
- Implementado como middleware ASGI (mismo lugar que `request_context` en
  `app/main.py`), no como dependency por endpoint — un solo punto,
  cobertura automática de rutas nuevas futuras sin tener que acordarse de
  sumarlo a cada router.

**Queda afuera (decisión explícita):**
- **Escrituras (`POST`/`PUT`/`PATCH`/`DELETE`)** — ya tienen su propio
  rate-limit, más estricto y ajustado por operación
  (`writeop.run_write`). El middleware nuevo sólo mira `GET`, para no
  duplicar ni pisar ese mecanismo.
- **`/auth/*`** — tiene su propio rate-limit dedicado
  (`docs/specs/done/login-hardening.md`), con su propia semántica (lockout por
  fallos, no por volumen).
- **`/health`** — sin auth, lo pega el propio Docker `HEALTHCHECK` cada
  15s; no tiene sentido limitarlo.
- **CAPTCHA o bloqueo permanente de cuenta** — un 429 con `Retry-After`
  alcanza; esto no es un endpoint de auth, es tráfico ya autenticado.

## Requisitos

1. **Rate-limit por `(operador, tenant)` en rutas de tenant.**
   - Dado un operador que hizo N requests `GET` contra
     `/tenants/{id}/...` (cualquier endpoint de ESE tenant) en los
     últimos `PAM_READ_RATE_WINDOW_SECONDS`,
     cuando N supera `PAM_READ_RATE_MAX_PER_WINDOW`,
     entonces responde `429` con `Retry-After`, sin llegar a ejecutar el
     endpoint (ni a pegarle a CyberArk).
   - Default propuesto: **300 requests / 60s** por `(operador, tenant)`
     — genera margen amplio sobre el uso real (un dashboard con carga en
     paralelo son ~10 requests; navegar y usar grillas activamente no
     debería superar unas pocas decenas por minuto) pero corta un loop
     descontrolado en segundos.

2. **Rate-limit por `operador` en rutas cross-tenant.**
   - Mismo mecanismo, key sin tenant, límite más generoso (default
     **600/60s**) — cubre búsqueda cross-tenant y listados que abren un
     cliente por cada tenant accesible de una sola vez.

3. **Degrada abierto si Redis no responde.**
   - Mismo criterio que el resto de `app.core.cache` — no bloquear el
     uso normal de PAM Web Console por una caída de Redis. Se loguea (no se audita
     por request individual, sería demasiado ruido para algo no crítico
     como el lockout de login — a diferencia de ese caso, acá no hay
     "intentos fallidos" que perder, es sólo un freno de volumen).

4. **No interfiere con lo ya limitado.**
   - `POST`/`PUT`/`PATCH`/`DELETE` no pasan por este middleware (siguen
     su propio rate-limit de `writeop.run_write`, o ninguno en los pocos
     casos ya documentados como de bajo riesgo).
   - `/auth/*` y `/health` excluidos explícitamente por path.

5. **Header `X-RateLimit-Remaining` informativo (nice-to-have).**
   - En cada respuesta que pasó el chequeo, sumar
     `X-RateLimit-Remaining` con lo que queda de la ventana — ayuda a
     debuggear sin tener que adivinar el estado del contador. No
     bloqueante si complica la implementación del middleware.

## Diseño técnico

**Backend:**
- `app/core/read_rate_limit.py` (nuevo) — la lógica pura: dado
  `operator_id`, `path`, arma la key de Redis (con o sin tenant, según
  si el path matchea `/tenants/{id}/...`) y llama a
  `cache.incr_with_ttl` (reusa `app.core.cache`, mismo patrón que
  `auth.py`/`writeop.py`).
- `app/main.py`: nuevo middleware `read_rate_limit_middleware`, registrado
  junto a `request_context`. Necesita el `operator_id` antes de que la
  request llegue al endpoint — decodifica el JWT del header
  `Authorization` con `security.read_token` en modo *best-effort*: si
  falla (sin token, inválido, expirado), **no aplica el límite y deja
  pasar** — el rechazo real por auth lo sigue haciendo
  `current_operator` como dependency normal del endpoint; este
  middleware sólo limita tráfico YA autenticado, nunca reemplaza el
  chequeo de auth.
  - Extracción de `tenant_id`: regex sobre `request.url.path`
    (`^/tenants/([^/]+)/`) — no hace falta esperar a que Starlette
    resuelva `path_params` (no están disponibles en middleware ASGI
    puro antes del routing).
  - Excluye por método (`request.method != "GET"` → pasa directo) y por
    prefijo de path (`/auth`, `/health`, `/docs` aunque ya esté
    deshabilitado).
  - **Evita decodificar el JWT dos veces:** guarda el `operator_id` (si
    se pudo decodificar) en `request.state.operator_id`.
    `app.core.deps.current_operator` lo reusa cuando está presente en
    vez de volver a llamar a `security.read_token` — el costo de un
    `jwt.decode` es bajo igual (HMAC-SHA256, microsegundos), pero es una
    optimización gratis ya que el middleware corre primero de todos
    modos.
- `app/config.py` — nuevos settings: `read_rate_max_per_window` (300),
  `read_rate_window_seconds` (60), `read_rate_global_max_per_window`
  (600, para las rutas cross-tenant/sin tenant).

**Frontend:** sin cambios — un `429` en una request de lectura ya cae en
el mismo manejo de error genérico que cualquier otro error HTTP en
`lib/api.ts` (`ApiError`), no hace falta un caso especial (a diferencia
del login, que sí necesita distinguir el 429 con un mensaje propio).

**Por qué middleware y no dependency:** una dependency (`Depends(...)`)
habría que agregarla a mano en cada uno de los ~15 routers de lectura —
alto riesgo de olvidarse en uno nuevo. El middleware cubre todo por
default, incluidas rutas que se agreguen después, sin tocar los routers
existentes.

**Impacto en performance:** por request de lectura se suma (a) un
`jwt.decode` (HMAC-SHA256, microsegundos, sin red — y con la
optimización de `request.state` de arriba, no se duplica) y (b) un
round-trip a Redis (`incr_with_ttl`), en la misma red de `docker-compose`
— submilisegundos a pocos ms. Es el mismo mecanismo que ya corre en cada
login y cada escritura hoy, no uno nuevo sin probar. Comparado con el
costo real de estos endpoints (llamadas a la API de CyberArk en la nube,
cientos de ms, más con paginación), el agregado es marginal — incluso en
respuestas que ya salen de caché Redis (Safes/Platforms/Dashboard), donde
el request extra a Redis es la porción más grande del tiempo total, sigue
siendo del orden de 1-2ms, no perceptible para un operador.

## Tareas

- [x] `app/core/read_rate_limit.py`: función pura para armar la key +
      decidir el límite aplicable (por tenant vs. global).
- [x] `app/main.py`: middleware nuevo, registrado junto a
      `request_context`.
- [x] `app/config.py`: settings nuevos con defaults documentados arriba.
- [x] `app/core/deps.py`: `current_operator` reusa el `operator_id`/
      `token_version` ya decodificado por el middleware
      (`request.state.auth_token_claims`) en vez de decodificar el JWT
      dos veces.
- [x] Tests unitarios (`test_read_rate_limit.py`, 8 nuevos): helpers
      puros, límite por tenant se dispara, es independiente entre
      tenants, límite global se dispara, escrituras no cuentan, `/auth`
      exento, token inválido/ausente no rompe el middleware (401 normal,
      no 500), degradación abierta si Redis no responde.
- [x] `ruff check/format` + `mypy` + `pytest` verdes (804 tests).
- [x] Probar contra el server: uso normal de la UI (Dashboard, Accounts,
      Search) no debe disparar el límite; confirmar con un loop de curl
      que sí dispara.
- [x] Actualizar `docs/security.md` (movido de "Pendiente" a
      "Implementado", más fila en el registro de parches).
