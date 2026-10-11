# Seguridad — PAM Web Console

Documento vivo. El modelo de amenaza resumido y el checklist están en
`PLAN.md` §3 y §10; aquí se desarrollan a medida que avanza el proyecto.
`## Implementado` es la postura actual (qué hay hoy); esta tabla es el
registro cronológico de cómo se llegó — para ver de un vistazo qué se
parchó, cuándo y por qué, sin releer los párrafos de abajo.

## Registro de parches y decisiones

| Fecha | Ítem | Mitiga | Commit / ref |
|---|---|---|---|
| 2026-10-07 | **Atribución real en el audit log.** Los comandos de consola que cambian algo (`create-operator`, `reset-operator`, `set-active`, `seed-tenants`, `set-tenant-secret`, `import-standby-audit`) piden el usuario de PAM Web Console de quien los corre (platform_admin activo, o `--operador`) y quedan a su nombre; antes los de secretos y fail-back quedaban como `system` y los de operadores/seed **no se auditaban**. Las renovaciones de sesión rechazadas se atribuyen al dueño del token si la firma es válida, o a `(sin sesión)` (antes `?`), con el motivo. El webhook de Teams ya no se loguea al fallar un envío (su `sig=` es la credencial del flujo). | Cambios de operadores o secretos sin rastro de quién los hizo; filtración de la URL del webhook en los logs | — |
| 2026-09-28 | Aprobación de un segundo operador (principio 4) para las ops de Fase 3: quien no es `platform_admin` deja una solicitud pendiente; otro operador `approver+` la aprueba re-ejecutando el request (el plan recalculado debe coincidir con el solicitado); nadie aprueba lo propio; vencen a las 24 h | Error o abuso de un solo operador en operaciones destructivas (borrar cuenta/safe/plataforma, quitar miembros, reasignar PSM), sesión robada de un `approver` | spec `docs/specs/dual-approval.md` |
| 2026-09-28 | Endurecimiento de sesión: access token 15 min sólo en memoria, refresh rotativo de un solo uso en cookie `HttpOnly; Secure; SameSite=Strict` (+ header `X-PAM-Refresh`), detección de reutilización (revoca la familia), vencimiento por inactividad (60 min) y tope de 12 h, anti-reutilización de TOTP, cierre por inactividad en la UI (30 min) | Robo de tokens por XSS desde `localStorage`, refresh robado válido 30 días, replay de un código TOTP interceptado, sesiones abiertas indefinidamente en un equipo desatendido | spec `docs/specs/done/session-hardening.md` |
| 2026-09-28 | IP real del cliente detrás de nginx: uvicorn `--proxy-headers --forwarded-allow-ips` (rangos privados de la red de compose, override `PAM_FORWARDED_ALLOW_IPS`) + nginx pisa `X-Forwarded-For` con `$remote_addr` | `request.client.host` era siempre la IP del contenedor nginx: el tope de login por IP (20/15 min) quedaba compartido por TODO el equipo (un atacante podía bloquear el login de todos), el lockout por (usuario, IP) degradaba a sólo-usuario, y el `source_ip` del `audit_log` no identificaba el origen real | `backend/entrypoint.sh`, `frontend/nginx.conf` |
| 2026-09-23 | Rate-limit general de lecturas (`GET`), middleware por `(operador, tenant)`/`operador` | Un cliente descontrolado (script, bug de polling) saturando el service account COMPARTIDO de un tenant, o disparando el rate-limit propio de CyberArk (afecta a todos los operadores de ese tenant) | spec `docs/specs/done/api-read-rate-limit.md` |
| 2026-09-23 | `cryptography` 43.0.3 → 50.0.1 + `pip` actualizado en el Dockerfile | 6 CVEs sin parchear en `cryptography` (`PYSEC-2026-35/1284/2141/3553/3554`, `GHSA-537c-gmf6-5ccf`), no cubiertos por el ignore-list de CI; CVEs de `pip` (sólo build-time) | — |
| 2026-09-23 | `/docs`, `/redoc`, `/openapi.json` deshabilitados fuera de `dev` | Exposición sin autenticación del schema completo de la API (agravada por el acceso directo a la LAN, ver fila siguiente) | — |
| 2026-09-23 | `PAM_ENVIRONMENT` `dev`→`prod` + `PAM_LOG_FORMAT=console` en el `.env` del server | Guard de `PAM_SECRET_KEY` inactivo al arranque (verificado: la clave real ya era fuerte, sin ventana de exposición real) | config del server, no versionado (`.env`) |
| 2026-09-23 | Hardening de login: `Operator.token_version` (revocación de sesión), rate-limit global por IP en `/auth/login`, claims `iss`/`aud`, `POST /auth/logout` real | Fuerza bruta cross-username desde una IP, sesiones sin forma de revocar, JWT sin defensa en profundidad | spec `docs/specs/done/login-hardening.md` |
| 2026-09-09 | Acceso directo por HTTPS en la LAN (`PAM_HTTPS_BIND` a la IP real) | Decisión consciente (confirmada 2026-09-23): prioriza reforzar login/superficie de API en vez de depender del túnel SSH como única barrera | config del server + `docker-compose.yml`, ver sección abajo |
| 2026-09-10 | Baseline de CI: `pip-audit`, `npm audit --audit-level=high`, `gitleaks`, `ruff` con reglas `S` | CVEs de dependencias, secretos commiteados, patrones inseguros comunes (SAST-lite) | `.github/workflows/ci.yml` |

## Implementado

- **Auth de operadores:** Argon2id (contraseñas) + TOTP obligatorio.
  Access token JWT HS256 15 min (sólo en memoria del navegador) + refresh
  token rotativo de un solo uso en cookie HttpOnly (tabla `refresh_tokens`,
  vence a los 60 min sin uso y a las 12 h desde el login, ver
  `docs/specs/done/session-hardening.md`), con claims `iss`/`aud` verificados. **Revocación granular vía
  `Operator.token_version`** (`tv` en el JWT, comparado contra la DB en
  cada request): se incrementa en `POST /auth/logout` (invalida TODAS las
  sesiones del operador, no sólo la actual), en `change-password` (que
  devuelve tokens nuevos para no cortar la sesión que hizo el cambio) y en
  `manage.py reset-operator`. Compatible con tokens emitidos antes de este
  cambio (sin `tv` = se trata como `0`). Ver
  `docs/specs/done/login-hardening.md`.
- **RBAC scopeado por tenant** (`role_assignments`, deny-by-default).
  `Operator.role` = rol de plataforma; `platform_admin` global = acceso
  total. Para el resto, el acceso a un tenant y el rol efectivo salen de
  `role_assignments` (fila por tenant, o `*` = fallback). `viewer` sin
  asignaciones no ve ningún tenant. Enforcement en todos los `/tenants/*`,
  la lista `GET /tenants` y la búsqueda cross-tenant.
- **`/audit`** restringido a `tenant_admin+` (de plataforma o efectivo en
  algún tenant) y scopeado a esos tenants. `GET /tenants/{id}/audit` para el
  rastro de un objeto puntual, con acceso `viewer` al tenant.
- **Audit log** append-only con hash sha256 encadenado + trigger Postgres
  anti UPDATE/DELETE (migración 0002). Toda lectura y escritura pasa por
  `audit.record()`, atribuida al operador real.
- **Aislamiento por tenant:** un `CyberArkClient` nuevo por tenant por
  request; tokens no compartidos.
- **Escritura (Fase 2, `writeop.run_write`):** cada operación de escritura
  exige el rol RBAC efectivo mínimo que le corresponde sobre el tenant
  (`operator+`/`approver+`/`tenant_admin+`/`platform_admin`, escalonado
  según el riesgo real de la operación — no hay feature-flags de escritura
  por tenant, se sacó el mecanismo `Tenant.enabled_features` el 2026-09-22,
  ver CLAUDE.md "Principios no negociables"). Antes de ejecutar: `dry-run`
  que arma el diff sin tocar CyberArk, `reason` obligatoria (≥ 8),
  rate-limit por `(operador, tenant)` y circuit breaker por cantidad de
  objetos. Se audita el intento, el dry-run y la ejecución (con
  `before`/`after` de los campos que cambian, `reason`, el JSON Patch). El
  cliente CyberArk **no reintenta** los POST de creación (un timeout podría
  haber ejecutado la operación).
- **Anti-fuerza-bruta en `/auth/login`:** contador de fallos por
  `(usuario, IP)` en Redis; a los `PAM_LOGIN_MAX_FAILS` (def 5) fallos en
  `PAM_LOGIN_LOCKOUT_MINUTES` (def 15) → `429 Retry-After`, sin verificar la
  contraseña. Un login completo limpia el contador. **Además, un techo
  global por IP** (`PAM_LOGIN_IP_MAX_ATTEMPTS`, def 20, en
  `PAM_LOGIN_IP_WINDOW_SECONDS`, def 900) evaluado antes que el anterior —
  corta el escenario de muchos usernames distintos probados desde la misma
  IP, que el contador por-usuario no cubre. Fallos de contraseña y de TOTP
  cuentan; los pasos intermedios (`enroll`, `totp_required`) no. Si Redis no
  responde, ambos degradan **abierto** (decisión consciente: fail-closed
  tumbaría el login de todos los operadores ante cualquier blip de Redis) —
  pero la degradación queda auditada (`auth.login.lockout_degraded`), no
  sólo en logs de contenedor.
- **Rate-limit general de lecturas (`GET`):** middleware ASGI
  (`app.main.read_rate_limit_middleware`), corre para toda request `GET`
  autenticada salvo `/auth/*`, `/health`, `/docs`/`/redoc`/`/openapi.json`.
  `PAM_READ_RATE_MAX_PER_WINDOW` (def 300) por `(operador, tenant)` en
  rutas `/tenants/{id}/...`; `PAM_READ_RATE_GLOBAL_MAX_PER_WINDOW` (def
  600) por operador en rutas cross-tenant (`/search`, `/system-health`,
  etc.), ambos en `PAM_READ_RATE_WINDOW_SECONDS` (def 60). Protege al
  service account COMPARTIDO de cada tenant de un cliente descontrolado
  — un operador scripteando o un bug de polling no debería poder disparar
  el rate-limit propio de CyberArk, que afectaría a TODOS los operadores
  de ese tenant. Las escrituras no pasan por acá (tienen su propio límite
  en `writeop.run_write`); si Redis no responde, degrada abierto (mismo
  criterio que el resto de `app.core.cache`). Ver
  `docs/specs/done/api-read-rate-limit.md`.
- **Auditoría de auth:** cada intento de login (`auth.login`, resultado
  `ok`/`error`/`locked`) y cada refresh (`auth.refresh`) queda en el audit
  log con `source_ip` — sin la contraseña ni el código TOTP.
- **Guard de arranque:** fuera de `environment=dev`, el proceso **no
  arranca** si `PAM_SECRET_KEY` es el default o tiene < 32 caracteres
  (validador de `Settings`). `GET /health` reporta `config_ok`.
- **Pipeline de seguridad en CI:**
  - `gitleaks` (secret scanning, historia completa en push) — `.gitleaks.toml`.
  - `pip-audit` (backend) + `npm audit --omit=dev --audit-level=high`
    (frontend) — CVEs de dependencias.
  - `ruff` con reglas `S` (flake8-bandit) — SAST-lite; tests exentos de
    `S101`, los pocos hits de app con `# noqa` justificado (`secrets.py`
    subprocess, default de dev).
  - `.pre-commit-config.yaml` con gitleaks + ruff + hooks básicos
    (recomendado, no obligatorio).
- **`/docs`, `/redoc`, `/openapi.json` deshabilitados fuera de `dev`.**
  Exponían sin autenticación el mapa completo de la API (paths, params,
  shapes de request/response de operaciones como `account.delete` o
  `account.reveal`) — aceptable cuando el único acceso era vía túnel SSH,
  pero riesgoso ahora que PAM Web Console también se sirve directo en la LAN (ver
  "Acceso directo por HTTPS en la LAN" más abajo). `create_app()` los
  deja en `None` salvo `PAM_ENVIRONMENT=dev`.
- **`PAM_ENVIRONMENT` corregido a `prod` en el server (2026-09-23).**
  Estaba en `dev` desde el despliegue inicial, lo que además desactivaba
  el guard de `PAM_SECRET_KEY` de arranque (ver abajo) — verificado que
  la clave real ya era fuerte (64 caracteres, no el default), así que no
  hubo ventana de exposición real, pero el guard no estaba cumpliendo su
  función. `PAM_LOG_FORMAT=console` se fijó explícito para no perder los
  logs legibles al salir de `dev` (que por default cambia el formato a
  JSON).
- **Headers HTTP (nginx, ambos server blocks):** `Content-Security-Policy`
  (`default-src 'self'`, sin fuentes externas), `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy`; HSTS en `:443`. La SPA es 100% self-hosted — la
  fuente Noto Sans se sirve local (sin request a `fonts.gstatic.com`).
- **Dependencias:** hasta el 2026-10-05 `pip-audit` ignoraba 5 CVEs de
  `starlette` (sólo corregidos en 1.x), con el argumento de que FastAPI
  pinneaba `starlette<0.50`. Ese argumento ya no valía: FastAPI declara
  `starlette>=0.46.0` sin tope, el tope era el pin manual de
  `requirements.txt`. El 2026-10-05 se subió a **starlette 1.7.0** (los
  parches llegan hasta 1.3.1) y se quitaron los `--ignore-vuln` del CI; las
  5 alertas de Dependabot se cierran al llegar el cambio a `main`. De los
  5, uno sí tocaba código
  propio: el CVE de validación del header `Host` (CVE-2026-48710) permite
  falsear `request.url.path`, y PAM Web Console lo usa en el bloqueo de cambio de
  contraseña (`core/deps.py`), en la exención del rate-limit de lecturas
  (`main.py`) y en `core/approvals.py`; los demás no aplicaban (sin
  `StaticFiles`, `HTTPEndpoint` ni `request.url.hostname`; Linux; límite de
  1 MB de nginx para el `request.form()` urlencoded). Si reaparece un
  ignore en el CI, documentar aquí por qué.
  **`cryptography` actualizada 43.0.3 → 50.0.1 (2026-09-23):** corriendo
  `pip-audit` a mano aparecieron 6 CVEs sin parchear en la versión pinneada
  (no cubiertos por el ignore-list existente, que sólo trata starlette) —
  sin uso directo en el código de PAM Web Console (dependencia transitiva de `PyJWT`
  para algoritmos asimétricos que PAM Web Console no usa, sólo HS256), así que el
  bump fue directo, sin cambios de código. `pytest`/`ruff`/`mypy` verdes
  después del bump. De paso, el Dockerfile actualiza `pip` mismo antes de
  instalar requirements (traía CVEs propios de manejo de índices/sdists —
  sólo importa en build time, `pip` no corre en runtime, pero es gratis
  de arreglar).
  **`react-router-dom` (frontend), 2 CVEs moderadas sin actuar (2026-09-23,
  riesgo aceptado):** open-redirect en `<Link>`/`useNavigate` (requiere
  pasarle a la navegación un valor no confiable — PAM Web Console no tiene ningún
  `?redirect=`/`?next=` que tome el destino de la URL, todas las
  navegaciones van a rutas fijas) y constructor injection en
  `deserializeErrors()` durante hidratación SSR (PAM Web Console es una SPA estática
  servida por nginx, sin SSR — no aplica). El fix real es un salto de
  versión mayor (v6 → v7) que `npm audit fix --force` marca como cambio
  de breaking; no se hace a ciegas por dos CVEs no explotables en el uso
  real de PAM Web Console. Revisar si en algún momento se justifica migrar a v7 por
  otro motivo.

## Acceso directo por HTTPS en la LAN (2026-09-09)

Decisión consciente, no un descuido: `docker-compose.yml` expone `:443` en
`${PAM_HTTPS_BIND:-127.0.0.1}`, y en el despliegue real se fijó a la IP LAN
del servidor, con un certificado propio (autofirmado al principio, luego
uno de una CA local con mkcert). Esto reemplaza al túnel SSH como forma
primaria de acceso a la UI para el equipo en la red interna. `:8080` (HTTP)
y `:8000` (API cruda/`/docs` en dev) siguen atados a `127.0.0.1`, sólo
alcanzables por túnel.

El objetivo (2026-09-23) es priorizar reforzar **login y superficie de la
API** (rate limiting, revocación de sesiones, `/docs` deshabilitado — ver
arriba) en vez de depender de que el túnel SSH sea la única barrera.
Superficie a cuidar con mkcert: la llave privada de la CA local
(`rootCA-key.pem`) permite emitir certificados que todo el equipo acepta;
sólo se distribuye el `rootCA.pem` público y la llave se respalda cifrada.

## Pendiente de desarrollar (Fase 0)

- [ ] Modelo de amenaza completo (STRIDE por componente).
- [ ] Diseño del audit log append-only: esquema, hash encadenado, export
      WORM, retención, procedimiento de verificación de integridad.
- [ ] Diseño del RBAC: matriz rol × operación × scope de tenant.
- [ ] Manejo de secretos: flujo de alta/lectura/rotación de service
      accounts en CCP / Vault self-hosted; autenticación de PAM Web Console ante el CCP
      (app-id + cert de cliente + allowlist).
- [ ] Escalonamiento de permisos del service account por fase y por cliente.
- [ ] Workflow de aprobación: estados, separación de funciones, timeouts,
      notificaciones.
- [ ] `dry-run` y circuit breaker: umbrales, confirmación reforzada.
- [ ] Snapshots de reversión: qué se guarda por tipo de objeto, cómo se
      restaura.
- [ ] Hardening restante: escaneo de imágenes (trivy — cuando el CI buildee
      imágenes).
- [ ] Plan de respuesta a incidentes + procedimiento break-glass.
- [ ] Checklist de onboarding de un tenant nuevo (revisión de seguridad del
      cliente, aprobación, cláusula contractual).

## Reporte de vulnerabilidades

*(Definir canal cuando el proyecto salga de Fase 0.)*
