# alta-cuenta-safe — Crear cuenta (wizard estilo PVWA) y crear safe

- **Estado:** hecha. Wizard de `account.create` (con y sin contraseña,
  Additional Properties, Remote Machine Access) y `safe.create` probados
  contra Globex (2026-09-12).
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — "Crear cuenta con plantilla por
  cliente (`account.create`)" y "Crear safe con plantilla (`safe.create`)"
- **Fecha:** 2026-09-11. Revisada dos veces sobre la marcha:
  1. Feedback de Alex: el flujo de `account.create` tiene que calcar el
     wizard "Add Account" del PVWA, no un formulario con plantillas armadas
     a mano.
  2. **Hallazgo al probar contra Globex:** "Get Platform Details"
     (`GET /API/Platforms/{id}`) **no expone las propiedades Required/
     Optional de la plataforma** — devuelve la política de gestión de
     contraseñas del CPM (intervalos, complejidad, notificaciones…). El
     esquema de campos del "Add Account" de PVWA (Log On To, User DN, Port,
     Comments, etc.) vive en `Policies.xml` (safe `PVWAConfig`), sin API
     pública — confirmado contra la documentación oficial de CyberArk. Por
     eso "Additional Properties" pasó de "leído en vivo de la plataforma" a
     una **lista libre clave/valor** que arma el operador (con sugerencias
     de nombres comunes por tipo de sistema, sólo de referencia, nunca
     desde CyberArk). También aparecieron dos campos que faltaban del todo
     (no son propiedades de plataforma, son campos del objeto Account):
     "Allow automatic password management" (`secretManagement.
     automaticManagementEnabled`) y "Remote Machine Access"
     (`remoteMachinesAccess`).
- **Base:** `docs/specs/done/fase-2-escritura.md` (maquinaria `writeop`),
  `docs/specs/safe-member-add.md` (patrón de op de "alta" con `Plan.effect` +
  diff `null → valor`).

## Problema

`account.create` y `safe.create` son las dos operaciones de más valor que
quedan en Fase 2: la otra mitad del onboarding (la primera mitad, "agregar
miembro a safe", ya está). Hoy se hacen a mano en el PVWA de cada cliente.

El wizard "Add Account" del PVWA no es un formulario fijo: **el tipo de
sistema (Windows, \*NIX, Database, Network Device, Application, Remote
Access, Website, …) determina qué plataformas se pueden elegir, y la
plataforma elegida determina qué campos adicionales pide** (una Windows
Domain pide `LogonDomain`; una \*NIX puede pedir configuración de sudo; cada
cliente además tiene sus propias plataformas custom).

La idea original era leer esos campos en vivo de CyberArk (para no
mantenerlos a mano y que nunca se desincronicen). **Se probó y la API no lo
soporta**: "Get Platform Details" sólo trae la política del CPM, no el
esquema de campos del Add Account (eso vive en `Policies.xml`, sin API
pública — ver la nota de la cabecera). Sin una fuente confiable, la sección
"Additional Properties" queda como una lista libre que arma el operador —
que de todas formas ya sabe qué pide cada plataforma en cada cliente por
usar PVWA a diario.

## Alcance

**Entra:**
- **`account.create`**, wizard de 4 pasos calcado del "Add Account" de
  PVWA:
  1. **Tipo de sistema** — agrupado desde las plataformas activas del
     tenant (`general.systemType` de `GET /platforms`, que ya existe).
  2. **Plataforma** — filtradas por el tipo elegido.
  3. **Safe** — de las safes del tenant (ya existe `safe-names`).
  4. **Campos**: `Address`, `User Name`, toggle "Customize account name"
     (apagado por defecto — si no se activa, CyberArk arma el nombre) +
     `Account Name` sólo si se activa, `Password`/`Confirm Password`
     (opcional), toggle "Allow automatic password management" (encendido
     por defecto; si se apaga, pide un motivo obligatorio —
     `secretManagement.automaticManagementEnabled`/`manualManagementReason`,
     campos del Account, no de la plataforma), "Remote Machine Access"
     (direcciones separadas por `;` + toggle "restringir a estas máquinas
     únicamente" — `remoteMachinesAccess`, típico de cuentas de dominio), y
     **"Additional Properties"**: lista libre clave/valor que arma el
     operador (con sugerencias de nombres comunes por tipo de sistema —
     ej. Windows: `LogonDomain`/`Port`/`UserDN`/`Comments` — como atajo de
     UI, nunca leídas de CyberArk; ver Hallazgo en la cabecera).
- **`safe.create`**: Safe Name, descripción, CPM, retención, OLAC — el
  formulario estándar de "Add Safe" del PVWA. Sin wizard por tipo de sistema
  (no aplica a safes). Para no pedirle al operador que recuerde la
  convención del cliente (qué CPM, qué retención), PAM Web Console **sugiere
  valores por defecto calculados de las safes que ya existen en ese
  tenant** (el CPM y la retención más frecuentes) — se pueden cambiar antes
  de crear.
- Ambas vía `writeop.run_write` (feature-flag propia, `reason` obligatoria,
  dry-run + diff "de alta", rate-limit, audit), igual que el resto de
  Fase 2.
- UI: botón "Crear cuenta" / "Crear safe" en el toolbar de las tabs Cuentas
  / Safes del Inventario, gateado por `enabled_features` + `my_role`.

**Fuera (explícitamente, con motivo):**
- **Alta masiva** (CSV, varias cuentas/safes de una) → Fase 4 (job).
- **Filtrar plataformas por `AllowedSafes`** contra la safe elegida (PVWA lo
  hace) — v1 no lo valida; si la safe no matchea, CyberArk devuelve el error
  y se muestra tal cual. Se agrega si molesta en la práctica.
- **Cualquier tabla de "plantillas" mantenida a mano en PAM Web Console** — se
  descarta a favor de leer la plataforma real de CyberArk (ver Problema) y
  de las sugerencias auto-calculadas para `safe.create`. Si en el uso real
  se necesita guardar combinaciones frecuentes (ej. "para Globex, VMs
  Windows siempre van a la safe X"), es una spec aparte, más chica, sobre
  esta base.

## Decisión de seguridad: el secreto inicial

A diferencia de la primera versión de esta spec (que excluía fijar el
secreto inicial), PVWA sí lo pide en el wizard y Alex lo confirmó como
necesario. Esto es una excepción deliberada al principio "todo se audita"
de `CLAUDE.md` — se documenta así, explícitamente, para que quede claro que
es la única:

- El campo `secret` viaja **una vez**, del formulario al `POST /API/Accounts`,
  y **nunca** se guarda en `changes` tal cual para el audit: el diff y el
  `detail` del audit muestran `{"field": "contraseña", "from": null, "to":
  "(definida)"}` — nunca el valor. `build_account_create_body` es el único
  lugar que lo lee del dict `changes`; `plan_account_create` (lo que arma el
  diff) no lo toca.
- "Confirm Password" es **solo del frontend** — nunca se manda al backend.
- CyberArk no devuelve el secreto en la respuesta de creación, así que
  `after_obj` (lo que queda en el audit como "objeto creado") ya sale limpio
  sin nada que enmascarar ahí.
- Si el operador no pone contraseña, la cuenta queda `secretManagement.status:
  "pending"` (igual que en PVWA) — se puede fijar después con `account.link`
  (reconcile account) + `account.change` (Reconcile), ambos ya
  implementados.

## Requisitos

### `account.create` — wizard

**R1 — tipos de sistema:**
Dado un operador `operator+` sobre `globex` (que tiene varios tipos de
plataforma) con la feature `account.create` en `true`,
Cuando abre "Crear cuenta",
Entonces el paso 1 lista los `systemType` distintos de las plataformas
**activas** del tenant (ej. Windows, \*NIX, Database, Network Device,
Application, Remote Access, Website, Imported Platforms) — sin llamar a
CyberArk de nuevo (reusa `GET /tenants/{id}/platforms`, ya cacheado por
TanStack Query en el front). Un tenant con menos tipos (ej. Acme) ve solo
los suyos.

**R2 — plataformas del tipo:**
Cuando elige "Windows",
Entonces el paso 2 lista solo las plataformas activas con
`general.systemType == "Windows"` (nombre + id).

**R3 — campos del paso 4:**
Cuando elige la plataforma `WinDomain` y ya eligió la safe `WIN-Prod`,
Entonces el paso 4 muestra los campos fijos (Address, User Name, Customize
account name, Password/Confirm, Allow automatic password management,
Remote Machine Access) y la sección "Additional Properties" con chips de
sugerencia según el `systemType` (`LogonDomain`, `Port`, `UserDN`,
`Comments` para Windows) — un clic agrega la fila (nombre editable +
valor); el operador puede agregar cualquier otra a mano. No hay llamada a
CyberArk para armar este paso (ver Hallazgo en la cabecera).

**R4 — preview:**
Dado los datos completos del paso 4 (sin contraseña, para este caso),
Cuando previsualiza,
Entonces ve el `effect` ("Se crea la cuenta **svc-app@10.1.2.30** en la
safe **WIN-Prod** con la plataforma **WinDomain**.") + diff `null → valor`
por cada campo con valor (safe, plataforma, userName, address, y cada
propiedad no vacía) — **sin fila de contraseña** porque no se puso —, no se
escribe en CyberArk, y queda `account.create` `dry_run:true` en el audit.

**R5 — preview con contraseña:**
Igual que R4 pero con `Password`/`Confirm Password` completos y coincidentes,
Entonces el diff agrega `{"field": "contraseña", "from": null, "to":
"(definida)"}` — nunca el valor real, ni en la respuesta ni en el audit.

**R6 — contraseñas no coinciden:** validación **de frontend**, no llega a
pedir preview (el botón "Previsualizar" queda deshabilitado hasta que
coincidan o ambas estén vacías).

**R7 — gestión manual sin motivo:**
Dado que el operador apaga "Allow automatic password management" y deja el
motivo vacío,
Cuando previsualiza o ejecuta,
Entonces `422` ("'manual_management_reason' es obligatoria al deshabilitar
la gestión automática") — se valida en el `fetch`, antes de tocar CyberArk
(mismo patrón no auditado que la validación de `reason`, igual que hoy el
`reason` corto).

**R8 — ejecución:**
Dado `dry_run:false` y `reason` válida,
Entonces `POST /API/Accounts`, la respuesta trae el `id` de la cuenta
nueva, el audit registra `account.create` con `dry_run:false`, `reason`,
`effect`, y el objeto creado (sin secreto). La tab Cuentas se refresca.

**R9 — safe inexistente:** `404` ("la safe '<nombre>' no existe en este
tenant"), `account.create` `result:"error"` — el `fetch` valida la safe
antes de armar el plan (`client.get_safe`).

**R10 — plataforma inexistente:** `404` con mensaje claro —
`client.get_platform_details` (usado sólo para confirmar existencia, ver
Hallazgo) levanta `NotFound` si CyberArk devuelve 404.

**R11 — permiso del service account faltante ("Add Accounts"):** `403` con
el texto de CyberArk, `account.create` `result:"error"`.

**R12 — feature apagada / RBAC:** igual que el resto de Fase 2 — `403`
`denied` si la feature está apagada; `403` si el rol efectivo es `viewer`.

### `safe.create`

**R13 — sugerencia de defaults:**
Dado un operador `operator+` sobre `globex` con la feature `safe.create`
en `true`,
Cuando abre "Crear safe",
Entonces PAM Web Console pide `GET /tenants/globex/safes/defaults` (nuevo, auditado
`safe.defaults`) y precarga CPM y retención con los valores **más
frecuentes** entre las safes existentes del tenant (ej. si el 90% usa
`PasswordManager` + 90 días, esos quedan de default). Editable antes de
crear. Si el tenant no tiene safes todavía, no hay sugerencia (campos
vacíos, el operador completa a mano).

**R14 — preview:**
Dado `safeName: WIN-Backup` y los defaults sugeridos sin cambios,
Cuando previsualiza,
Entonces ve el `effect` ("Se crea la safe **WIN-Backup** (CPM
**PasswordManager**, retención 90 días).") + diff `null → valor` (nombre,
descripción si la hay, CPM, retención, OLAC), sin tocar CyberArk.

**R15 — ejecución:** `POST /API/Safes`, audit con el objeto creado,
refresco de la tab Safes.

**R16 — safe duplicada:** CyberArk devuelve `409` → PAM Web Console lo propaga con
`result:"error"` en el audit y mensaje claro en la UI.

**R17 — feature apagada / RBAC:** igual criterio que el resto.

**R18 — rate-limit / circuit breaker:** heredado de `writeop`
(`object_count = 1` en ambas — no dispara el breaker en la práctica, pero
usa el mecanismo común).

## Diseño técnico

### Cliente CyberArk — `app/cyberark/client.py`

```python
async def get_safe(self, safe_id: str) -> dict:
    """GET /API/Safes/{safeUrlId} — valida existencia + trae managingCPM.
    404 si no existe -> lo usa el `fetch` de account.create."""
    return await self.get(f"/API/Safes/{safe_id}")

async def get_platform_details(self, platform_id: str) -> dict:
    """GET /API/Platforms/{platformId} — "Get Platform Details": la
    política de gestión de contraseñas del CPM, NO el esquema de campos del
    Add Account (ver Hallazgo). Se usa sólo para confirmar que la
    plataforma existe (404 si no)."""
    return await self.get(f"/API/Platforms/{platform_id}")

async def create_account(self, body: dict) -> dict:
    """POST /API/Accounts — alta. NO idempotente -> retry_safe=False."""
    data = await self.post("/API/Accounts", json=body, retry_safe=False)
    return data if isinstance(data, dict) else {}

async def create_safe(self, body: dict) -> dict:
    """POST /API/Safes — alta. NO idempotente -> retry_safe=False."""
    data = await self.post("/API/Safes", json=body, retry_safe=False)
    return data if isinstance(data, dict) else {}
```

### Dominio — `app/domain/accounts.py` (agrega)

```python
def build_account_create_body(changes: dict) -> dict:
    """`changes['properties']` es la lista libre clave/valor de
    "Additional Properties" (arma el operador, ver Hallazgo)."""
    body: dict[str, Any] = {
        "safeName": changes["safe_name"],
        "platformId": changes["platform_id"],
        "userName": changes["user_name"],
        "address": changes["address"],
    }
    if changes.get("name"):
        body["name"] = changes["name"]
    if changes.get("secret"):
        body["secret"] = changes["secret"]
        body["secretType"] = "password"
    props = {k: v for k, v in (changes.get("properties") or {}).items() if v}
    if props:
        body["platformAccountProperties"] = props

    # "Allow automatic password management" — sólo se manda si se desactiva.
    if changes.get("automatic_management_enabled") is False:
        sm: dict[str, Any] = {"automaticManagementEnabled": False}
        if changes.get("manual_management_reason"):
            sm["manualManagementReason"] = changes["manual_management_reason"]
        body["secretManagement"] = sm

    # "Remote Machine Access" — típico de cuentas de dominio.
    remote = str(changes.get("remote_machines") or "").strip()
    if remote:
        body["remoteMachinesAccess"] = {
            "remoteMachines": remote,
            "accessRestrictedToRemoteMachines": bool(
                changes.get("access_restricted_to_remote_machines")
            ),
        }
    return body


def plan_account_create(_current: dict, changes: dict) -> Plan:
    """Diff siempre "de alta" (`from: None`). El secreto real NUNCA entra acá
    (sólo `build_account_create_body` lo lee) — el diff muestra
    "(definida)"."""
    diff = [
        {"field": "safe", "from": None, "to": changes["safe_name"]},
        {"field": "plataforma", "from": None, "to": changes["platform_id"]},
        {"field": "usuario", "from": None, "to": changes["user_name"]},
        {"field": "address", "from": None, "to": changes["address"]},
    ]
    if changes.get("name"):
        diff.append({"field": "nombre", "from": None, "to": changes["name"]})
    for k, v in (changes.get("properties") or {}).items():
        if v:
            diff.append({"field": f"props.{k}", "from": None, "to": v})
    if changes.get("automatic_management_enabled") is False:
        reason = changes.get("manual_management_reason") or ""
        diff.append({
            "field": "gestión automática", "from": None,
            "to": f"deshabilitada — {reason}" if reason else "deshabilitada",
        })
    remote = str(changes.get("remote_machines") or "").strip()
    if remote:
        diff.append({"field": "remote machine access", "from": None, "to": remote})
        if changes.get("access_restricted_to_remote_machines"):
            diff.append({"field": "acceso restringido a estas máquinas", "from": None, "to": True})
    warnings = []
    if changes.get("secret"):
        diff.append({"field": "contraseña", "from": None, "to": "(definida)"})
    else:
        warnings.append(
            "la cuenta se crea sin secreto (pendiente); vincule una reconcile "
            "account y ejecute Reconcile para fijarlo"
        )
    effect = (
        f"Se crea la cuenta **{changes['user_name']}@{changes['address']}** "
        f"en la safe **{changes['safe_name']}** con la plataforma "
        f"**{changes['platform_id']}**."
    )
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)
```

### Dominio — `app/domain/safes.py` (agrega)

```python
def suggest_safe_defaults(safes: list[dict]) -> dict:
    """CPM y retención más frecuentes entre las safes existentes (para
    precargar el form de `safe.create`). Puro. Vacío si no hay safes."""
    if not safes:
        return {}
    cpm = Counter(s.get("managingCPM") for s in safes if s.get("managingCPM")).most_common(1)
    days = Counter(s.get("numberOfDaysRetention") for s in safes if s.get("numberOfDaysRetention")).most_common(1)
    versions = Counter(s.get("numberOfVersionsRetention") for s in safes if s.get("numberOfVersionsRetention")).most_common(1)
    out: dict = {}
    if cpm:
        out["managing_cpm"] = cpm[0][0]
    if days:
        out["retention_type"], out["retention_value"] = "days", days[0][0]
    elif versions:
        out["retention_type"], out["retention_value"] = "versions", versions[0][0]
    return out


def build_safe_create_body(changes: dict) -> dict:
    body: dict[str, Any] = {"safeName": changes["safe_name"]}
    if changes.get("description"):
        body["description"] = changes["description"]
    if changes.get("managing_cpm"):
        body["managingCPM"] = changes["managing_cpm"]
    if changes.get("retention_type") == "days":
        body["numberOfDaysRetention"] = changes["retention_value"]
    elif changes.get("retention_type") == "versions":
        body["numberOfVersionsRetention"] = changes["retention_value"]
    body["olacEnabled"] = bool(changes.get("olac_enabled"))
    return body


def plan_safe_create(_current: dict, changes: dict) -> Plan:
    diff = [{"field": "nombre", "from": None, "to": changes["safe_name"]}]
    if changes.get("description"):
        diff.append({"field": "descripción", "from": None, "to": changes["description"]})
    if changes.get("managing_cpm"):
        diff.append({"field": "CPM", "from": None, "to": changes["managing_cpm"]})
    if changes.get("retention_type"):
        unit = "días" if changes["retention_type"] == "days" else "versiones"
        diff.append({"field": "retención", "from": None, "to": f"{changes['retention_value']} {unit}"})
    diff.append({"field": "OLAC", "from": None, "to": bool(changes.get("olac_enabled"))})
    cpm_txt = f" (CPM **{changes['managing_cpm']}**)" if changes.get("managing_cpm") else ""
    effect = f"Se crea la safe **{changes['safe_name']}**{cpm_txt}."
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)
```

### Endpoint de lectura (para `safe.create`) — `app/api/inventory.py` (agrega)

```
GET /tenants/{id}/safes/defaults   viewer+, auditado "safe.defaults"
```

Mismo patrón que `user_detail`/`safe_names` (usa `_run`). Devuelve
`{tenant, defaults: suggest_safe_defaults(safes)}`.

### Endpoints de escritura — `app/api/writes.py` (agrega)

```python
class AccountCreateChanges(BaseModel):
    safe_name: str = Field(min_length=1)
    platform_id: str = Field(min_length=1)
    user_name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    name: str | None = None
    secret: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    automatic_management_enabled: bool = True
    manual_management_reason: str | None = None
    remote_machines: str | None = None
    access_restricted_to_remote_machines: bool = False

class AccountCreateIn(BaseModel):
    changes: AccountCreateChanges
    dry_run: bool = True
    reason: str | None = None

@router.post("/accounts")
async def account_create(tenant_id, body: AccountCreateIn, request, op=Depends(_operator), session=...):
    changes = body.changes.model_dump()

    async def fetch(client: CyberArkClient) -> dict:
        if changes["automatic_management_enabled"] is False and not (
            changes.get("manual_management_reason") or ""
        ).strip():
            raise HTTPException(422, detail="'manual_management_reason' es obligatoria...")
        safe = await client.get_safe(changes["safe_name"])                    # 404 si no existe
        details = await client.get_platform_details(changes["platform_id"])   # sólo valida existencia
        return {"safe": safe, "platform_details": details}

    async def apply(client: CyberArkClient, _plan: Plan) -> dict:
        return await client.create_account(build_account_create_body(changes))

    return await run_write(
        ..., feature="account.create", target=f"{changes['safe_name']}/{changes['user_name']}",
        changes=changes, fetch=fetch, build_plan=plan_account_create, apply=apply,
    )
```

`safe.create` análogo: `SafeCreateChanges` (`safe_name`, `description?`,
`managing_cpm?`, `retention_type: Literal["days","versions"] | None`,
`retention_value: int | None`, `olac_enabled: bool = False`), `target =
changes["safe_name"]`, `fetch` devuelve `{}` (no hay nada que validar antes:
la duplicidad la resuelve CyberArk con `409`, ver R16).

`NotFound`/`HTTPException` ya se usan así en otros módulos; `run_write` ya
mapea `NotFound`/`Forbidden` levantados en `fetch` a 404/403 (`writeop.py`
paso 4) — no hace falta tocar el motor. El `HTTPException(422)` de
`manual_management_reason` faltante se propaga tal cual (mismo patrón no
auditado que la validación de `reason`, ver R7).

### Frontend

- `lib/types.ts`: `SafeDefaults`, `SafeDefaultsResponse`.
- `components/ui/switch.tsx` (nuevo): toggle switch accesible
  (`role="switch"`, botón simple, sin dependencia nueva) — reemplaza los
  checkboxes de "Allow automatic password management"/"Customize account
  name"/"Restringir el acceso a estas máquinas" para calcar la UI de PVWA.
- `components/AccountCreateWizard.tsx` (diálogo de 4 pasos, `max-w-2xl`):
  1. **Tipo de sistema** — tarjetas con ícono (por substring del
     `systemType`, ver `systemTypeIcon`) de los tipos distintos entre las
     plataformas activas de `GET /tenants/{t}/platforms`.
  2. **Plataforma** — buscador + lista filtrada por tipo.
  3. **Safe** — buscador + lista sobre `GET /tenants/{t}/safe-names`.
  4. **Campos**: Address, User Name; switch "Customize account name"
     (apagado por defecto) + Account Name si se activa; Password/Confirm
     (validación de coincidencia en el frontend); switch "Allow automatic
     password management" (+ motivo si se apaga); "Remote Machine Access"
     + switch "restringir a estas máquinas"; sección "Additional
     Properties" — chips de sugerencia (`suggestedProperties(systemType)`,
     sólo UI) + filas libres clave/valor (agregar/editar/quitar).
     "Previsualizar" → `<DryRunDialog path="/tenants/{t}/accounts"
     changes={...} title="Crear cuenta">` (reuso directo).
  - Botón "Crear cuenta" en el toolbar de `AccountsTab` (Inventory.tsx),
    visible si `enabledFeatures["account.create"]` y `myRole >= operator`
    (mismo criterio que `AccountEditSection`).
  - `onDone` → invalida `["inv", tenant, "accounts", ...]`.
- `components/SafeCreateDialog.tsx` (un solo paso): Safe Name, Descripción,
  CPM, Retención (tipo + valor), OLAC — precargado desde
  `GET /tenants/{t}/safes/defaults` al abrir. Igual patrón de
  `<DryRunDialog path="/tenants/{t}/safes" ...>`. Botón en el toolbar de
  `SafesTab`, invalida `["inv", tenant, "safes", ...]`.
- Ambos componentes resuelven `enabled_features`/`my_role` con su propia
  query `["tenant", tenant]` (no hace falta pasarlos como props desde
  `Inventory.tsx`, mismo patrón que `AccountEditSection`).

## Tareas

- [x] `app/cyberark/client.py`: `get_safe`, `get_platform_details` (sólo
      valida existencia, ver Hallazgo), `create_account`, `create_safe`.
- [x] `app/domain/accounts.py`: `build_account_create_body` (+
      `secretManagement`/`remoteMachinesAccess`), `plan_account_create`.
- [x] `app/domain/safes.py`: `suggest_safe_defaults`,
      `build_safe_create_body`, `plan_safe_create`.
- [x] `app/api/inventory.py`: `GET /safes/defaults`.
- [x] `app/api/writes.py`: `AccountCreateChanges`/`In` (+
      `automatic_management_enabled`/`manual_management_reason`/
      `remote_machines`/`access_restricted_to_remote_machines`) + `POST
      /tenants/{id}/accounts`; `SafeCreateChanges`/`In` + `POST
      /tenants/{id}/safes`.
- [x] Tests unit (`test_account_safe_create.py`, 27 casos): `plan_account_create`
      (con/sin secreto — nunca expone el valor —, gestión manual, remote
      machines), `suggest_safe_defaults`/`plan_safe_create`, endpoints
      (dry-run, ejecución, safe/plataforma inexistente → 404, gestión
      manual sin motivo → 422, feature off → denied, viewer → 403, safe
      duplicada → 409).
- [x] Frontend: `AccountCreateWizard` (4 pasos, `ui/switch.tsx`, Additional
      Properties como lista libre + sugerencias); `SafeCreateDialog`;
      botones en `AccountsTab`/`SafesTab`.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Probar contra un tenant real: crear una cuenta de al menos 2 tipos de
      sistema distintos, con y sin contraseña, con Additional Properties y
      con Remote Machine Access; crear una safe usando y sin usar los
      defaults sugeridos; camino de error (safe inexistente, plataforma
      inexistente, gestión manual sin motivo, safe duplicada). Probado
      contra Globex (2026-09-12).

## Decisiones abiertas

1. **Filtrar por `AllowedSafes`** (ver Alcance) — descartado para v1, se
   evalúa si molesta en la práctica.
2. **"Additional Properties" como lista libre** (ver Hallazgo) — si el uso
   real muestra que ciertas combinaciones cliente+plataforma se repiten
   mucho, evaluar una spec chica aparte para guardarlas (no una tabla
   genérica de "plantillas": algo puntual, sobre esta base).
