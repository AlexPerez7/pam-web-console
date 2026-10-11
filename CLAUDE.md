# CLAUDE.md

Guía para Claude Code al trabajar en este repositorio. Estas instrucciones
tienen prioridad sobre el comportamiento por defecto.

**Responder siempre en español** (el chat con el usuario, no sólo el código
y la documentación — ver "Español neutro, sin voseo" en Convenciones).

## Qué es

**PAM Web Console** — webapp de operación PAM **multi-tenant** sobre
CyberArk Privilege Cloud, pensada para el equipo PAM de un MSP que
administra los tenants de varios clientes: inventario navegable, gestión
(CRUD de cuentas, safes, plataformas, usuarios, sesiones), dashboards de
telemetría, reportes, alertas y un audit log propio atribuido al operador
real.

**Repositorio público de portfolio.** Es la versión saneada de un proyecto
usado en producción: los clientes son ficticios (Globex, Acme, Initech,
Umbrella y un tenant `lab`) y se agregó un **modo demo**
(`PAM_DEMO_MODE=true`, `backend/app/demo/`) que responde la API de CyberArk
con datos generados, para mostrar la consola sin un tenant real.

- **Alcance: solo Privilege Cloud / ISPSS.** El cliente de API soporta
  también Self-Hosted, pero el registro de tenants solo admite
  `privilege_cloud`.
- Plan, arquitectura, riesgos y roadmap: **`docs/PLAN.md`**. Postura de
  seguridad: `docs/security.md`. Árbol de módulos con notas de diseño:
  **`docs/ARCHITECTURE.md`** (leerlo antes de tocar un router, servicio o
  componente).

## Regla de oro del repo público

**Nunca agregar datos reales**: nombres de clientes o de personas, URLs o
IDs de tenants reales, IPs, hostnames o dominios internos, capturas de
respuestas reales de la API, credenciales ni nombres de la empresa. En
tests, specs y datos del modo demo usar sólo los tenants ficticios y
rangos/dominios de ejemplo (`10.x`, `192.0.2.0/24`, `203.0.113.0/24`,
`*.example`, `*.local`). Ante la duda, preguntar antes de commitear.

## Principios no negociables

La consola puede **borrar la cuenta privilegiada de un cliente en
producción**. Cualquier cambio de código debe respetar:

1. **Gating por rol, no por interruptor.** No hay feature-flags de
   escritura por tenant. El único freno de una escritura es el rol RBAC
   efectivo mínimo que exige su endpoint (`require_tenant_access(role)` /
   `require_role(role)` — `operator+`/`approver+`/`tenant_admin+`/
   `platform_admin`, escalonado por el riesgo real de la operación). Nada
   de escritura llega a un endpoint sin ese chequeo de rol explícito.
2. **Todo se audita**, en `audit_log` (append-only, hash encadenado). Toda
   acción — incluidas lecturas — pasa por `app.core.audit.record()`,
   atribuida al operador real. Nunca agregar un path que toque un tenant
   sin auditarlo.
3. **`dry-run` + diff** antes de cualquier escritura (`writeop.run_write`).
4. **Aprobación de un segundo operador** para operaciones destructivas
   (las que tienen `Plan.confirm_phrase`, ver `docs/specs/dual-approval.md`):
   quien no es `platform_admin` deja una solicitud pendiente
   (`app/core/approvals.py`) que aprueba OTRO operador `approver+`
   re-enviando el request con `X-PAM-Approval-Id`. Nadie aprueba lo propio.
5. **Aislamiento por tenant.** Credenciales, tokens y RBAC segmentados. Un
   cliente CyberArk NUEVO por request (no compartir entre tenants).
6. **Mínimo privilegio del service account**, escalonado por fase.
7. **Tenant de laboratorio** antes de cualquier escritura contra un cliente.

El modo demo no relaja nada de esto: sólo cambia el transport HTTP del
cliente de CyberArk y la fuente de las credenciales.

## Modo demo

- `PAM_DEMO_MODE=true` → `build_client()` (y `AleroClient`) usan
  `app.demo.demo_transport(tenant.id)`, un `httpx.MockTransport`; y
  `get_secret()` devuelve credenciales falsas sin leer el secret store.
- Datos: `app/demo/data.py`, deterministas por `id` de tenant, mutables en
  memoria (las escrituras se ven hasta reiniciar el proceso).
- Rutas: `app/demo/transport.py`, una función por endpoint de la API de
  CyberArk (decorador `@route`). Lo que no reconoce responde una colección
  vacía. **Al agregar una llamada nueva a `app/cyberark/client.py`, sumar
  su ruta en el transport** para que la pantalla tenga datos en la demo.
- Tests: `tests/unit/test_demo_mode.py`.
- `manage.py demo-setup` carga los tenants del seed y crea el primer
  operador.

## Comandos

```bash
# levantar (dev o demo)
cp .env.example .env            # completar POSTGRES_PASSWORD y PAM_SECRET_KEY
docker compose up -d --build
docker compose exec -it api python manage.py demo-setup     # sólo en modo demo

# administración (dentro del contenedor). Los que cambian algo piden el
# usuario platform_admin que los corre y quedan a su nombre en el audit log
# (sin terminal: --operador <usuario>).
docker compose exec api python manage.py seed-tenants
docker compose exec api python manage.py create-operator <u> platform_admin
docker compose exec -it api python manage.py reset-operator <u>
docker compose exec api python manage.py verify-audit
docker compose exec api python manage.py check-secrets
docker compose run --rm secrets-admin set-tenant-secret <tenant>
docker compose exec api python manage.py sync-accounts-now
docker compose exec api python manage.py check-alerts-now
docker compose exec api alembic revision --autogenerate -m "..."
docker compose exec api alembic upgrade head
```

Login: `POST /auth/login {username,password}` → la primera vez devuelve el
`otpauth://` para enrolar el TOTP → `POST /auth/login {username,password,
totp_code}` → `access_token` (JWT 15 min) + refresh rotativo en cookie
HttpOnly (`pam_refresh`). Ver `docs/specs/done/session-hardening.md`.

`/docs`, `/redoc` y `/openapi.json` sólo existen con `PAM_ENVIRONMENT=dev`.

## Verificación antes de commitear

Lo mismo que corre el CI (`.github/workflows/ci.yml`):

```bash
cd backend && ruff check . && ruff format --check . && mypy && pytest
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```

Tests backend: `tests/unit/` corre siempre (SQLite in-memory, sin Postgres
ni Redis); `tests/contract/` pega a un tenant real y se salta salvo que
estén las `PAM_CONTRACT_*` (`pytest -m contract`).

## Flujo de specs

Cada feature nueva arranca con una **spec** en `docs/specs/<feature>.md`
antes de tocar código (formato en `docs/specs/README.md`): propuesta +
spec → aprobación del usuario → implementación tildando el checklist →
cierre (la spec pasa a `docs/specs/done/` y se actualiza `docs/PLAN.md`
§7). Cambios chicos (fixes, textos, deps) no necesitan spec.

## Convenciones

- **Español neutro, sin voseo, en TODO el proyecto**: comentarios y
  docstrings, texto de UI y los `detail` de las `HTTPException`,
  documentación y mensajes de commit. "seleccione / elija / vuelva a
  ingresar", no "seleccioná / elegí / volvé"; "aquí", no "acá".
- Python 3.12, type hints, `from __future__ import annotations` en módulos
  con anotaciones nuevas.
- Migraciones Alembic numeradas (`0001`, `0002`, …), con `downgrade`.
- Un router por área en `app/api/`, prefijo en el `APIRouter`.
- Errores de CyberArk → excepciones tipadas de `app.cyberark.errors`, que
  los handlers mapean a HTTP + audit `result="error"`.
- Variables de entorno con prefijo `PAM_` (`app/config.py`).
- **Accesibilidad** (auditado con axe-core, 0 violaciones WCAG 2.1 AA en
  las páginas principales): todo botón sólo con ícono lleva `aria-label`;
  todo `<SelectTrigger>` fuera de un `<label>` lleva `aria-label`; texto de
  estado con `text-success-ink`/`text-warning-ink`/`text-danger-ink`; las
  filas clickeables de `DataTable` ya son navegables con teclado, no
  agregar `onClick` sueltos en `<div>`/`<tr>` sin `tabIndex` + `onKeyDown`.
- Commits: mensaje claro en español.

## Datos sensibles (no commitear)

Cubierto por `.gitignore`: `.env`, `secrets/*` (salvo README y
`.example`), `tls/*`, `*.pem`/`*.key`/`*.crt`, `node_modules/`, `.venv/`.
El secret store real (`secrets/age.key`, `secrets/secrets.age`) sólo existe
en el servidor. `backend/seed/tenants.json` se commitea (sólo URLs y
`secret_ref`, y en este repo son ficticias).
