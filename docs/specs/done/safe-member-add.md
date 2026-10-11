# safe-member-add — Agregar miembro a una safe

- **Estado:** hecha. Probada la escritura contra Globex (2026-09-12).
  Iteración tras probar en read-only: presets = los de PVWA (Connect Only /
  Read Only / Approver / Account Manager / Full / Custom, defs exactas de
  psPAS), checkboxes siempre visibles estilo PVWA, detalle de la safe
  rediseñado (lista de miembros con rol + permisos, ya no una tabla
  apretada).
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — "Agregar miembro a safe
  (`safe.member.add`)"
- **Fecha:** 2026-09-10
- **Base:** `docs/specs/done/fase-2-escritura.md` (maquinaria `writeop`),
  `docs/specs/done/account-change.md` (patrón de op de acción con `effect`).

## Problema

Dar acceso a una safe es *la* tarea de onboarding del equipo PAM: entra un
usuario o un grupo nuevo a un cliente y hay que sumarlo a las safes que le
corresponden con el nivel de permiso adecuado (auditor, conectar, gestionar
cuentas…). Hoy se hace en el PVWA de cada cliente, uno por uno.

PAM Web Console ya tiene el pipeline de escritura (`account.update`, `account.change`) y
ya muestra los miembros de cada safe en el detalle (solo lectura). Falta la
operación que **agrega** un miembro.

## Alcance

**Entra:**
- **Buscar** un principal (usuario / grupo) por término, en todas las fuentes
  que PAM Web Console puede consultar: usuarios del Vault, grupos del Vault, usuarios de
  Identity / directorio (Redrock). Endpoint read-only, auditado.
- **Agregar** ese principal como miembro de **una** safe, con permisos
  elegidos por **preset** (Auditor / Conectar / …) o **personalizado**
  (permiso por permiso, como el "Custom" de PVWA).
- Vía `writeop.run_write`: feature-flag `safe.member.add`, `reason`
  obligatoria, dry-run + preview del objeto `permissions` final, rate-limit,
  audit.
- UI: acción "Agregar miembro" en el detalle de la safe (`DetailDrawer`) —
  buscador → picker de candidatos → selector de permisos → `<DryRunDialog>`.

**Fuera (otras specs / futuro):**
- **Editar** o **quitar** permisos de un miembro existente → Fase 3
  (escritura de alto riesgo, toca accesos ya vigentes).
- Agregar el mismo miembro a **varias safes** de una (por filtro, en lote) →
  Fase 4 (job).
- Crear la safe (`safe.create`) — otra spec.
- Alta del usuario/grupo en el Vault o en Identity — acá el miembro ya
  tiene que existir; si no, CyberArk devuelve error y se muestra.
- Buscar **roles** de Identity para agregarlos como miembro — v1 busca
  usuarios y grupos; el tipo "Role" se soporta en el body pero no en el
  buscador (se puede tipear a mano). Se amplía si se pide.

## Diseño

### Feature-flag

- `safe.member.add` — ya está en `KNOWN_FEATURES`. Se agrega el toggle en la
  página Tenants ("Agregar miembro a safe").

### Búsqueda de principal (read-only)

`GET /tenants/{tenant_id}/member-search?q=<term>&kind=<user|group|any>`
(rol `viewer`, `require_tenant_access`; auditado `member.search` con
`detail={q, kind, count}`).

Fan-out en paralelo (limitado por el semáforo del cliente), tolerante a que
una fuente falle (se devuelve lo que haya):

| Fuente | Llamada | Da |
|---|---|---|
| Usuarios del Vault | `GET /API/Users?search=<q>&ExtendedDetails=false` | `userName`, `id`, `source` (`AD`/`Vault`), `userType` |
| Grupos del Vault | `GET /API/UserGroups` (sin paginar) filtrado por `q` en memoria | `groupName`, `id`, `directory` |
| Usuarios de Identity | Redrock `SELECT ID, Username, DisplayName, Email, SourceDs, SourceDsType FROM User WHERE Username LIKE '%q%' OR DisplayName LIKE '%q%' ORDER BY Username COLLATE NOCASE LIMIT 50` | `Username`, `DisplayName`, `Email`, `SourceDs` (nombre del directorio) |

Se dedupe por `(name.lower(), kind)`. Respuesta:

```json
{
  "results": [
    {
      "name": "svc-backup",
      "displayName": "Servicio de backup",
      "email": "svc-backup@cliente.com",
      "kind": "user",              // "user" | "group"
      "source": "Vault",           // etiqueta legible de la fuente
      "searchIn": "Vault",         // lo que va en el body de AddSafeMember
      "alreadyMember": false        // true si ya está en ESTA safe
    }
  ]
}
```

- `searchIn`:
  - Vault (interno) → `"Vault"`.
  - Directorio → el nombre del directorio (`SourceDs` de Redrock / `directory`
    del grupo). **A verificar en implementación contra Globex** (solo
    lectura): confirmar que ese string es el que acepta `AddSafeMember` como
    `searchIn`. Si no matchea limpio, el picker igual muestra la fuente y el
    form deja editar el campo "Buscar en".
- `alreadyMember` se calcula cruzando con `safe_members(safe_id)` **sólo si**
  el front pasa `&safe_id=` (para marcar en el picker los que ya están). Es
  informativo; la validación real vuelve a hacerse en `build_plan`.

### La escritura: op de acción (sin `before`/`after` de campos)

Agregar un miembro no edita campos de un objeto → no hay JSON Patch. Se
modela como las acciones del CPM (`account.change`): `Plan` con `effect` + un
`diff` "de alta". **No hace falta tocar `writeop`** — ya soporta `effect`
(no-op = `not patch and not effect`) y el bloque final audita `effect` + arma
`before/after` desde `diff` (quedan `null → valor`).

- `Plan.patch = []`
- `Plan.effect = "Se agrega a **<nombre>** (<tipo>) como miembro de esta safe."`
- `Plan.diff`:
  - `{field: "miembro", from: null, to: "<nombre> (<tipo>)"}`
  - `{field: "buscar en", from: null, to: "<searchIn>"}`
  - `{field: "preset", from: null, to: "<preset o 'personalizado'>"}`
  - `{field: "permisos", from: null, to: "useAccounts, listAccounts, …"}`
    (los flags en `true` del objeto final resuelto — el operador ve exacto qué
    concede)
  - `{field: "vence", from: null, to: "<fecha ISO>"}` sólo si se pasó
    vencimiento
- `Plan.object_count = 1`

`DryRunDialog` ya sabe: hay `diff` → tabla (Campo / Antes / Después) +
warnings; al confirmar, "Cambio aplicado." (el POST es síncrono, no es acción
de CPM async).

### Presets y permisos

`app/domain/safe_permissions.py` (nuevo — fuente canónica; el reporte
`app/reports/safe_permissions.py` pasa a importar `PERMISSION_KEYS` de acá):

- `PERMISSION_KEYS: list[tuple[str, str]]` — `(key, label_es)` de los ~21
  flags de `permissions` de la API Gen2 (la lista que hoy vive en
  `safe_permissions.py`).
- `PERMISSION_GROUPS: list[tuple[str, list[str]]]` — agrupación para la UI:
  - **Acceso**: `useAccounts`, `retrieveAccounts`, `listAccounts`,
    `accessWithoutConfirmation`
  - **Gestión de cuentas**: `addAccounts`, `updateAccountContent`,
    `updateAccountProperties`, `initiateCPMAccountManagementOperations`,
    `specifyNextAccountContent`, `renameAccounts`, `deleteAccounts`,
    `unlockAccounts`
  - **Gestión de la safe**: `manageSafe`, `manageSafeMembers`,
    `viewSafeMembers`, `viewAuditLog`, `backupSafe`
  - **Workflow**: `requestsAuthorizationLevel1`, `requestsAuthorizationLevel2`
  - **Carpetas**: `createFolders`, `deleteFolders`, `moveAccountsAndFolders`
- `PRESETS: dict[str, dict[str, bool]]` — **exactamente los perfiles de PVWA**
  (los mismos que `Add-PASSafeMember` de psPAS —
  `ConvertTo-SortedPermission`), cada uno expandido a **todas** las keys:

| Preset (PVWA) | `key` | Flags en `true` |
|---|---|---|
| Connect Only | `connect_only` | `useAccounts`, `listAccounts` |
| Read Only | `read_only` | `useAccounts`, `retrieveAccounts`, `listAccounts` |
| Approver | `approver` | `listAccounts`, `manageSafeMembers`, `viewSafeMembers`, `requestsAuthorizationLevel1` |
| Account Manager | `accounts_manager` | Read Only + `addAccounts`, `updateAccountContent`, `updateAccountProperties`, `initiateCPMAccountManagementOperations`, `specifyNextAccountContent`, `renameAccounts`, `deleteAccounts`, `unlockAccounts`, `manageSafeMembers`, `viewAuditLog`, `viewSafeMembers`, `accessWithoutConfirmation` |
| Full | `full` | Account Manager + `manageSafe`, `backupSafe`, `createFolders`, `deleteFolders`, `moveAccountsAndFolders`, `requestsAuthorizationLevel1` |
| Custom | `custom` | lo que tilde el operador |

- Se envía **siempre el objeto `permissions` completo** (igual que psPAS —
  expande el rol y manda `permissions`, no un `permissionSet`): explícito en
  el audit, sin depender de qué enum acepte cada tenant.
- La UI muestra los checkboxes **siempre visibles** (como PVWA): elegir un
  preset tilda los checkboxes; tocar un checkbox pasa a "Custom". `matchPreset`
  vuelve a un preset si los checkboxes coinciden exacto con uno.

### Backend

- `app/cyberark/client.py`:
  - `search_users(q)` → `GET /API/Users?search=<q>` (ya existe `list_users`;
    agregar el `search`).
  - `search_user_groups(q)` → filtra `list_user_groups()` por `q`.
  - `add_safe_member(safe_id, body)`:
    ```python
    async def add_safe_member(self, safe_id: str, body: dict) -> dict:
        """POST /API/Safes/{safeUrlId}/Members — agrega un miembro.
        NO idempotente (2º POST del mismo miembro -> 409) -> retry_safe=False."""
        data = await self.post(f"/API/Safes/{safe_id}/Members", json=body,
                               retry_safe=False)
        return data if isinstance(data, dict) else {}
    ```
- `app/domain/safe_permissions.py` (nuevo): `PERMISSION_KEYS`,
  `PERMISSION_GROUPS`, `PRESETS`, helper `resolve_permissions(preset,
  overrides) -> dict[str, bool]` (arranca del preset, aplica overrides, deja
  todas las keys presentes).
- `app/domain/safes.py` (nuevo):
  - `build_safe_member_plan(current: dict, changes: dict) -> Plan`
    - `changes = {member_name, member_type, search_in, preset, permissions?,
      membership_expiration_date?}` (`permissions` sólo cuando
      `preset == "custom"`; si no, se resuelve del preset).
    - `current = {"members": [...]}` (miembros actuales, para R6).
    - arma `effect` + `diff` + `warnings` (R5, R6). `object_count = 1`.
  - `safe_member_body(changes) -> dict` — a lo que espera CyberArk:
    ```python
    {
      "memberName": member_name,
      "searchIn": search_in,            # "Vault" o el nombre del directorio
      "MemberType": member_type,        # "User" | "Group" | "Role"
      "permissions": resolve_permissions(preset, permissions),
      # "membershipExpirationDate": <epoch> sólo si vino
    }
    ```
- `app/api/inventory.py` (o un router nuevo `search.py` ya existe para
  cross-tenant — acá es dentro de un tenant, va en `inventory.py`):
  `GET /tenants/{id}/member-search` — fan-out + dedupe + `alreadyMember`,
  auditado `member.search`.
- `app/api/writes.py`:
  - `SafeMemberChanges(BaseModel)`: `member_name: str` (min_length 1),
    `member_type: Literal["User","Group","Role"] = "User"`,
    `search_in: str = "Vault"`, `preset: str = "read_only"`
    (`field_validator` contra `PRESET_KEYS`),
    `permissions: dict[str, bool] | None = None`,
    `membership_expiration_date: int | None = None`.
    `SafeMemberAddIn`: `{changes: SafeMemberChanges, dry_run, reason}` (mismo
    formato que `AccountUpdateIn`, para que `<DryRunDialog>` lo mande igual).
  - `POST /tenants/{tenant_id}/safes/{safe_id}/members` — thin, cierra sobre
    `member_body = safe_member_body(body.changes.model_dump())`; llama a
    `run_write(..., feature="safe.member.add", changes=body.changes.model_dump(),
    build_plan=build_safe_member_plan, apply=lambda c,_: c.add_safe_member(...))`.
  - RBAC: `require_tenant_access(OperatorRole.operator)`.

### Frontend

- `lib/types.ts`: `MemberSearchResult`, `MemberSearchResponse`.
- `lib/permissions.ts`: `PERMISSION_LABELS`, `PERMISSION_GROUPS`, `PRESETS`,
  `PRESET_OPTIONS`, `PRESET_LABELS`, `matchPreset` — **copia** del catálogo
  del backend (los presets de PVWA). El backend es la fuente de verdad; el
  front sólo arma el `changes`. También lo usa `DetailDrawer` para etiquetar
  el rol de cada miembro existente.
- `components/SafeMemberAddSection.tsx` (nuevo) — en el `DetailDrawer` cuando
  `isSafe`, debajo de la lista de miembros, visible sólo si
  `enabledFeatures["safe.member.add"]` **y** `myRole >= operator`:
  1. Botón "Agregar miembro" → abre un panel embebido (card sobre `muted/30`).
  2. **Principal**: input con debounce + icono de lupa →
     `GET /tenants/{t}/member-search?q=&safe_id=` → lista de candidatos
     (nombre, displayName, email, tipo, fuente; `alreadyMember` deshabilitado
     con "ya es miembro"). Al elegir, chip con "cambiar". Link "…o ingresar el
     nombre a mano" → campos `memberName` / tipo / `searchIn` libres.
  3. **Permisos** — como PVWA: fila de chips de preset (Connect Only / Read
     Only / Approver / Account Manager / Full) + la grilla de **checkboxes
     siempre visible**, agrupada (`PERMISSION_GROUPS`). Elegir un chip tilda
     los checkboxes; tocar un checkbox recalcula el perfil con `matchPreset`
     (vuelve a "Custom" si no coincide con ninguno). Vencimiento (date,
     opcional).
  4. "Previsualizar" → `<DryRunDialog
     path="/tenants/{t}/safes/{id}/members" changes={...}
     title="Agregar miembro a la safe">`.
  - `onDone` → invalida `["safe-members", tenant, safeId]` + `["audit", ...]`
    (helper `invalidateSafe` en `lib/invalidate.ts`).
- `DetailDrawer` ya recibe `tenant` y `safeId`; pasarle también
  `enabledFeatures` + `myRole` (como el detalle de cuenta con
  `AccountEditSection`).

## Requisitos / escenarios

**R1 — búsqueda:**
Dado un operador `viewer+` sobre `lab`,
Cuando busca `svc` en "Agregar miembro",
Entonces ve candidatos de las tres fuentes (usuarios Vault, grupos Vault,
usuarios Identity) deduplicados, cada uno con su tipo y fuente, y queda un
`member.search` en el audit. Una fuente caída (p. ej. Redrock) no rompe la
búsqueda.

**R2 — preview:**
Dado la feature `safe.member.add` en `true` y un operador `operator+`,
Cuando selecciona el grupo `PAM-Auditors`, preset "Read Only", y
previsualiza,
Entonces ve el `effect` + la tabla (miembro, buscar en, preset, permisos
concedidos), **no se escribe en CyberArk**, y queda un `safe.member.add` con
`dry_run:true` en el audit.

**R3 — ejecución:**
Dado el mismo caso con `dry_run:false` y `reason:"onboarding grupo auditoría, ticket OPS-1234"`,
Entonces PAM Web Console hace `POST /API/Safes/{id}/Members`, responde `ok`, la tabla de
miembros del detalle se refresca y aparece el nuevo miembro, y el audit tiene
`safe.member.add` con `dry_run:false`, `reason`, `effect` y el `diff`.

**R4 — permisos personalizados:**
Dado que el operador parte del preset "Conectar" y tilda además
`retrieveAccounts` y `viewAuditLog`,
Cuando previsualiza,
Entonces el `preset` del diff dice "personalizado" y la fila `permisos`
lista `useAccounts, retrieveAccounts, listAccounts, viewSafeMembers,
viewAuditLog`.

**R5 — permisos de administración:**
Dado que el preset elegido (o los checkboxes) incluyen `manageSafe` o
`manageSafeMembers`,
Cuando previsualiza,
Entonces ve un `warning`: "estos permisos dejan al miembro cambiar la
configuración y los permisos de la safe".

**R6 — el miembro ya existe:**
Dado que `PAM-Auditors` ya es miembro de la safe,
Cuando previsualiza para agregarlo,
Entonces ve un `warning`: "'PAM-Auditors' ya es miembro de esta safe;
agregarlo de nuevo va a fallar. Cambiar los permisos de un miembro existente
es Fase 3". Si igual ejecuta, CyberArk devuelve `409` → `409` con el texto de
CyberArk y `result:"error"` en el audit.

**R7 — feature apagada:**
Dado `safe.member.add` ausente/`false`,
Cuando se llama al endpoint (dry-run incluido),
Entonces `403` y un `safe.member.add` `result:"denied"` con
`reason:"feature_disabled"` en el audit.

**R8 — RBAC:**
Dado un operador con rol `viewer` sobre el tenant,
Cuando llama al endpoint de escritura,
Entonces `403` (no llega a `run_write`). El `member-search` sí le responde
(es `viewer`).

**R9 — el miembro no existe / `searchIn` errado:**
Dado un `memberName` que no resuelve en `searchIn`,
Cuando ejecuta,
Entonces CyberArk devuelve error (`400`/`404`), mapeado a HTTP +
`result:"error"` con el detalle de CyberArk.

**R10 — permiso del service account faltante:**
Dado la feature `true` pero el service account sin "Manage Safe Members" en
esa safe,
Cuando ejecuta,
Entonces `403` con el texto de CyberArk y `safe.member.add`
`result:"error"`.

**R11 — rate-limit:** heredado de `writeop` (un preview no cuenta; ejecutar
cuenta contra `pam:write:<op>:<tenant>`). No-op no aplica (siempre hay
`effect`).

## Tareas

- [x] `app/domain/safe_permissions.py`: `PERMISSION_KEYS`,
      `PERMISSION_GROUPS`, `PRESETS`, `resolve_permissions`, `granted_keys`.
      `app/reports/safe_permissions.py` importa `PERMISSION_KEYS` de acá.
- [x] `app/domain/safes.py`: `build_safe_member_plan`, `safe_member_body`.
- [x] `app/domain/member_search.py`: `merge_member_candidates` +
      `sanitize_search_term` / `redrock_user_search_sql`.
- [x] `app/cyberark/client.py`: `list_users(search=)`, `add_safe_member`
      (`retry_safe=False`).
- [x] `app/api/inventory.py`: `GET /tenants/{id}/member-search` (fan-out con
      `asyncio.gather(return_exceptions=True)` + dedupe + `alreadyMember`,
      auditado `member.search`).
- [x] `app/api/writes.py`: `SafeMemberChanges` + `SafeMemberAddIn` (body
      `{changes, dry_run, reason}`) + `POST
      /tenants/{id}/safes/{safe_id}/members` por `run_write`.
- [x] Página Tenants: toggle "Agregar miembro a safe" (`safe.member.add`).
- [x] Tests unit (`tests/unit/test_safe_member_add.py`, 17): `resolve_permissions`,
      `build_safe_member_plan`, `merge_member_candidates`, `member-search`
      (fuente caída), endpoint (dry-run, ejecución, feature off → denied,
      viewer → 403, preset inválido → 422). 98 backend verdes.
- [x] Frontend: `SafeMemberAddSection` (buscador con debounce + picker +
      preset + checkboxes agrupados + vencimiento + gating por feature/rol),
      `lib/permissions.ts`, `lib/invalidate.ts` (`invalidateSafe`), tipos
      (`MemberSearchResult`/`MemberSearchResponse`), wiring en `DetailDrawer`.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Verificar en el server contra **Globex (solo lectura)**: que el
      `member-search` devuelve resultados sensatos de las 3 fuentes y que el
      `searchIn` de un usuario de directorio es un string usable.
- [x] Probar la escritura contra un cliente con la feature habilitada a
      mano: agregar un grupo de prueba con preset auditor, verlo en el
      detalle y en el reporte safe-permissions, quitarlo a mano del PVWA.
      Probado contra Globex (2026-09-12), no `lab` (sigue con la
      URL rota).

## Decisiones abiertas

1. **`searchIn` de directorio.** Se resuelve del `SourceDs` de Redrock / el
   `directory` del grupo. Si en Globex ese string no es el que acepta
   `AddSafeMember`, el fallback es el campo editable "Buscar en" en el form
   (el operador lo corrige una vez y listo). Se cierra en implementación.
2. **Duplicar presets/labels en el front.** El backend es la fuente de
   verdad (resuelve y valida), pero el front necesita los labels y la
   composición de los presets para la UI sin round-trip. Alternativa: un
   `GET /meta/safe-permissions` que sirva el catálogo. — Propongo duplicar
   (es una tabla estática chica); si crece, se hace el endpoint.
3. **Roles de Identity en el buscador.** v1 busca usuarios y grupos; "Role"
   se soporta en el body pero se tipea a mano. — Se amplía si el equipo los
   usa para safes.
