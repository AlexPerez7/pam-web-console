# Endurecimiento de sesión

- **Estado:** cerrada (probada en el server, 2026-10-07)
- **Roadmap:** seguridad transversal (revisión de mejoras 2026-09-28)
- **Fecha:** 2026-09-28

## Problema
Tras `login-hardening.md` quedaban cuatro huecos: (1) access y refresh token
en `localStorage` — un XSS se los lleva, y el refresh duraba 30 días;
(2) el refresh token no rotaba: una copia robada servía hasta que el
operador cerrara TODAS sus sesiones; (3) un código TOTP interceptado se
podía reutilizar durante ~90 s (`valid_window=1`, sin registrar el último
usado); (4) no había cierre por inactividad — un navegador abierto quedaba
logueado 8 h.

## Alcance
Entra: refresh token rotativo de un solo uso en cookie HttpOnly, access
token corto en memoria, detección de reutilización, vencimiento por
inactividad y tope absoluto de sesión, anti-reutilización de TOTP, cierre
por inactividad en la UI. Fuera: listado/revocación de sesiones por
dispositivo desde la UI (la tabla `refresh_tokens` ya lo permitiría).

## Requisitos
1. **Refresh en cookie.** Dado un login correcto, el refresh token viaja en
   `Set-Cookie: pam_refresh=…; HttpOnly; Secure; SameSite=Strict;
   Path=/api/auth`, nunca en el body. El access token (15 min) va en el body
   y el frontend lo guarda sólo en memoria.
2. **Un solo uso.** `POST /auth/refresh` (con header `X-PAM-Refresh: 1`,
   si falta → 403) marca el token como usado y emite el siguiente de la
   misma familia.
3. **Reutilización.** Cuando llega un refresh ya usado hace más de 30 s,
   entonces se revoca la familia completa (sesión legítima incluida), se
   audita `auth.refresh.reuse` y se responde 401. Dentro de los 30 s se
   acepta (dos pestañas renovando a la vez).
4. **Inactividad / tope.** Un refresh token vence a los 60 min sin usarse
   (`PAM_SESSION_IDLE_MINUTES`); una sesión nunca supera 12 h desde el
   login (`PAM_SESSION_MAX_HOURS`), aunque haya actividad.
5. **TOTP.** Dado un código ya aceptado, cuando se presenta otra vez (o uno
   de un paso anterior), entonces 401 "código TOTP inválido".
6. **UI.** Tras 30 min sin teclado/mouse (en ninguna pestaña) aparece un
   aviso de 60 s; si no hay respuesta se llama a `POST /auth/logout?scope=
   current` (sólo esta sesión, no los otros equipos del operador) y el
   login muestra "Su sesión se cerró por inactividad".

## Diseño técnico
- Migración `0016`: tabla `refresh_tokens` (`jti` PK, `operator_id`,
  `family_id`, `family_started_at`, `issued_at`, `expires_at`, `used_at`,
  `revoked_at`) + `operators.totp_last_step`. Sólo se guarda el `jti`,
  nunca el token.
- `app/core/security.py`: `verify_totp_step()` (devuelve el paso aceptado,
  rechaza pasos `<= totp_last_step`), refresh JWT con claim `jti`
  (obligatorio: los refresh emitidos antes de este cambio se rechazan y hay
  que volver a iniciar sesión una vez).
- `app/api/auth.py`: `_issue_session()` (login, cambio de contraseña,
  refresh), `logout?scope=all|current`. El `SELECT` del operador en login
  va con `FOR UPDATE` para serializar dos logins simultáneos con el mismo
  código.
- `reset-totp` (API y `manage.py reset-operator`) limpia `totp_last_step`;
  el reset de TOTP por API además incrementa `token_version` (dispositivo
  perdido → cortar sesiones).
- Frontend: `lib/api.ts` (token en memoria, `sendWithAuth` único para
  JSON/multipart/descargas), `lib/auth.tsx` (al cargar la página pide un
  access token con la cookie), `components/IdleGuard.tsx`.
- **Túnel SSH (`http://localhost:8080`):** Chrome y Firefox aceptan cookies
  `Secure` en `localhost`. Si algún navegador no lo hiciera, se puede poner
  `PAM_REFRESH_COOKIE_SECURE=false` — sólo para ese caso.
- **Primer deploy:** todos los operadores tendrán que volver a iniciar
  sesión una vez (el access token viejo ya no se guarda y el refresh viejo
  no trae `jti`).

## Tareas
- [x] Modelo + migración `0016`
- [x] Refresh rotativo, reutilización, inactividad, tope absoluto
- [x] Anti-reutilización de TOTP
- [x] Frontend: token en memoria, cookie, IdleGuard, aviso en login
- [x] Tests (`test_session_hardening.py`, `test_security.py`)
- [x] Probar en el server (login, recarga de página, dos pestañas, túnel
      SSH, cierre por inactividad). 2026-10-07 en `pam-standby`: recarga y
      dos pestañas pasado el vencimiento del access token (6 renovaciones,
      ningún `auth.refresh.reuse`). Túnel y cierre por inactividad los
      había probado Alex antes (hay `auth.logout` `scope=current` en el
      audit log desde el 2026-09-29); no se repitieron por los 30 min de
      espera.
