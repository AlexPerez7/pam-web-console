# login-hardening

- **Estado:** cerrada — implementada y probada en el server (2026-09-30)
- **Roadmap:** Refuerzo de seguridad (fuera de fases de producto — ver `docs/security.md`), ítem "hardening de autenticación de operadores"
- **Fecha:** 2026-09-23

## Problema

El login de PAM Web Console (Argon2 + TOTP obligatorio + JWT HS256, `backend/app/api/auth.py` +
`backend/app/core/security.py`) ya tiene varias protecciones (lockout por
intentos fallidos, timing constante ante usuario inexistente, `is_active`
revalidado en cada request). Revisando el código contra buenas prácticas de
JWT (RFC 8725) y de rate limiting de endpoints de auth, aparecieron 4 gaps
concretos, priorizados de mayor a menor impacto:

1. **El lockout es por `(username, ip)`, sin un límite global por IP.** Un
   atacante que prueba muchos usernames distintos desde la misma IP (o un
   mismo username desde muchas IPs) nunca acumula 5 fallos en la misma
   combinación — el contador nunca se dispara. Hoy no hay ningún techo al
   volumen total de intentos que puede hacer una sola IP contra `/auth/login`.
2. **No hay forma de revocar un JWT ya emitido.** `read_token`/
   `read_refresh_token` sólo validan firma + expiración — no hay `jti` ni
   versión de sesión. Si un operador cambia su contraseña (sospecha de
   compromiso) o un `platform_admin` la resetea, los access tokens ya
   emitidos (hasta 8h) y refresh tokens (hasta 30 días) del operador
   siguen siendo válidos igual. Sólo `set-active off` corta el acceso
   (`current_operator` sí revalida `is_active` en cada request).
3. **El refresh token no rota ni detecta reuso.** El mismo refresh token
   sirve para pedir access tokens nuevos indefinidamente hasta su propia
   expiración (30 días) — no hay señal de "este refresh token se usó dos
   veces" que delate un robo.
4. **Sin `logout` explícito.** El frontend sólo puede descartar el token
   localmente; el JWT sigue siendo válido en el server hasta que expira
   solo. En un puesto compartido o ante un token filtrado, no hay manera de
   cortarlo de inmediato salvo desactivar la cuenta entera.

Fuera de estos 4, dos observaciones menores, ya documentadas como
decisión consciente y no como bug:

- El lockout (`cache.incr_with_ttl`) degrada abierto si Redis está caído
  (mismo criterio que el resto de `app.core.cache`, pensado para
  disponibilidad). Para un control de seguridad — a diferencia de una
  caché de performance — esto significa que una caída de Redis apaga en
  silencio la única defensa antifuerza-bruta del login. Se documenta acá
  para que la decisión de mantenerlo así sea explícita, no accidental.
- No hay claims `iss`/`aud` en el JWT. Con un solo emisor y un solo
  consumidor (el propio backend de PAM Web Console) agregan poca defensa real hoy,
  pero cuestan casi nada — se incluyen como nice-to-have.

## Alcance

**Entra:**
- Rate limit global por IP en `/auth/login` (independiente del lockout
  por username+IP existente, que se mantiene).
- Versión de sesión (`token_version`) en `Operator`, incluida como claim
  del JWT y comparada en cada verificación — permite invalidar todos los
  tokens de un operador sin esperar su expiración natural (al cambiar
  contraseña, al resetearla un admin, o mediante una acción explícita
  "cerrar todas las sesiones").
- Endpoint `POST /auth/logout` — invalida la sesión actual (bump de
  `token_version` si es la única sesión activa esperada, ver Diseño
  técnico para el trade-off de invalidar TODAS las sesiones del operador
  vs. sólo la actual).
- Claims `iss`/`aud` en el JWT (defensa en profundidad, costo bajo).
- Log/auditoría explícita (no sólo `log.warning`) cuando el lockout de
  login degrada abierto por Redis caído, para que quede visible en
  `docs/runbooks`/monitoreo y no sólo en los logs del contenedor.

**Queda afuera (decisión explícita, no pendiente-por-olvido):**
- Rotación de refresh token con detección de reuso — requiere guardar
  estado por refresh token emitido (no sólo por operador), más máquina
  nueva. Se deja para una spec de seguimiento si el punto 2 (revocación
  por `token_version`) no alcanza en la práctica.
- CAPTCHA o desafíos interactivos en el login — PAM Web Console es una consola
  interna de operación, no pública; el rate limit por IP + lockout por
  usuario ya cubre el escenario realista de amenaza (MSP con acceso
  restringido a la red del server + operadores conocidos).
- Cambiar HS256 por un algoritmo asimétrico (RS256/ES256/EdDSA) — HS256
  con secreto único es correcto para un emisor y consumidor únicos (el
  propio backend); no hay múltiples servicios verificando el token que
  justifiquen separar clave de firma y de verificación.
- Fail-closed del lockout ante Redis caído — cambiarlo tumbaría el login
  de TODOS los operadores ante cualquier blip de Redis, lo cual es peor
  para una consola que a veces hay que usar de urgencia. Se deja
  fail-open pero con visibilidad reforzada (ver Alcance, arriba).

## Requisitos

1. **Rate limit global por IP en `/auth/login`.**
   - Dado que una IP hizo N intentos de login (fallidos o exitosos, contra
     cualquier username) en los últimos `login_ip_window_seconds`,
     cuando N supera `login_ip_max_attempts`,
     entonces el endpoint responde `429` con `Retry-After`, sin ejecutar
     `verify_password`/`verify_totp` (para no gastar cómputo de Argon2 en
     el ataque) y sin distinguir si el username existe.
   - El contador es independiente del lockout por `(username, ip)` que ya
     existe — ambos se evalúan, el más estricto gana.
   - Auditado como `auth.login` con `result="locked"` y `detail.scope="ip"`
     (para distinguirlo del lockout por usuario existente,
     `detail.scope` ausente = comportamiento actual).

2. **`token_version` invalida sesiones sin esperar la expiración.**
   - `Operator.token_version: int` (default 0, migración nueva).
   - El JWT (access y refresh) incluye el claim `tv` con el valor vigente
     al momento de emitirlo.
   - `current_operator`/`read_refresh_token` comparan `tv` contra
     `Operator.token_version` — si no coincide, 401 igual que un token
     inválido (mismo mensaje genérico, no revelar el motivo exacto).
   - Dado un operador con una sesión activa, cuando cambia su propia
     contraseña (`POST /auth/change-password`) o un `platform_admin`
     resetea la suya (`reset-operator` CLI), entonces `token_version` se
     incrementa y todos los tokens emitidos antes de ese momento dejan de
     servir en el próximo request, no en el próximo login.

3. **`POST /auth/logout`.**
   - Requiere sesión válida (`current_operator`).
   - Incrementa `token_version` del operador — invalida TODAS sus
     sesiones activas (no sólo el token que mandó el request). Se elige
     este alcance porque PAM Web Console no trackea tokens individuales (no hay
     `jti` por sesión) — invalidar solo "la actual" necesitaría esa
     máquina extra, que quedó fuera de alcance (ver arriba). Un operador
     que use PAM Web Console desde dos pestañas/equipos a la vez pierde ambas
     sesiones al hacer logout desde cualquiera — trade-off aceptable para
     una consola de operación, no un producto de consumo.
   - Auditado `auth.logout`, `result="ok"`.

4. **Claims `iss`/`aud`.**
   - Emitir `iss="pam"` y `aud="pam-web-console-api"` en access y refresh tokens.
   - Verificarlos explícitamente en `read_token`/`read_refresh_token`
     (PyJWT: `issuer=...`, `audience=...` en `jwt.decode`).

5. **Visibilidad cuando el lockout degrada abierto.**
   - Cuando `cache.incr_with_ttl`/`cache.get_int` devuelven `None` (Redis
     no respondió) durante un intento de login, además del
     `log.warning` ya existente en `app.core.cache`, `auth.py` audita un
     evento propio `auth.login.lockout_degraded` (`result="error"`, sin
     datos de la credencial) — para que quede en `audit_log`/`/audit`, no
     sólo en logs de contenedor que rotan.

## Diseño técnico

**Backend:**
- `alembic/versions/00XX_operator_token_version.py` — `ALTER TABLE
  operators ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0`.
- `app/models/operator.py` — campo `token_version: Mapped[int]`.
- `app/core/security.py`:
  - `_encode(...)` gana un parámetro `token_version: int` → claim `tv`.
  - `make_token`/`make_refresh_token` reciben `op.token_version` (cambian
    de firma: ya no solo `operator_id`, ahora `operator_id, token_version`
    o directamente el `Operator` — a definir en implementación, impacto en
    los ~2 call sites de `auth.py`).
  - `read_token`/`read_refresh_token` **ya no alcanza con devolver el
    `operator_id`** — necesitan devolver también `tv` para que el
    caller (`current_operator`, `refresh`) lo compare contra la DB, o
    bien recibir el `Operator` esperado como parámetro y comparar
    adentro. Definir en implementación cuál ensucia menos la firma
    actual (probablemente: devolver `tuple[int, int]` operator_id+tv).
  - Agregar `iss`/`aud` al payload de `_encode` y a los `jwt.decode` de
    ambas funciones de lectura.
- `app/api/auth.py`:
  - `_fail_key` gana un segundo helper `_ip_fail_key(ip)` (sin username)
    con su propio `settings.login_ip_max_attempts` /
    `settings.login_ip_window_seconds` — chequeo ANTES del lockout por
    username (para no gastar una consulta a la DB si ya está limitado
    por IP).
  - Nuevo endpoint `POST /auth/logout`.
  - `change_password` incrementa `op.token_version` antes del commit.
  - Manejo de `None` de `cache.get_int`/`incr_with_ttl` en el flujo de
    login: distinguir "no hay fallos registrados" (`None` porque la key
    no existe, comportamiento normal) de "Redis no respondió" — hoy
    `cache.get_int` no distingue esos dos casos (ambos devuelven `None`).
    Puede necesitar que `cache.py` exponga una variante que sí distinga,
    o aceptar el degradado sin poder loguear el evento 4 con precisión
    (a resolver en implementación, documentar la limitación si no se
    resuelve).
- `app/config.py` — nuevos settings: `login_ip_max_attempts` (sugerido:
  20), `login_ip_window_seconds` (sugerido: 900, igual a
  `login_lockout_minutes*60` actual).
- `manage.py reset-operator` — ya rota password + limpia TOTP; sumar el
  bump de `token_version` ahí también (hoy un reset de un `platform_admin`
  no corta las sesiones activas del operador reseteado).

**Frontend:**
- `lib/auth.tsx` — botón "Cerrar sesión" ya debe existir (menú de
  usuario); confirmar que hoy sólo borra el token local y sumarle la
  llamada a `POST /auth/logout` antes de limpiar el estado local.

**Compatibilidad:** operadores con sesión activa al momento del deploy
tienen `token_version=0` tanto en la DB como (implícitamente, al no
existir el claim `tv` en tokens ya emitidos) — la verificación debe tratar
la ausencia del claim `tv` en un token viejo como equivalente a `0`, para
no invalidar todas las sesiones activas en el momento del deploy.

## Tareas

- [x] Migración `token_version` en `Operator` (`0015_operator_token_version.py`).
- [x] `security.py`: claims `tv`/`iss`/`aud` en emisión y verificación,
      compatibilidad con tokens sin `tv` (tratar como 0).
- [x] `auth.py`: rate limit por IP global en `/auth/login`.
- [x] `auth.py`: endpoint `POST /auth/logout`.
- [x] `auth.py`: bump de `token_version` en `change-password` (devuelve
      tokens nuevos para que la sesión actual siga andando).
- [x] `manage.py reset-operator`: bump de `token_version`.
- [x] Auditoría `auth.login` con `detail.scope="ip"` para el lockout por IP.
- [x] Auditoría `auth.login.lockout_degraded` cuando Redis no responde
      durante el chequeo/incremento de intentos fallidos (cubre el
      contador global por IP y el incremento de fallos por usuario; el
      gate inicial de `cache.get_int` sigue sin poder distinguir "sin
      fallos" de "Redis caído" — limitación documentada, no resuelta).
- [x] Frontend: `POST /auth/logout` real antes de limpiar el token local
      (`lib/auth.tsx`); `Account.tsx` usa los tokens nuevos que devuelve
      `change-password` en vez de sólo refrescar el operador.
- [x] Tests unitarios nuevos: `test_auth_lockout.py` (rate limit por IP),
      `test_auth_session_revocation.py` (logout invalida todas las
      sesiones, cambio de password invalida la vieja y devuelve una
      nueva, refresh con `tv` vieja rechazado), `test_security.py`
      (compatibilidad con tokens sin `tv`, `aud` incorrecta rechazada).
- [x] `ruff check/format` + `mypy` + `pytest` verdes (pytest corrido en un
      contenedor descartable sobre la imagen `pam-web-console-api` real del server,
      ya que el entorno Python local no coincide con el del proyecto).
      Frontend: `eslint`, `tsc --noEmit`, `vite build` verdes.
- [x] Probado contra el server (2026-09-30): login real, logout real,
      cambio de contraseña con una segunda pestaña abierta que cae.
