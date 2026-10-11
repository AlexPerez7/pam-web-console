# hardening-pre-escritura

- **Estado:** hecha (R1–R7). Deploy verificado en el server 2026-09-10: la
  app levanta (⇒ guard de `secret_key` OK) y funciona con el CSP activo
  (login + QR del TOTP). Smoke-test puntual del lockout (R4) y primer run de
  CI: no verificados individualmente, pero el deploy quedó sano.
- **Roadmap:** Fase 1 → cierre; `docs/PLAN.md §10` (checklist de seguridad)
- **Fecha:** 2026-09-10

## Problema

PAM Web Console hoy es read-only, pero antes de que exista **cualquier** path de
escritura (Fase 2) conviene cerrar los ítems de `§10` que son código / CI /
config y no dependen de decisiones del MSP ni de un pentest:

- No hay **secret scanning** (gitleaks) ni pre-commit.
- No hay **SCA** (CVEs de dependencias) ni **SAST** en CI.
- `/auth/login` **no tiene protección de fuerza bruta** ni queda registrado
  en el audit (el log de auth es justamente el artefacto de cumplimiento).
- Nada impide arrancar en prod con el `PAM_SECRET_KEY` de dev.
- El frontend **descarga las fuentes de `fonts.gstatic.com` en runtime** —
  dependencia externa innecesaria y complica el CSP.
- Faltan headers de seguridad (`Referrer-Policy`, `Permissions-Policy`, CSP)
  y el bloque HTTP de nginx no tiene ninguno.

## Alcance

**Entra** (todo code / CI / config):
- gitleaks en CI + `.gitleaks.toml` + `.pre-commit-config.yaml`.
- `pip-audit` (backend) + `npm audit` (frontend) en CI.
- Reglas `S` (flake8-bandit) + `RUF` en ruff; hallazgos triados.
- Lockout de `/auth/login` (Redis) + auditoría de eventos de auth.
- Guard de arranque: no bootear fuera de `dev` con `secret_key` por defecto.
- CSP + headers completos en nginx (los dos server blocks); **self-host** de
  Noto Sans (sin `gstatic`).
- `.dockerignore`: `.env`, `*.pem/*.key/*.crt`, `secrets/`.

**Fuera (explícito):**
- Escaneo de imágenes (trivy): el CI no buildea imágenes hoy; se agrega
  cuando exista ese step. Anotado.
- Pentest; backups de Postgres + restore probado; runbook de incidentes /
  break-glass; revisión de seguridad por cliente; store de sesiones para
  revocación granular (Fase 2+); IP allowlist / ZTNA (deployment).
- Escalonamiento de permisos del service account (Fase 2).

## Requisitos

### R1 — Secret scanning (gitleaks)

- Job `gitleaks` en `.github/workflows/ci.yml` (push a `main` + PR). Escanea
  el diff en PR y el árbol completo en push. Falla el pipeline ante un hit.
- `.gitleaks.toml`: allowlist de `seed/tenants.json` (URLs + `secret_ref`,
  cero credenciales), `*.example`, y las fixtures de test que usan
  contraseñas obviamente falsas.
- `.pre-commit-config.yaml`: `gitleaks`, `ruff` (check + format),
  `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`,
  `check-added-large-files`, `detect-private-key`. Documentado en
  `docs/ONBOARDING.md` como recomendado (pre-commit es Python puro, no choca
  con el EDR).

**Escenario:**
Dado un commit que agrega un `client_secret` real en un `.py`,
Cuando corre el CI (o el hook local),
Entonces gitleaks lo marca con archivo+línea y el pipeline/commit falla.

### R2 — SCA (CVEs de dependencias)

- Backend: paso `pip-audit` en el job `backend` (sobre
  `requirements.txt` + `requirements-dev.txt`). `pip-audit` en
  `requirements-dev.txt`.
- Frontend: paso `npm audit --audit-level=high` en el job `frontend`.
- Advisories aceptados a conciencia: `pip-audit --ignore-vuln <ID>` /
  `.npmrc` o `npm audit` con lista, cada uno con un comentario del por qué.

**Escenario:**
Dado que una dependencia tiene un CVE `high`,
Cuando corre el CI,
Entonces el job falla nombrando el paquete y el advisory, salvo que esté en
la lista de ignorados con su justificación.

### R3 — SAST-lite (ruff security)

- `backend/ruff.toml`: `select` suma `"S"` y `"RUF"`.
- `[lint.per-file-ignores]`: `"tests/*" = ["S101"]` (asserts).
- Los 3-4 hits en código de app se resuelven con `# noqa: <code>` +
  comentario, o ignore puntual:
  - `secrets.py` — `subprocess.run(["age", ...])` (S603/S607): comando
    estático, `argv[0]` fijo, argumentos de config confiable → `# noqa`.
  - `security.py` — `DUMMY_HASH` / `config.py` — `secret_key` default
    (S105/S106): son dev-only y R5 los bloquea en prod → `# noqa`.

**Escenario:**
Dado un `eval(...)` nuevo en el backend,
Cuando corre `ruff check`,
Entonces `S307` lo marca y el CI falla.

### R4 — Anti-fuerza-bruta + auditoría de auth

- **Lockout**: en `/auth/login`, contador de fallos por `(username, ip)` en
  Redis (`pam:login:fail:<user>:<ip>`). A partir de `PAM_LOGIN_MAX_FAILS`
  (def 5) en `PAM_LOGIN_LOCKOUT_MINUTES` (def 15) → responde `429` con
  `Retry-After`, sin siquiera verificar la contraseña. Un login exitoso
  limpia el contador. Si Redis no está → **fail-open** (se loguea un warning;
  no se traba a todos por una caída de cache).
- **Auditoría**: cada intento se registra en el audit log
  (`auth.login` con `result` = `ok` | `error` | `locked`, `source_ip`,
  `detail.username`; **sin** la contraseña ni el TOTP). También
  `auth.refresh` (ok/error). Estos registros van con `operator` = el
  username intentado (aunque no exista) para poder investigar.
- El paso de enrolamiento TOTP y el `totp_required` intermedio **no**
  cuentan como fallo (contraseña correcta).

**Escenario:**
Dado 5 intentos fallidos de `alex` desde la misma IP en 3 minutos,
Cuando llega el 6º,
Entonces responde `429 Retry-After: ~720` sin tocar Argon2, y quedan 6
`auth.login` en el audit (5 `error` + 1 `locked`).

### R5 — Guard de configuración al arranque

- `app/config.py` (o el lifespan): si `environment != "dev"` y
  `secret_key in {"dev-only-change-me", ""}` **o** `len(secret_key) < 32` →
  el proceso **no arranca** (excepción clara en el log).
- `GET /health` agrega `config_ok: bool` (secret_key no-default y con largo
  mínimo; secret store disponible) — sin filtrar el valor.

**Escenario:**
Dado `PAM_ENVIRONMENT=prod` y `PAM_SECRET_KEY` sin setear,
Cuando arranca el contenedor `api`,
Entonces sale con error "PAM_SECRET_KEY inseguro para environment=prod" y no
sirve tráfico.

### R6 — Headers de seguridad + fuentes self-hosted

- **Self-host Noto Sans**: bajar los 2 `.woff2` a
  `frontend/src/assets/fonts/`, cambiar el `@font-face` a `url(...)` local.
  Se elimina la request a `fonts.gstatic.com`.
- **nginx** (`frontend/nginx.conf`): un `include /etc/nginx/snippets/
  security-headers.conf` en **ambos** server blocks (:80 y :443) con:
  - `X-Content-Type-Options nosniff`
  - `X-Frame-Options DENY`
  - `Referrer-Policy no-referrer`
  - `Permissions-Policy geolocation=(), camera=(), microphone=(), payment=()`
  - `Content-Security-Policy` (ver abajo)
  - (HSTS queda solo en :443, ya está)
- **CSP** (probada, la app es una SPA servida desde el mismo origen):
  ```
  default-src 'self';
  script-src 'self';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  font-src 'self';
  connect-src 'self';
  frame-ancestors 'none';
  base-uri 'self';
  form-action 'self';
  object-src 'none'
  ```
  `'unsafe-inline'` en `style-src` es necesario (React `style={{}}` en
  DataTable/virtualizer). `img-src data:` por el QR del TOTP.

**Escenario:**
Dado el frontend cargado,
Cuando se inspeccionan las respuestas y la consola,
Entonces todas las respuestas traen el CSP y los headers, no hay request a
`gstatic`, y la app funciona sin violaciones de CSP (login con QR incluido).

### R7 — Higiene del build context

- `backend/.dockerignore` y `frontend/.dockerignore` suman: `.env`, `.env.*`,
  `*.pem`, `*.key`, `*.crt`, `secrets/`, `tls/`.

## Diseño técnico

### CI (`.github/workflows/ci.yml`)

- Job nuevo `security`:
  - `gitleaks/gitleaks-action@v2` (o el binario pinneado) con
    `GITLEAKS_CONFIG=.gitleaks.toml`.
- Job `backend`: `+ pip-audit -r requirements.txt -r requirements-dev.txt`.
- Job `frontend`: `+ npm audit --audit-level=high`.

### Backend

- `app/core/security.py` o `app/api/auth.py` — helper `login_guard`:
  `check_and_bump(username, ip) -> None | RetryAfterSeconds` usando
  `app.core.cache` (nueva funcioncita `incr_with_ttl`). Tolerante a Redis
  caído (fail-open + `log.warning`).
- `app/api/auth.py` — envuelve `login` y `refresh` con `audit.record(...)`
  (helper local, commitea aparte de la lógica de negocio). `Request` para
  el `source_ip`.
- `app/config.py` — `login_max_fails: int = 5`,
  `login_lockout_minutes: int = 15`; validador de `secret_key` vs
  `environment` (pydantic `model_validator`), y el mismo chequeo explícito en
  el lifespan por si el validador se saltea.
- `app/api/health.py` — `config_ok`.
- `backend/ruff.toml` — `select += ["S", "RUF"]`, per-file-ignores.
- `backend/requirements-dev.txt` — `pip-audit`.

### Frontend

- `src/assets/fonts/noto-sans-*.woff2` + `@font-face` local en `index.css`.
- `nginx.conf` + `snippets/security-headers.conf` (montado en la imagen vía
  `COPY` en `frontend/Dockerfile`).

### Docs

- `docs/security.md` — mover a "Implementado" lo que entra; dejar el resto en
  pendiente con nota de por qué (pentest/backups/etc.).
- `docs/ONBOARDING.md` — sección "pre-commit (recomendado)".
- `CLAUDE.md` — CI (nuevos pasos), comandos (`pre-commit install`).

### Principios

- Nada de esto toca el flujo de datos ni agrega escritura. El lockout
  degrada **abierto** ante Redis caído (disponibilidad > el hardening en ese
  caso puntual, y queda en el log). La auditoría de auth **nunca** guarda
  contraseñas ni códigos.

## Tareas

### Pasada 1 (R1 · R2 · R4 · R5 · R7) — hecha en código

- [x] R1: `.gitleaks.toml` + job `security` en CI (binario pinneado,
      `fetch-depth: 0`) + `.pre-commit-config.yaml` (gitleaks + ruff + hooks).
- [x] R2: `pip-audit` (backend) + `npm audit --omit=dev --audit-level=high`
      (frontend) en CI; `pip-audit` en `requirements-dev`. Bumps por CVE:
      fastapi 0.115.6→0.121.1, PyJWT 2.10.1→2.13.0, starlette pinneado
      0.49.1. 5 CVEs de starlette 1.x-only → `--ignore-vuln` documentado.
- [x] R4: `cache.get_int` / `incr_with_ttl` / `delete` (Redis, fail-open);
      `/auth/login` chequea el contador antes de Argon2, lo incrementa en
      cada fallo (password **o** TOTP), lo limpia en el login completo;
      `auth.login` (ok/error/locked) y `auth.refresh` en el audit con
      `source_ip`, sin password ni TOTP. Config
      `login_max_fails` / `login_lockout_minutes` + `.env.example`.
- [x] R5: `model_validator` en `Settings` — no arranca fuera de `dev` con
      `PAM_SECRET_KEY` default o < 32 chars. `config_ok` en `/health`.
      Fixture de tests con key de ≥ 32.
- [x] R7: `.dockerignore` de backend y frontend excluyen `.env*`, `secrets/`,
      `tls/`, `*.pem/*.key/*.crt`.
- [x] Tests: `test_config.py` (guard dev/prod), `test_auth_lockout.py`
      (5 fallos → 429, usuario inexistente cuenta, login OK limpia,
      password-sin-TOTP no cuenta). 48 tests verdes; `ruff` + `mypy` limpios.
- [x] `docs/security.md` + `docs/ONBOARDING.md` + `CLAUDE.md` + `docs/PLAN.md`.
- [~] Server (2026-09-10): la app levanta y el login funciona (⇒ el guard de
      `secret_key` pasó). Lockout (6 fallos → 429) y primer run de CI: no
      smoke-testeados individualmente.

### Pasada 2 (R3 · R6) — hecha en código

- [x] R3: `ruff.toml` `select += ["S", "RUF"]`; per-file-ignore
      `tests/* = [S101, S105, S106]`; `config.py` (S105) y `secrets.py`
      (S603) con `# noqa` justificado; RUF012 (`_SEG` → `ClassVar`), RUF005
      (concat → unpacking), RUF022 (`__all__` ordenado, auto-fix).
- [x] R6: Noto Sans self-hosted (`src/assets/fonts/noto-sans-latin.woff2`,
      sólo el subset latin — cubre el español; sin `gstatic`).
      `frontend/security-headers.conf` con CSP (`default-src 'self'`,
      `style-src 'unsafe-inline'` por React, `img-src data:` por el QR) +
      `X-Content-Type-Options` + `X-Frame-Options` + `Referrer-Policy` +
      `Permissions-Policy`; `include` en los dos server blocks de
      `nginx.conf`; `frontend/Dockerfile` copia el snippet a
      `/etc/nginx/snippets/`. `index.html` construido no tiene scripts ni
      estilos inline → el CSP no lo rompe.
- [x] Server (2026-09-10): la app carga y el login con QR del TOTP funciona
      con el CSP activo. (La única violación de CSP en consola era un script
      inyectado por una extensión del navegador — el CSP bloqueándolo, no un
      bug de la app.)

## Notas de implementación

- gitleaks en CI: usar la action oficial pinneada a un SHA/major; el escaneo
  de historia completa en `main` puede tardar — aceptable.
- `npm audit` a veces marca advisories de `devDependencies` sin fix; si
  bloquea de más, bajar a `--audit-level=critical` para dev y dejar `high`
  para prod deps (`npm audit --omit=dev --audit-level=high`).
- El validador de `secret_key` no debe romper los tests (usan
  `PAM_SECRET_KEY` fijo de 28 chars vía la fixture — subir la fixture a ≥32
  o marcar `environment=dev` en la fixture, que ya es el default).
- CSP: si algo del build de Vite inyecta un `<script>` inline con nonce/hash
  (no debería con la config actual), ajustar `script-src` con el hash exacto
  — verificar en el server, no asumir.
