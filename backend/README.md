# backend — API PAM Web Console

FastAPI (async) + SQLAlchemy 2.0 + Alembic. Proxy inteligente hacia la API
de CyberArk de cada tenant, con RBAC, auditoría, `dry-run` y jobs.

## Módulos

- `app/api/` — routers por recurso (accounts, safes, platforms, users,
  sessions, tenants, audit, jobs).
- `app/core/` — config, autenticación de operadores (store local + TOTP; o
  LDAP contra el AD on-prem), RBAC (roles × scope por tenant), servicio de
  auditoría append-only, acceso a la custodia de secretos (CCP / Vault).
- `app/cyberark/` — cliente unificado. Ver `app/cyberark/README.md`.
- `app/domain/` — lógica de negocio: validación, construcción de diffs,
  `dry-run`, snapshots de reversión, workflow de aprobación.
- `app/jobs/` — tareas ARQ (onboarding masivo, sync de inventario,
  campañas de rotación). Corren en el/los worker(s).
- `app/models/` — SQLAlchemy: `tenants`, `operators`, `roles`,
  `role_assignments`, `audit_log`, `approvals`, `jobs`, `inventory_cache`,
  `object_snapshots`.
- `app/schemas/` — Pydantic (request/response, validación de payloads).

## Tests

- `tests/unit/` — sin red.
- `tests/contract/` — contra el tenant de laboratorio; verifican que cada
  operación de la API de CyberArk sigue comportándose como se espera.
  **No corren contra producción.**
