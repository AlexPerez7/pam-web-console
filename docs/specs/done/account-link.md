# account-link — Vincular una cuenta de logon / reconciliación

- **Estado:** hecha. Probada contra Globex (2026-09-12). v1 sin listar los
  links actuales (`GET /API/Accounts/{id}` no los devuelve) ni desvincular —
  fast-follow.
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — "Vincular logon/reconcile accounts
  (`account.link`)"
- **Fecha:** 2026-09-10
- **Base:** `docs/specs/done/fase-2-escritura.md` (maquinaria `writeop`),
  `docs/specs/safe-member-add.md` (op de acción + buscador).

## Problema

Muchas cuentas privilegiadas necesitan una **cuenta de logon** (para que el
CPM/PSM entre con un usuario y luego eleve) o una **cuenta de
reconciliación** (para que el CPM resetee el secreto cuando se
desincroniza). Vincularlas es tarea habitual del onboarding y del
troubleshooting de rotación. Hoy se hace en el PVWA de cada cliente.

PAM Web Console ya tiene el pipeline de escritura y la búsqueda de objetos. Falta la
operación que **vincula** una cuenta a otra.

## Alcance

**Entra:**
- Vincular **una** cuenta existente como **logon** (`extraPasswordIndex 1`) o
  **reconcile** (`extraPasswordIndex 3`) de una cuenta destino —
  `POST /API/Accounts/{id}/LinkAccount`.
- Buscar la cuenta a vincular por nombre (reusa `GET /tenants/{id}/accounts?
  search=`).
- Vía `writeop.run_write`: feature `account.link`, `reason`, dry-run, audit.
- UI: sección "Cuentas vinculadas" en el detalle de la cuenta, con la acción
  "Vincular cuenta".

**Fuera (otras specs / futuro):**
- **Desvincular** (`DELETE /API/Accounts/{id}/LinkAccount/{index}`) — bajo
  riesgo también; se agrega como fast-follow con la misma feature cuando se
  pueda mostrar el estado actual de los links (ver "Decisiones abiertas").
- Índices distintos de 1 (logon) y 3 (reconcile) — otros tipos de linked
  account que define la plataforma. Se tipea el índice a mano si se necesita.
- Crear la cuenta de logon/reconcile (`account.create`) — otra spec.
- Vincular en lote (todas las cuentas de una plataforma a la misma reconcile)
  — Fase 4 (job).

## Diseño

### Feature-flag

- `account.link` — ya está en `KNOWN_FEATURES`. Toggle en la página Tenants
  ("Vincular cuenta de logon / reconciliación").

### Es una op de acción (sin diff de campos)

Como `safe.member.add`: `Plan.effect` + un `diff` "de alta" + `warnings`.

- `Plan.patch = []`
- `Plan.effect = "Se vincula **<nombre>** (safe <safe>) como cuenta de
  **<logon | reconciliación>** de esta cuenta."`
- `Plan.diff`:
  - `{field: "tipo", from: null, to: "logon (índice 1)" | "reconcile (índice 3)"}`
  - `{field: "cuenta vinculada", from: null, to: "<nombre>"}`
  - `{field: "safe", from: null, to: "<safe>"}`
  - `{field: "folder", from: null, to: "<folder>"}`
- `Plan.warnings`:
  - si la cuenta destino tiene `secretManagement.automaticManagementEnabled`
    en `false` **y** el tipo es reconcile → "la gestión automática está
    desactivada; el CPM no va a usar la reconcile account hasta que se
    active."
  - si `linked_safe` == la safe de la cuenta destino y `linked_name` == el
    nombre de la cuenta destino → "estás vinculando la cuenta a sí misma."
- `Plan.object_count = 1`

`DryRunDialog`: hay `diff` → tabla + warnings → "Cambio aplicado." (POST
síncrono).

### Backend

- `app/cyberark/client.py`:
  - `link_account(account_id, body)` → `POST /API/Accounts/{id}/LinkAccount`.
    Idempotente (re-linkear el mismo índice reemplaza) → `retry_safe=True`.
  - (fast-follow) `unlink_account(account_id, index)` →
    `DELETE /API/Accounts/{id}/LinkAccount/{index}`.
- `app/domain/accounts.py`: `plan_account_link(current: dict, changes: dict)
  -> Plan` — puro. `changes = {link_type: "logon"|"reconcile", linked_safe,
  linked_name, linked_folder}`. `account_link_body(changes)` →
  `{"safe":…, "folder":…, "name":…, "extraPasswordIndex": 1|3}`.
- `app/api/writes.py`:
  - `AccountLinkIn(BaseModel)`: `changes: AccountLinkChanges` (link_type:
    Literal["logon","reconcile"]; linked_safe: str min_length 1; linked_name:
    str min_length 1; linked_folder: str = "Root") + `dry_run` + `reason`.
  - `POST /tenants/{tenant_id}/accounts/{account_id}/link` — `run_write(
    feature="account.link", target=account_id, fetch=get_account,
    build_plan=plan_account_link, apply=lambda c,_: c.link_account(...))`.
  - RBAC `operator+`.

### Frontend

- `lib/types.ts`: nada nuevo obligatorio (reusa `AccountsResponse` para el
  buscador, `WriteResult`/`DryRunResult`).
- `components/AccountLinkSection.tsx` (nuevo) — en el `DetailDrawer` de una
  cuenta, debajo de `<AccountActions>`, visible sólo si
  `enabledFeatures["account.link"]` **y** `myRole >= operator`:
  1. Botón "Vincular cuenta".
  2. Selector de tipo: Logon / Reconciliación.
  3. Buscador de cuenta (input con debounce → `GET /tenants/{t}/accounts?
     search=<q>&limit=10` → lista con `name` · `userName` · `address` ·
     `safeName`). Al elegir, se toma `safeName` + `name` (+ `folder` "Root").
     Link "…o ingresar a mano" → campos safe / name / folder libres.
  4. "Previsualizar" → `<DryRunDialog path="/tenants/{t}/accounts/{id}/link"
     changes={…} title="Vincular cuenta">`.
  - `onDone` → `invalidateAccount(qc, tenant, accountId)` (ya existe).

## Requisitos / escenarios

**R1 — preview:**
Dado una cuenta destino en `lab`, la feature `account.link` on y un
operador `operator+`,
Cuando busca y elige la cuenta `svc-recon` (safe `LinuxRecon`), tipo
"Reconciliación", y previsualiza,
Entonces ve el `effect` + la tabla (tipo, cuenta vinculada, safe, folder),
**no se escribe en CyberArk**, y queda un `account.link` `dry_run:true` en el
audit.

**R2 — ejecución:**
Dado el mismo caso con `dry_run:false` y `reason:"onboarding, reconcile para la plataforma LinuxSSH"`,
Entonces PAM Web Console hace `POST /API/Accounts/{id}/LinkAccount` con
`extraPasswordIndex:3`, responde `ok`, el detalle se refresca, y el audit
tiene `account.link` `dry_run:false` con `reason`, `effect` y el `diff`.

**R3 — auto-vínculo:**
Dado que la cuenta elegida es la misma cuenta destino,
Cuando previsualiza,
Entonces ve el warning "estás vinculando la cuenta a sí misma".

**R4 — reconcile con gestión automática off:**
Dado la cuenta destino con `automaticManagementEnabled:false` y tipo
reconcile,
Cuando previsualiza,
Entonces ve el warning correspondiente.

**R5 — cuenta vinculada inexistente / sin permiso:**
Dado un `linked_name`/`linked_safe` que no resuelve (o el service account no
lo ve),
Cuando ejecuta,
Entonces CyberArk devuelve error, mapeado a HTTP + `result:"error"` con el
detalle.

**R6 — feature off / RBAC / rate-limit:** heredados de `writeop`.

## Tareas

- [x] `app/cyberark/client.py`: `link_account` (`retry_safe=True`) +
      `unlink_account` (para el fast-follow).
- [x] `app/domain/accounts.py`: `plan_account_link` + `account_link_body`
      (`LINK_INDEX = {logon:1, reconcile:3}`).
- [x] `app/api/writes.py`: `AccountLinkChanges` / `AccountLinkIn` + `POST
      /tenants/{id}/accounts/{account_id}/link`.
- [x] Página Tenants: toggle `account.link`.
- [x] Tests unit (`tests/unit/test_account_link.py`, 8): `plan_account_link`
      (reconcile, auto-vínculo, reconcile+gestión off) + `account_link_body`
      (índices) + endpoint (dry-run, ejecución, feature off → denied, viewer
      → 403, validación 422). 114 backend verdes.
- [x] Frontend: `AccountLinkSection` (tipo logon/reconcile + buscador de
      cuenta reusando `/accounts?search=` + modo manual + gating), wiring en
      `DetailDrawer` (debajo de `<AccountActions>`), `invalidateAccount`.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Probar contra un tenant con la feature habilitada: vincular una cuenta
      de reconcile de prueba, verificar en el PVWA, desvincular a mano.
      Probado contra Globex (2026-09-12).

## Decisiones abiertas

1. **Mostrar los links actuales.** `GET /API/Accounts/{id}` no devuelve las
   cuentas vinculadas de forma estructurada. Sin eso, la sección sólo puede
   "agregar" (re-linkear un índice lo reemplaza, así que no rompe nada). —
   Propongo v1 sin listar los links; investigar en implementación si hay una
   forma de traerlos (p. ej. `platformAccountProperties` con
   `ExtraPass1Name`…) y, si sale, agregar el "desvincular" en el mismo PR.
2. **Índice fijo 1/3 vs libre.** Propongo Logon=1 / Reconcile=3 en la UI, con
   el campo de índice editable sólo en el modo "a mano" para plataformas
   raras.
3. **`folder`.** Casi siempre "Root". Propongo default "Root" editable en el
   modo manual.
