# account-change — Change / Verify / Reconcile on-demand

- **Estado:** hecha — probada contra Globex en el server 2026-09-10
  (verify: la actividad y el estado del CPM se reflejan; UI del timeline
  aprobada por Alex). Pendiente (no bloquea): probar change/reconcile reales;
  `lab` está inaccesible por un cambio de URL del lado de CyberArk
  (ver notas). Cerrada.
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — "Change / Verify / Reconcile
  on-demand"
- **Fecha:** 2026-09-10
- **Base:** `docs/specs/done/fase-2-escritura.md` (maquinaria `writeop`).

## Problema

Rotar/verificar una credencial "ahora" es tarea diaria del equipo PAM: una
cuenta quedó en `Failure`, alguien sospecha que el secreto se desincronizó,
hay que forzar un cambio por política. Hoy hay que ir a PVWA de cada cliente.
PAM Web Console ya tiene el pipeline de escritura (`account.update`); falta la operación
que **dispara el CPM**.

## Alcance

**Entra:** tres acciones on-demand sobre una cuenta, vía el CPM:

- **Change** — el CPM genera una contraseña nueva y la cambia en el target.
- **Verify** — el CPM chequea que el secreto guardado coincide con el target.
- **Reconcile** — el CPM resetea el secreto usando la reconcile account.

Todas pasan por `writeop.run_write` (feature-flag, `reason`, dry-run,
rate-limit, audit). Botones en el detalle de la cuenta.

**Fuera:**
- **Change to a specific value** (`SetNextPassword` / pasar un secreto
  conocido) — más riesgo, otra spec si se pide.
- Change/verify **en lote** (por safe, por plataforma, por filtro) — Fase 4
  (job).
- Recuperar el secreto para verlo — sigue en PVWA (fuera de alcance del
  proyecto).

## Diseño

### Feature-flags

Dos flags (granularidad útil: un cliente puede permitir verificar pero no
rotar):

- `account.verify` — sólo Verify (bajo riesgo, no cambia nada).
- `account.change` — Change **y** Reconcile (rotan el secreto).

Ambas se agregan a `KNOWN_FEATURES` y a la página Tenants.

### La maquinaria: operaciones "de acción" (sin diff)

Estas ops no editan campos → no hay `before`/`after` ni JSON Patch. Hay que
generalizar un poco `writeop`:

- `Plan` suma un campo opcional `effect: str | None` — descripción legible de
  lo que va a pasar (para el dry-run de las ops de acción).
- `run_write`: `no_op = not plan.patch and not plan.effect`. Si hay `effect`,
  **no** es no-op aunque `patch` esté vacío: en `dry_run` devuelve
  `{effect, warnings}`; al ejecutar llama a `apply` igual.
- El audit de una op de acción: `detail = {reason, effect, warnings}` (sin
  `before`/`after`, que quedan sólo cuando hay diff).
- `DryRunDialog` (front): si `diff` viene vacío pero hay `effect`, muestra el
  texto del `effect` + los `warnings` en vez de la tabla de diff. El resto
  (razón ≥ 8, confirmar) igual.

### Backend

- `app/cyberark/client.py`:
  - `change_account(id)` → `POST /API/Accounts/{id}/Change` (body vacío; el
    CPM genera).
  - `verify_account(id)` → `POST /API/Accounts/{id}/Verify`.
  - `reconcile_account(id)` → `POST /API/Accounts/{id}/Reconcile`.
  - Las tres con `client.post(...)` → `retry_safe=False` (un timeout no debe
    re-disparar el CPM). Devuelven la cuenta si la API la trae, si no `{}`.
- `app/domain/accounts.py`:
  - `plan_cpm(action: str)` → función que, dado `current`, arma el `Plan`:
    - `effect`: p. ej. "El CPM va a **cambiar** la contraseña de esta cuenta
      ahora. La credencial actual dejará de ser válida en cuanto el CPM
      complete el cambio."
    - `warnings` según `current`:
      - `secretManagement.automaticManagementEnabled == false` →
        "la gestión automática está desactivada; el CPM puede ignorar el
        pedido".
      - `secretManagement.status == "InProcess"` → "ya hay una operación de
        CPM en curso para esta cuenta".
      - Reconcile sin reconcile account vinculada → "esta cuenta no tiene
        reconcile account; el CPM no podrá reconciliar".
    - `patch=[]`, `diff=[]`, `object_count=1`.
- `app/api/writes.py`: `POST /tenants/{id}/accounts/{id}/change` `/verify`
  `/reconcile` — tres endpoints thin que llaman a `run_write` con
  `feature = "account.change" | "account.verify" | "account.change"`,
  `build_plan = plan_cpm(<action>)`, `apply = lambda client, _: client.
  <action>_account(account_id)`.

### Requisitos / escenarios

**R1 — Change (dry-run):**
Dado una cuenta gestionada de `lab` y la feature `account.change` en
`true`,
Cuando el operador abre "Cambiar contraseña ahora" y previsualiza,
Entonces ve el texto del efecto + (si aplica) los warnings, **no se llama al
CPM**, y queda un `account.change` con `dry_run:true` en el audit.

**R2 — Change (ejecución):**
Dado el mismo caso con `dry_run:false` y `reason:"cuenta en Failure, fuerzo rotación"`,
Entonces PAM Web Console hace `POST .../Change`, responde `ok`, y el audit tiene
`account.change` con `dry_run:false`, `reason`, y el `effect`. La grilla y el
detalle se refrescan (el `secretManagement.status` puede pasar a "In Process").

**R3 — Verify con su propia feature:**
Dado `account.verify:true` pero `account.change:false`,
Cuando el operador intenta "Cambiar contraseña ahora",
Entonces `403 feature_disabled`; "Verificar" sí funciona.

**R4 — permiso del service account faltante:**
Dado la feature `true` pero el service account sin "Initiate CPM account
management operations",
Cuando ejecuta,
Entonces `403` con el texto de CyberArk y `account.change` `result:"error"`.

**R5 — rate-limit y no-op:** heredados de `writeop` (ídem `account.update`).

### Frontend

- `DetailDrawer` de una cuenta: sección **"Acciones"** (nueva, debajo de
  "Editar metadata") con botones "Cambiar contraseña ahora" / "Verificar" /
  "Reconciliar". Cada uno visible sólo si su feature está habilitada para el
  tenant **y** `my_role >= operator`.
- Cada botón abre `<DryRunDialog>` con el `path` correspondiente y
  `changes = {}` (no hay form). El diálogo muestra el `effect` + warnings.
- `onDone` → invalida `["inv", tenant, "accounts"]`, `["account-detail", …]`,
  `["account-activities", …]`, `["audit", …]` (igual que `account.update` —
  conviene extraer un helper `invalidateAccount(qc, tenant, accountId)`).

## Tareas

- [x] `writeop`: `Plan.effect`; `run_write` trata `effect` como no-no-op;
      audit y respuesta sin `before`/`after` cuando no hay diff.
- [x] `DryRunDialog`: rama "efecto" (texto en vez de tabla de diff).
- [x] `features.py`: `account.verify` + `account.change` en `KNOWN_FEATURES`;
      página Tenants con los tres toggles (update / verify / change+reconcile).
- [x] `client.py`: `change_account` / `verify_account` / `reconcile_account`
      (`POST .../{Change|Verify|Reconcile}`, `retry_safe=False`).
- [x] `domain/accounts.py`: `plan_cpm(action)` — `effect` + warnings
      (gestión automática off; operación de CPM en curso).
- [x] `api/writes.py`: `POST .../accounts/{id}/{change,verify,reconcile}`.
- [x] Tests: `plan_cpm` + endpoints (dry-run, ejecución, feature off por
      acción, change/reconcile con su feature).
- [x] Frontend: `<AccountActions>` (sección "Acciones" en el `DetailDrawer`)
      + `lib/invalidate.ts` (`invalidateAccount`, reusado por edit y actions;
      re-invalida a los 8 s por la latencia del CPM).
- [x] Frontend (UX del async): `<DryRunDialog>` no cierra solo tras una
      acción — muestra "pedido enviado al CPM, revisá la Actividad";
      `<AccountManagementStatus>` (nuevo) — estado del CPM + últimas
      operaciones en el detalle; el detalle + la actividad hacen **poll cada
      5 s mientras `secretManagement.status == InProcess`**; botón
      "Actualizar" en la sección de actividad.
- [x] Frontend (UI de la actividad): `<ActivityFeed>` — timeline con icono
      por tipo de acción, etiquetas en español, tiempo relativo, y acento
      rojo en las fallas. Reemplaza las dos tablas de texto plano
      ("Actividad en CyberArk" y "Actividad en PAM Web Console").
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Probado en el server (2026-09-10): **verify** contra Globex — la
      actividad del CPM y el estado se reflejan en el detalle. **change /
      reconcile** reales: pendiente (probar cuando haya una cuenta de lab
      accesible; `lab` cayó por un cambio de URL de CyberArk).

## Notas

- Change/Reconcile **rotan** el secreto — no es "reversible" en el sentido
  literal, pero sí en el del PLAN (no destruye datos ni config; el CPM lo
  hace en cada rotación programada). El `effect` del dry-run tiene que dejar
  claro el impacto.
- La operación del CPM es **asíncrona**: el `200` de la API significa "pedido
  encolado", no "cambiado". El resultado real se ve después en
  `secretManagement.status` y en las Activities de la cuenta. La UI no
  espera; muestra "pedido enviado al CPM".
- `POST .../Change` acepta opcionalmente `{"ChangeEntireGroup": true}` para
  las cuentas de un grupo de dependencia — **fuera de alcance** en v1 (sólo
  la cuenta puntual).
