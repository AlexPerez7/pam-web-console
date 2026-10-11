# compliance-exemptions

- **Estado:** hecha (probada por Alex contra Globex; cerrada el 2026-10-07)
- **Roadmap:** Fase 3, mejora sobre Compliance/ITR (no es una operación de
  escritura sobre CyberArk, es metadata propia de PAM Web Console)
- **Fecha:** 2026-09-29

## Problema

El % de Compliance (Dashboard, página `/compliance` y el reporte ITR — los
tres alimentados por `app.analysis.compliance_breakdown` /
`app.analysis.analyze_accounts`) cuenta como no-compliant a toda cuenta con
`automaticManagementEnabled=false` que está vencida contra la política de su
plataforma o el default del tenant (categoría `DisabledStale`). Ese criterio
se agregó a propósito (2026-09-17, caso real Initech: credenciales con la
gestión deshabilitada y "olvidadas", un problema real que había que mostrar).

Pero hay un segundo caso, real y legítimo, que hoy cae en la misma bolsa:
cuentas que el cliente pidió explícitamente almacenar en la Vault SIN
rotación — ejemplo confirmado en Globex, cuentas `root` que no se usan
para conectarse a nada, sólo custodiadas. Ahí `automaticManagementEnabled`
se deja en `false` a propósito y para siempre; no es un olvido, es la
configuración correcta para esa cuenta. Lo mismo aplica a llaves SSH
(`secretType = "key"`), que por naturaleza no rotan.

CyberArk no registra la diferencia: apagar "Allow automatic password
management" es una acción rápida en el formulario de la cuenta, sin exigir
motivo (confirmado con Alex — el equipo PAM no deja nada escrito ahí). Así
que **no hay ninguna señal confiable en los datos de CyberArk** para
distinguir "deshabilitada a pedido del cliente, para siempre" de
"deshabilitada y olvidada" (caso Initech). Inferirlo por safe, plataforma o
nombre de cuenta sería frágil y arriesgaría falsos negativos (una cuenta que
sí debería rotar, silenciosamente excluida sin que quede registro de por
qué).

## Alcance

- Exclusión automática, sin configuración, de llaves SSH (`secretType ==
  "key"`) del cálculo de vencimiento — señal objetiva, ya usada con el mismo
  criterio en `docs/specs/done/non-rotating-accounts.md`.
- Una excepción de compliance **explícita, por cuenta, con motivo
  obligatorio**, gestionada en PAM Web Console (no en CyberArk): el operador la marca a
  mano, queda auditada, y se puede desmarcar.
- Aplica al mismo criterio en los tres consumidores de
  `compliance_breakdown`/`analyze_accounts`/`account_compliance`/
  `disabled_stale_rows`/`active_overdue_rows`: Dashboard, `/compliance`
  (incluida la ficha de una cuenta) y el reporte ITR — mismo número en
  todos lados, como ya exige el resto del módulo (ver docstrings "mismo
  criterio EXACTO").
- Fuera de alcance:
  - Excepciones masivas (por safe/plataforma) — se agregan después si hace
    falta, empezar por cuenta individual.
  - Expiración automática de la excepción (por ahora queda activa hasta que
    alguien la saque a mano).
  - Tocar `app/itr/analysis.py` (las funciones `analyze_compliance*` de ahí
    están muertas — no las usa ningún caller — no se tocan en esta spec).

## Requisitos

### R1 — Exclusión automática de llaves SSH

- *Escenario:* una cuenta con `secretType = "key"`, nunca rotada, gestión
  activa o no → nunca cuenta en `DisabledStale` ni `ActiveOverdue` ni resta
  del % de compliance, sin que nadie tenga que marcarla.

### R2 — Marcar una cuenta como excepción de compliance

- `POST /tenants/{tenant_id}/compliance/exemptions` — body
  `{"account_id": str, "reason": str}` (`reason` obligatorio, no vacío).
- Rol mínimo `operator+` (es metadata de PAM Web Console, no una escritura sobre
  CyberArk, pero igual que cualquier cambio de estado queda con dueño;
  incluirla en `operator+` — mismo nivel que las operaciones de riesgo bajo
  de Fase 2 — y no en `viewer` para que no sea gratis).
- Guarda `tenant_id`, `account_id`, `reason`, `created_by`, `created_at` en
  `compliance_exemptions`. Si ya existe una activa para esa cuenta, 409.
- Auditado como `compliance.exemption.create`, con `detail={"account_id",
  "reason"}`.
- *Escenario:* operador marca `root-db01` con motivo "Pedido del cliente:
  solo almacenamiento, no se conecta ni rota" → la cuenta desaparece de
  `DisabledStale` en el próximo cálculo (sin caché propia en `/compliance`;
  Dashboard e ITR lo ven en el próximo refresh/generación).

### R3 — Quitar una excepción

- `DELETE /tenants/{tenant_id}/compliance/exemptions/{account_id}` — mismo
  rol (`operator+`). 404 si no hay una excepción activa.
- Auditado como `compliance.exemption.delete`.
- *Escenario:* el cliente pide que esa cuenta vuelva a rotar → se borra la
  excepción, la próxima vez que se calcule compliance la cuenta vuelve a
  evaluarse normal (si sigue deshabilitada y vencida, vuelve a contar).

### R4 — Listar excepciones activas de un tenant

- `GET /tenants/{tenant_id}/compliance/exemptions` — rol `viewer+`, para
  que cualquiera pueda ver QUÉ está excluido y por qué (transparencia:
  nada se excluye en silencio). Devuelve `account_id`, `reason`,
  `created_by`, `created_at` — no hace falta pegarle a CyberArk para esto
  (es una tabla propia).
- La UI de Compliance (`/compliance`) suma una sección o filtro chico con
  esta lista, y un botón "Excluir de compliance" / "Quitar excepción" en la
  ficha de cuenta (mismo lugar donde ya se ve `ComplianceCard`).

### R5 — El cálculo respeta las excepciones activas

- `compliance_breakdown`, `analyze_accounts`, `account_compliance`,
  `disabled_stale_rows`, `active_overdue_rows` reciben un nuevo parámetro
  `exempt_ids: set[str] | None = None` (además de la exclusión de SSH keys,
  que no necesita parámetro — se mira `secretType` directo de la cuenta).
  Una cuenta exenta (por ID o por ser llave SSH) nunca es `overdue`, pase
  lo que pase con su fecha de rotación.
- Los tres callers (`app/api/dashboards.py`, `app/api/itr.py`,
  `app/api/compliance.py`) consultan `compliance_exemptions` del tenant (una
  query liviana, sin pegarle a CyberArk) y arman el `exempt_ids` antes de
  llamar a las funciones de `app.analysis`.
- *Escenario:* Globex tiene 30 cuentas root excluidas y 5 llaves SSH sin
  rotar → las 35 no restan del % de compliance en Dashboard, `/compliance`
  ni en el próximo ITR generado.

### R6 — Seguridad

- Sólo lectura hacia CyberArk (la excepción vive 100% en la base de PAM Web Console).
- No hace falta un segundo operador (principio 4): no es una operación
  destructiva contra un cliente, es metadata de reporting.
- La excepción es por `tenant_id` + `account_id` — aislada por tenant como
  el resto (no puede excluir una cuenta de otro tenant).

## Diseño técnico

- **Migración** `0018_compliance_exemptions.py`: tabla
  `compliance_exemptions` — sólo excepciones ACTIVAS (decisión 2026-09-29):
  `id` (str, uuid corto, PK), `tenant_id` (String, index), `account_id`
  (String, index), `reason` (Text), `created_by` (String), `created_at`
  (DateTime tz), `UNIQUE(tenant_id, account_id)`. Al hacer `DELETE` se
  borra la fila de verdad — el historial de quién creó y quién sacó cada
  excepción queda en `audit_log` (append-only, hash encadenado), no hace
  falta duplicarlo con soft-delete.
- `app/models/compliance_exemption.py`: modelo SQLAlchemy, mismo estilo que
  `app/models/approval.py`.
- `app/analysis.py`:
  - `_account_overdue`: sin cambios de firma; se sigue llamando igual.
  - Nuevo helper `_is_rotation_exempt(account, exempt_ids, *,
    ssh_keys_exempt=True)` → `True` si `account["id"] in exempt_ids` o
    (con `ssh_keys_exempt`, el default) `account.get("secretType") ==
    "key"`.
  - `analyze_accounts`, `compliance_breakdown`, `_overdue_rows` (de la que
    salen `disabled_stale_rows`/`active_overdue_rows`), `account_compliance`:
    todas ganan `exempt_ids: set[str] | None = None`; si la cuenta es
    exenta, se saltea el cálculo de `overdue` (queda `False`) antes de
    sumar a cualquier categoría.
  - **Hallazgo al implementar:** `account_compliance` también la usa
    `app/reports/non_rotating_accounts.py` (reporte "Cuentas que no
    rotan"), que a propósito SÍ quiere ver las llaves SSH como "no rotan"
    (es su función — con su propio filtro opcional `ssh_keys` para
    excluirlas a pedido, ver `docs/specs/done/non-rotating-accounts.md`).
    Aplicar la exención automática de SSH ahí rompía ese reporte (las
    llaves SSH dejaban de aparecer aunque no se pidiera excluirlas). Por
    eso `account_compliance` suma `ssh_keys_exempt: bool = True`, y
    `non_rotating_accounts.py` la llama con `ssh_keys_exempt=False` para
    preservar su comportamiento. Las excepciones manuales (`exempt_ids`)
    no tienen este problema — ese reporte no las consulta, así que no
    hay regresión ahí (fuera de alcance de esta spec, ver arriba).
- `app/api/compliance.py`: nuevo router de exemptions (o sumarlo al mismo
  archivo, prefijo ya es `/tenants/{tenant_id}/compliance`), y los dos
  endpoints existentes (`/accounts`, `/accounts/{id}/status`) pasan a
  consultar `compliance_exemptions` y sumar `exempt_ids`.
- `app/api/dashboards.py` y `app/api/itr.py`: mismo query de
  `compliance_exemptions` antes de llamar a `analyze_accounts`/
  `compliance_breakdown`.
- Frontend: sección nueva en la página Compliance (lista de excepciones +
  alta/baja) y un botón en la ficha de cuenta / `ComplianceCard`.
  `aria-label` en el botón si es sólo ícono (convención del proyecto).

## Tareas

- [x] Confirmar con Alex: DELETE real + audit_log como fuente de verdad
      (decisión 2026-09-29)
- [x] Migración `0018_compliance_exemptions.py`
- [x] `app/models/compliance_exemption.py`
- [x] `_is_rotation_exempt` + parámetro `exempt_ids` en `app/analysis.py`
      (`analyze_accounts`, `compliance_breakdown`, `_overdue_rows`,
      `account_compliance`) + exclusión automática de `secretType == "key"`
      (con `ssh_keys_exempt=False` en `non_rotating_accounts.py` para no
      romper ese reporte)
- [x] `app/core/compliance_exemptions.py` (helpers de acceso a datos
      compartidos) + endpoints `POST`/`DELETE`/`GET`
      `/tenants/{tenant_id}/compliance/exemptions` en `app/api/compliance.py`
- [x] Wiring en `app/api/dashboards.py` y `app/api/itr.py`
- [x] Registrar `compliance.exemption.create`/`.delete` en
      `app/core/audit_catalog.py` (`KNOWN_ACTIONS` + `_LABELS`)
- [x] Tests unitarios (`app.analysis` con `exempt_ids`, SSH keys) —
      `tests/unit/test_analysis.py`; suite completa verde (`ruff`, `mypy`,
      `pytest`)
- [x] Tests de los endpoints nuevos (`POST`/`DELETE`/`GET` exemptions) en
      `tests/unit/test_compliance.py`
- [x] Frontend: pestaña "Excepciones manuales" en `/compliance` (lista +
      alta + baja, `ComplianceExemptionDialogs.tsx`) y botón "Excluir de
      compliance"/"Quitar excepción" en la ficha de cuenta
      (`AccountDetailPanel.tsx`, tarjeta "Estado de la contraseña") —
      `npm run lint` / `npx tsc --noEmit` / `npm run build` verdes
- [x] Probar contra Globex: marcar las cuentas root reales, confirmar que
      el % sube y que el ITR generado después las refleja. Probado por
      Alex; cerrada el 2026-10-07.
