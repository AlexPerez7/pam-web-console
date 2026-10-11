# polish-fase-0

- **Estado:** hecha (probada en el server 2026-09-10: el stack levanta con
  `reload pam`, la web funciona)

## Decisiones (confirmadas 2026-09-10)

1. eslint lintea también `src/components/ui/**` — se arreglan los hallazgos,
   no se ignora.
2. Tests con DB: SQLite in-memory (`aiosqlite`); lo que dependa del trigger
   PG de la migración 0002 va a contract.
3. `pyproject.toml` nuevo sólo para `[tool.mypy]` y
   `[tool.pytest.ini_options]`; `ruff` se queda en `ruff.toml`.
- **Roadmap:** Fase 0, ítem "[~] Pendiente (polish, no bloquea Fase 1):
  … Falta: mypy, pytest/tests de contrato, eslint, logging estructurado."
- **Fecha:** 2026-09-10

## Problema

El CI actual sólo corre `ruff` + `py_compile` (backend) y `tsc` + `build`
(frontend). Para cerrar el polish de Fase 0 faltan cuatro controles que el
`docs/PLAN.md` (§4.2, §7 Fase 0) da por necesarios antes de avanzar con
features de Fase 1+:

1. **Type-check del backend** (`mypy`) — hoy nada valida los tipos de
   Python; los `type hints` existen pero no se verifican.
2. **Suite de tests** (`pytest`) — `backend/tests/unit` y
   `backend/tests/contract` existen vacíos. No hay una sola aserción
   automatizada; toda la lógica pura (`analysis.py`, `core/security.py`,
   `core/audit.py`, `core/rbac.py`) se prueba a mano.
3. **Lint del frontend** (`eslint`) — no hay `eslint` configurado; sólo
   `tsc`. Errores de hooks de React, imports sin usar, `any` implícito,
   etc. pasan sin aviso.
4. **Logging estructurado** — sólo `app/core/cache.py` tiene un logger. No
   hay configuración central, ni formato JSON, ni `request-id` que permita
   cruzar una línea de log con la entrada del `audit_log`.

## Alcance

**Entra:**

- `mypy` sobre `backend/app` (modo pragmático, no `--strict`), en CI y
  documentado para correr local.
- `pytest` + `pytest-asyncio`: tests **unit** de la lógica pura y de los
  helpers de seguridad/auditoría/RBAC. Tests **contract** con marcador
  `contract`, **skippeados por defecto** (corren sólo con credenciales de
  lab presentes) — se deja el andamiaje y 1-2 tests de ejemplo.
- `eslint` (flat config, `typescript-eslint` + `react-hooks` +
  `react-refresh`) en el frontend, script `npm run lint`, en CI.
- Logging estructurado: `app/core/logging.py` (formato JSON opcional por
  env), middleware de `request-id` (genera/propaga `X-Request-ID`), y que
  ese `request-id` quede disponible para `audit.record()`. Config de log de
  `uvicorn` alineada.

**Fuera (explícito):**

- OpenTelemetry / traces / Grafana-Loki-Tempo (§4.2) — Fase 2+.
- Cobertura mínima obligatoria (`--cov-fail-under`) — se puede sumar después
  con la suite ya madura.
- `pre-commit` y `gitleaks` (§10) — su propia tarea de polish, no ésta.
- Reescribir handlers para inyectar el `request-id` en el `detail` de cada
  `audit.record()` de forma exhaustiva — en esta spec el `request-id` llega
  al log estructurado y al header de respuesta; el enganche fino con el
  audit se hace donde sea barato (helper) y el resto queda para una
  iteración posterior si hace falta.

## Requisitos

### R1 — mypy en CI

`mypy` corre sobre `backend/app` con una config pragmática
(`backend/mypy.ini` o sección en un `pyproject.toml` nuevo):

- `python_version = 3.12`
- `ignore_missing_imports = true` (varias libs sin stubs)
- `check_untyped_defs = true`, `warn_unused_ignores = true`,
  `warn_redundant_casts = true`
- **No** `disallow_untyped_defs` global (se puede subir por módulo más
  adelante).
- `plugins = pydantic.mypy`.
- `exclude` de `alembic/versions/`.

**Escenario:**
Dado un cambio que pasa un `str` donde se espera `int`,
Cuando corre el CI,
Entonces el job `backend` falla en el paso `mypy` con el archivo y la línea.

**Escenario (estado base):**
Dado el código actual de `main`,
Cuando se corre `mypy app` por primera vez,
Entonces se corrigen los errores reales y, para los que requieran refactor
mayor, se deja un `# type: ignore[code]` con comentario o un override de
módulo en la config (documentado en la spec al cerrar).

### R2 — pytest: tests unit

`pytest` con `pytest-asyncio` (modo `auto`). `backend/tests/unit` cubre al
menos:

| Módulo | Qué se prueba |
|---|---|
| `app/analysis.py` | `analyze_accounts` (incl. `failed_ids`), `analyze_compliance_breakdown` (el escenario del dashboard-v2: 1000 cuentas / 40 PolicyFailures → 96.0), `analyze_cpm_errors`, `analyze_components` |
| `app/core/security.py` | round-trip Argon2 (`hash`/`verify`), TOTP (`pyotp` con un secreto fijo y `freezegun`/`for_time`), make/read de access y refresh token (typ correcto, expiración, firma inválida) |
| `app/core/audit.py` | `record()` encadena el hash; `verify_chain()` da OK; alterar un `detail` rompe la cadena |
| `app/core/rbac.py` | `effective_role` (platform_admin global gana; deny-by-default sin asignación), `accessible_tenant_ids`, `audit_tenant_ids`, `ROLE_RANK` |

Los tests que tocan DB usan SQLite in-memory async (`aiosqlite`) o una
fixture de sesión con rollback; si el trigger de audit (migración 0002, PG
puro) lo impide, ese test se marca y se cubre en contract. Preferencia:
mantener `audit.py` testeable sin PG (el hash-chain es lógica pura sobre la
fila previa).

**Escenario:**
Dado `pytest` en CI,
Cuando corre `pytest -m "not contract"`,
Entonces pasan todos los tests unit y el job falla si alguno rompe.

### R3 — pytest: andamiaje de contract

`backend/tests/contract` con un `conftest.py` que:

- Define el marcador `contract`.
- Skippea toda la carpeta salvo que exista una env var
  (`PAM_CONTRACT_TENANT` + secreto accesible) — en CI **siempre skip**.
- Incluye 1-2 tests de ejemplo (p. ej. `authenticate()` contra el lab,
  `list_platforms()` devuelve algo con forma esperada).

**Escenario:**
Dado que no hay credenciales de lab,
Cuando corre `pytest`,
Entonces los tests de `tests/contract` aparecen como `skipped`, no como
error ni fallo.

### R4 — eslint en el frontend

Flat config (`eslint.config.js`) con:

- `@eslint/js` recommended
- `typescript-eslint` recommended
- `eslint-plugin-react-hooks` (rules-of-hooks + exhaustive-deps)
- `eslint-plugin-react-refresh`
- `ignores`: `dist/`, `src/components/ui/**` si el ruido de las primitivas
  shadcn es alto (a decidir al implementar; preferencia: no ignorar y
  arreglar).

Script `npm run lint` (`eslint .`). CI lo corre antes de `tsc`.

**Escenario:**
Dado un `useEffect` sin dependencia declarada,
Cuando corre `npm run lint`,
Entonces eslint reporta `react-hooks/exhaustive-deps` y el CI falla.

### R5 — Logging estructurado + request-id

- `app/core/logging.py`: `configure_logging()` que instala un handler con
  formato **JSON** (timestamp ISO, level, logger, mensaje, `request_id` si
  hay) cuando `PAM_LOG_FORMAT=json` (default en `environment != "dev"`), y
  formato legible en dev.
- Middleware (`app/core/middleware.py` o en `main.py`): por cada request
  genera un `request_id` (o toma el `X-Request-ID` entrante), lo mete en un
  `ContextVar`, lo agrega al `response.header` y a un `LoggerAdapter` /
  filtro para que todas las líneas de log del request lo lleven.
- `audit.record()` toma el `request_id` del `ContextVar` si no se le pasa
  uno explícito (hoy el modelo ya tiene el campo `request_id` — verificar;
  si no, esta spec **no** agrega migración, sólo deja el gancho listo).
- Log de acceso: una línea por request (método, path, status, duración_ms,
  operador si está autenticado, tenant si aplica, request_id).
- `configure_logging()` se llama en el `lifespan` de `create_app`. Config
  de `uvicorn` (`--log-config` o `logging_config` dict) alineada para que no
  duplique/rompa el formato.
- `app/config.py`: `log_level: str = "INFO"`, `log_format: str = "auto"`
  (`auto` | `json` | `console`).

**Escenario:**
Dado `PAM_LOG_FORMAT=json`,
Cuando llega `GET /tenants/globex/accounts`,
Entonces se emite una línea JSON con `request_id`, `path`,
`status`, `duration_ms`, y el mismo `request_id` aparece en el header
`X-Request-ID` de la respuesta.

**Escenario (correlación):**
Dado un request que dispara `audit.record()`,
Cuando se inspecciona la fila del `audit_log` y la línea de log de acceso,
Entonces comparten el `request_id` (si el modelo lo soporta hoy; si no,
queda documentado como paso siguiente).

## Diseño técnico

### Backend

- `backend/requirements-dev.txt` suma: `mypy==1.18.*`, `pytest==8.*`,
  `pytest-asyncio==0.25.*`, `aiosqlite` (si se usa SQLite en tests),
  `freezegun` (TOTP/tokens). Versiones exactas al implementar.
- `backend/pyproject.toml` **nuevo** — sólo `[tool.mypy]`,
  `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`, `markers`,
  `testpaths`). `ruff` se queda en `ruff.toml` (no se mueve).
- `app/core/logging.py`, `app/core/context.py` (ContextVar `request_id_var`),
  middleware en `app/main.py`.
- `app/core/audit.py`: leer `request_id_var.get(None)` como default.

### Frontend

- `frontend/package.json` devDeps: `eslint`, `@eslint/js`,
  `typescript-eslint`, `eslint-plugin-react-hooks`,
  `eslint-plugin-react-refresh`, `globals`.
- `frontend/eslint.config.js` nuevo. Script `"lint": "eslint ."`.

### CI (`.github/workflows/ci.yml`)

- Job `backend`: sumar pasos `mypy app` y `pytest -m "not contract" -q`
  (tras `ruff`).
- Job `frontend`: sumar `npm run lint` antes de `tsc`.

### Orden de implementación (commits separados)

1. **eslint** — config + arreglar hallazgos + CI. Aislado al frontend.
2. **mypy** — `pyproject.toml` + config + arreglar/ignorar hallazgos + CI.
3. **pytest unit** — deps + config + tests de lógica pura + CI.
4. **contract scaffold** — `conftest.py` + skip-gating + ejemplos.
5. **logging estructurado** — `logging.py` + ContextVar + middleware +
   config uvicorn + `config.py`.

Cada uno: `ruff` + (lo que aplique) local verde → commit → push. El usuario
pullea en el server y verifica que el stack levanta igual
(`sudo systemctl reload pam`).

### Principios

No toca el flujo de datos ni endpoints. El logging es **aditivo** y no
puede tumbar un request si el handler de log falla (best-effort, como
`cache.py`). Ningún secreto ni credencial entra a una línea de log
(revisar que los `detail` de audit y los payloads de CyberArk no se logueen
crudos). Read-only y aislamiento por tenant intactos.

## Tareas

- [x] **eslint**: `eslint.config.js` (flat, typescript-eslint + react-hooks
      + react-refresh), `npm run lint --max-warnings 0`, paso en CI. 0
      hallazgos: primitivas `ui/` exentas de la regla de fast-refresh,
      `useAuth` con `eslint-disable-next-line`.
- [x] **mypy**: `backend/pyproject.toml` `[tool.mypy]` (sin `--strict`,
      plugin pydantic, `ignore_missing_imports`, `check_untyped_defs`);
      `mypy` limpio ("no issues found in 35 source files"), sin ningún
      `type: ignore`. CI instala `requirements.txt` + `requirements-dev.txt`.
      Fixes: anotación de `by_tenant` (rbac) y `summary` (analyze_components),
      returns de `account_activities` / `live_sessions`.
- [x] **pytest unit**: `[tool.pytest.ini_options]` (asyncio auto, pythonpath,
      marker), `tests/conftest.py` (fixture `session` SQLite in-memory),
      26 tests (`analysis`, `security` con freezegun, `audit` hash-chain,
      `rbac`, `logging`/middleware). CI: `pytest -m "not contract"`.
- [x] **contract scaffold**: `tests/contract/conftest.py` — marca `contract`
      + skip a esa carpeta si faltan `PAM_CONTRACT_{PVWA_URL,IDENTITY_URL,
      CLIENT_ID,CLIENT_SECRET}`; fixture `client` (ISPSSAuth). 3 ejemplos de
      lectura. En CI: skipped.
- [x] **logging**: `app/core/context.py` (`request_id_var`),
      `app/core/logging.py` (`configure_logging`, `JsonFormatter`,
      `RequestIdFilter`, alinea uvicorn), middleware `request_context` en
      `main.py` (genera/propaga `X-Request-ID`, línea de acceso
      `pam.access` con method/path/status/duration_ms), `audit.record()`
      toma el `request_id` del ContextVar, `PAM_LOG_LEVEL`/`PAM_LOG_FORMAT`
      en `config.py` + `.env.example`. `configure_logging()` en el lifespan.
- [x] `CLAUDE.md` (CI + comandos + árbol) + `docs/PLAN.md` (§7 Fase 0)
      actualizados.
- [x] Probado en el server (2026-09-10: `reload pam`, la web funciona),
      spec a `docs/specs/done/`.

## Notas de implementación

- **Python local:** el equipo tiene 3.14 (sin wheels para pydantic-core /
  asyncpg) — el venv de dev se arma con el CPython 3.12 de `uv`:
  `cd backend && uv venv --python 3.12 .venv && uv pip install --python
  .venv/Scripts/python.exe -r requirements.txt -r requirements-dev.txt`.
- **Test del hash-chain en SQLite:** SQLite descarta el `tzinfo` del
  `DateTime`; si el identity map (refs débiles) suelta la fila, `verify_chain`
  la recarga *naive* y el hash recalculado no matchea. Los tests mantienen
  referencias fuertes a las filas. En Postgres (`timezone=True`) no pasa.
- **`mypy` vs `ruff` (C416):** `dict(rows)` sobre `Sequence[Row]` no tipa;
  el dict-comprehension identidad lo marca ruff — se resuelve con
  `{row.tenant_id: row.role for row in rows}`.
- El middleware de logging es best-effort (`contextlib.suppress`) — nunca
  tumba una respuesta.
