# safe-member-edit — Fase 3: editar / quitar permisos de un miembro de safe

- **Estado:** hecha. Probada contra Globex (2026-09-12): editar permisos
  y quitar un miembro, ambas con confirmación reforzada.
- **Roadmap:** `docs/PLAN.md §7` Fase 3 — "Editar/quitar permisos de
  miembros de safe"
- **Fecha:** 2026-09-12

## Problema

`safe.member.add` (Fase 2) sólo agrega miembros **nuevos** — de hecho ya
avisa explícitamente que "cambiar los permisos de un miembro existente es
Fase 3" (`app/domain/safes.py::build_safe_member_plan`, warning cuando el
nombre ya es miembro). Hoy, ajustar el nivel de acceso de alguien que ya
tiene acceso a una safe, o sacarlo del todo, se hace a mano en el PVWA —
fuera del audit log de PAM Web Console.

Es la **primera operación de Fase 3**: puede dejar a alguien sin acceso a
una safe de producción, o subirle los permisos más de la cuenta. Fase 2 no
tenía nada de esto porque sus 7 operaciones son reversibles editando de
nuevo con datos de bajo impacto; acá el radio de impacto es mayor, así que
la operación se apoya en maquinaria nueva (no sólo en `writeop.run_write`
de Fase 2) — ver la sección "Maquinaria de Fase 3" abajo. Esa maquinaria es
para **toda la fase**, no sólo para esta operación.

## Alcance

**Entra:**

- **Maquinaria de Fase 3 (transversal, la usan todas las próximas
  operaciones de la fase):**
  - **Confirmación reforzada**: además de la `reason`, ejecutar exige que
    el operador tipee una frase exacta (el nombre del miembro) — validado
    en el **backend**, no sólo deshabilitando un botón en la UI.
  - **RBAC más alto**: rol efectivo **`approver+`** sobre el tenant (no
    `operator+` como en Fase 2). Primer uso real del rol `approver` — existe
    en el modelo desde Fase 0 pero ningún endpoint lo exige todavía.
  - Se auditan igual que Fase 2 (`before`/`after`, `dry_run`, `reason`) más
    el nuevo campo `confirm`.
- `safe.member.update` — cambiar permisos / preset / vencimiento de un
  miembro existente. `PUT /API/Safes/{id}/Members/{memberName}`.
- `safe.member.remove` — sacar a un miembro. `DELETE
  /API/Safes/{id}/Members/{memberName}`.
- Frontend: la lista "Miembros" del detalle de una safe gana botones
  **"Editar"** / **"Quitar"** por fila, gateados por feature + rol
  `approver+`. `<DryRunDialog>` gana el paso de confirmación reforzada
  (genérico — lo reusan las próximas operaciones de Fase 3, no sólo ésta).

**Fuera (Fase 4 / futuro, o de otras specs):**

- **Aprobación de un segundo operador** (solicitante → approver
  *distinto*). Alex es el único operador hoy — con un solo operador un
  segundo aprobador no es viable. Fallback: confirmación reforzada + razón
  obligatoria (esta spec). Se agrega cuando haya más operadores, sin
  rediseñar `run_write` (ver Decisiones abiertas).
- **Notificación a Slack.** No hay canal ni webhook hoy (`docs/PLAN.md`,
  Fase 0 → Pendientes #3). Se agrega cuando exista, sin bloquear esto.
- **Snapshot de reversión automático / botón "deshacer".** El `before` del
  audit alcanza para reconstruir el estado anterior a mano (mismo criterio
  que Fase 2) — un snapshot store queda para cuando de verdad haga falta.
- Editar el tipo de miembro o volver a buscarlo — sólo permisos +
  vencimiento (igual alcance que `safe.member.add`).
- El resto de Fase 3 (borrar cuenta/safe, editar safes, CRUD de
  plataformas/usuarios/grupos, terminar sesiones) — cada una su spec propia
  sobre esta misma maquinaria.

## Requisitos

1. **Editar permisos de un miembro existente (dry-run).**
   Dado el grupo `SQL-Readers` es miembro de la safe `SQL-PROD` con preset
   Read Only,
   Cuando el operador pide preset "Accounts Manager" con `dry_run=true`,
   Entonces la respuesta trae el `diff` (permisos que pasan de `false` a
   `true`), `confirm_phrase: "SQL-Readers"`, y no toca CyberArk.

2. **Ejecutar exige que la confirmación coincida.**
   Dado el dry-run anterior,
   Cuando el operador ejecuta con `reason` válida pero `confirm: "SQL-Reader"`
   (typo, sin la "s" final),
   Entonces `422` "la confirmación no coincide", CyberArk no se toca, y el
   audit registra `safe.member.update` con `result: "denied"`,
   `detail.reason: "confirm_mismatch"`.

3. **Quitar un miembro.**
   Dado el usuario `jdoe` es miembro de `SQL-PROD` con preset Approver,
   Cuando el operador confirma con `confirm: "jdoe"` y `reason` válida,
   Entonces `DELETE /API/Safes/SQL-PROD/Members/jdoe`, el miembro
   desaparece de la lista, y el audit `safe.member.remove` trae
   `before: {permissions: {...}, expiration: ...}`.

4. **RBAC: `operator` no llega ni al dry-run.**
   Dado un operador con rol efectivo `operator` (no `approver`) sobre el
   tenant,
   Cuando intenta `safe.member.update` o `safe.member.remove` (dry-run
   incluido),
   Entonces `403` por rol insuficiente, antes de tocar CyberArk o de
   calcular ningún plan.

5. **Miembro que ya no existe.**
   Dado el nombre no aparece en `client.safe_members(safe_id)` al momento
   del `fetch` (alguien lo sacó desde el PVWA mientras tanto),
   Cuando el operador intenta editarlo o quitarlo,
   Entonces `404`, sin tocar CyberArk, `result: "error"` en el audit.

6. **Vaciar los permisos de un miembro por error.**
   Dado el operador destilda todos los checkboxes en "Editar",
   Cuando arma el dry-run,
   Entonces `warnings: ["el miembro se quedaría sin ningún permiso sobre
   la safe."]` (mismo warning que ya existe en `safe.member.add`).

## Diseño técnico

### Maquinaria de Fase 3

**1. Confirmación reforzada — `app/core/writeop.py`.**

`Plan` gana un campo nuevo:

```python
@dataclass
class Plan:
    patch: list[dict]
    diff: list[dict]
    warnings: list[str] = field(default_factory=list)
    object_count: int = 1
    effect: str | None = None
    confirm_phrase: str | None = None   # nuevo — None = Fase 2, sin cambios
```

Sólo lo llenan las operaciones de Fase 3 (`build_safe_member_update_plan`,
`build_safe_member_remove_plan`, ...). `run_write` gana un parámetro
`confirm: str | None = None`:

- El **dry-run** ya devuelve `confirm_phrase` (calculado por `build_plan`,
  disponible sin tocar CyberArk) para que la UI sepa qué pedir.
- Al **ejecutar**, si `plan.confirm_phrase is not None`: `confirm` (del
  body) debe ser **exactamente igual** (case-sensitive, sin strip — el
  nombre puede tener sentido con espacios) o `422` "la confirmación no
  coincide" + audit `denied` / `confirm_mismatch`, **antes** de llamar a
  CyberArk.
- Backward-compatible: las 7 operaciones de Fase 2 no setean
  `confirm_phrase` → siguen funcionando sin el campo nuevo.

**2. RBAC — `app/api/writes.py`.**

```python
_approver = require_tenant_access(OperatorRole.approver)
```

Los dos endpoints nuevos usan `_approver` en vez de `_operator`. El resto
de Fase 2 no cambia.

**3. Feature-flags — `app/core/features.py`.**

`KNOWN_FEATURES` suma `safe.member.update` y `safe.member.remove`. Sin un
set separado de "alto riesgo": alcanza con que la operación llene
`confirm_phrase` en su `Plan` — `run_write` no necesita saber a qué fase
pertenece una feature.

### Cliente CyberArk — `app/cyberark/client.py`

```python
async def update_safe_member(self, safe_id: str, member_name: str, body: dict) -> dict:
    """PUT /API/Safes/{safeUrlId}/Members/{memberName} — permisos/vencimiento
    de un miembro existente. Idempotente -> retry_safe=True (default)."""
    path = f"/API/Safes/{safe_id}/Members/{quote(member_name, safe='')}"
    data = await self._request("PUT", path, json=body)
    return data if isinstance(data, dict) else {}

async def remove_safe_member(self, safe_id: str, member_name: str) -> None:
    """DELETE /API/Safes/{safeUrlId}/Members/{memberName}."""
    path = f"/API/Safes/{safe_id}/Members/{quote(member_name, safe='')}"
    await self.delete(path)
```

`quote(member_name, safe="")` (de `urllib.parse`): los nombres de
directorio (`DOMAIN\jdoe`) traen `\` y a veces espacios, que no son válidos
sin codificar en un segmento de path. El resto del cliente no lo necesitó
hasta ahora porque los otros IDs de path (`account_id`, `safe_id`) son IDs
opacos de CyberArk, no nombres con caracteres arbitrarios.

`_request` necesita el verbo `PUT` — hoy sólo tiene `patch`/`post`/`delete`
como wrappers; se agrega `put()` o se llama `_request("PUT", ...)`
directo (a definir en la implementación, no cambia el contrato).

### Dominio — `app/domain/safes.py`

Reusa `app/domain/safe_permissions.py` tal cual (mismos presets, mismo
`ADMIN_KEYS` para el warning).

```python
def build_safe_member_update_plan(current: dict, changes: dict) -> Plan:
    """`current` = {"members": [...]} (fetch trae la lista completa, igual
    que safe.member.add). Busca el miembro por nombre (404 si no está),
    compara permisos actuales vs pedidos -> diff sólo de las keys que
    cambian, confirm_phrase = el nombre exacto."""

def build_safe_member_remove_plan(current: dict, changes: dict) -> Plan:
    """Sin diff (es una acción) -> `effect` legible + confirm_phrase."""
```

Ambas levantan `NotFound` si `changes["member_name"]` no está en
`current["members"]` (mismo patrón de `fetch`+`build_plan` de Fase 2: el
`fetch` trae el estado real justo antes de decidir).

### Endpoints — `app/api/writes.py`

```
PUT    /tenants/{id}/safes/{safe_id}/members/{member_name}   (safe.member.update)
DELETE /tenants/{id}/safes/{safe_id}/members/{member_name}   (safe.member.remove)
```

Body (ambos, estilo Fase 2): `{ changes?, dry_run, reason, confirm }`. Para
`remove`, `changes` puede ir vacío (no hay campos que editar, sólo
confirmar); para `update`, `changes` trae `preset`/`permissions`/
`membership_expiration_date` (mismo shape que `SafeMemberChanges`, sin
`member_type`/`search_in` — no se pueden cambiar).

`fetch` de ambos: `{"members": await client.safe_members(safe_id)}` (mismo
que `safe.member.add`, para no pedir todos los miembros dos veces con
formas distintas).

### Auditoría

Igual que Fase 2 (`before`/`after` o `effect`, `reason`, `dry_run`) más:
- `detail.confirm_ok: true|false` en la ejecución (para distinguir de un
  típo en el reason si algo falla).
- El `dry-run` que muestra `confirm_phrase` también se audita (ya pasa por
  default en `run_write`).

### Frontend

- **`<DryRunDialog>`**: si `preview.data.confirm_phrase` viene seteado,
  agrega un segundo input **debajo** de la razón: "Para confirmar, escribí
  `<confirm_phrase>` exactamente" (el placeholder muestra la frase). El
  botón "Confirmar" se deshabilita hasta que el valor tipeado sea idéntico
  **y** la razón tenga el mínimo. El body de la ejecución manda `confirm`.
  Genérico: cualquier operación futura de Fase 3 que llene `confirm_phrase`
  lo hereda gratis.
- **Lista "Miembros" del `DetailDrawer`** (hoy sólo lectura): cada fila
  gana dos botones chicos "Editar" / "Quitar", visibles sólo si
  `enabled_features["safe.member.update"/"safe.member.remove"]` **y**
  `my_role` ≥ `approver`. "Editar" abre un panel inline (mismo layout de
  presets + checkboxes que `SafeMemberAddSection`, precargado con los
  permisos actuales) → `<DryRunDialog>`. "Quitar" va directo a
  `<DryRunDialog>` (no hay campos que llenar, el `effect` describe la
  acción).
- Nuevo componente `SafeMemberEditSection` (edición) reusa
  `lib/permissions.ts` tal cual; "Quitar" no necesita componente propio,
  sólo el dialog con `changes: {member_name}`.

## Tareas

- [x] `app/core/writeop.py`: `Plan.confirm_phrase`, parámetro `confirm` en
      `run_write`, chequeo antes de `apply`, `confirm_phrase` en la
      respuesta de dry-run.
- [x] `app/core/features.py`: `safe.member.update` + `safe.member.remove`
      en `KNOWN_FEATURES`.
- [x] `app/cyberark/client.py`: `update_safe_member`, `remove_safe_member`
      (URL-encoding del `memberName`), wrapper `put()`.
- [x] `app/domain/safes.py`: `find_member`, `build_safe_member_update_plan`,
      `build_safe_member_remove_plan`, `safe_member_update_body`. El
      `member_name` de la URL es la fuente de verdad — no se duplica en
      `changes`; el 404 de "no es miembro" se levanta en el `fetch` del
      endpoint (dentro del try/except de `run_write`), no en `build_plan`.
- [x] `app/api/writes.py`: `_approver` dependency, endpoints
      `PUT`/`DELETE .../members/{member_name}`, Pydantic
      `SafeMemberUpdateIn`/`ConfirmedActionIn`.
- [x] Tests unit (`test_safe_member_edit.py`, 18 casos): confirm
      match/mismatch, RBAC operator (403) vs approver, miembro inexistente
      (404), warnings (sin permisos / admin), diff sólo de lo que cambia,
      no-op sin cambios, `find_member` case-insensitive.
- [x] Frontend: `<DryRunDialog>` con el paso de confirmación reforzada
      (prop `method` para PUT/DELETE) + `SafeMemberRow.tsx` (Editar/Quitar
      por fila en la lista de Miembros del detalle de safe).
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Probar contra Globex: editar permisos de un miembro y quitar un
      miembro, ambas con confirmación reforzada. Confirmado 2026-09-12.

## Decisiones abiertas

1. **`confirm_phrase` = nombre exacto del miembro** (case-sensitive, estilo
   "escribí el nombre del repo para borrarlo") vs. una frase genérica fija
   ("QUITAR" / "EDITAR"). Propongo el nombre exacto: obliga a mirar qué se
   está tocando, no sólo a clickear más rápido.
2. **RBAC `approver+`** vs. dejar `operator+` y confiar sólo en la
   confirmación reforzada. Propongo `approver+`: es la primera vez que el
   rol se usa para algo, y separa "quién puede tocar cosas de bajo riesgo"
   de "quién puede tocar cosas que revocan/cambian acceso".
3. **Aprobación de un segundo operador**: quién es hoy el segundo
   aprobador si Alex es el único operador — no hay uno. Se documenta como
   gap conocido (igual que ya anota `docs/PLAN.md`, Fase 0 → Pendientes
   #4) y se resuelve cuando el equipo PAM crezca, sin tocar esta spec (el
   `confirm` de hoy no compite con un futuro `approved_by`, son campos
   distintos).
4. **Verbo del endpoint**: `PUT/DELETE .../members/{member_name}` (REST
   puro, coherente con la API real de CyberArk que esto envuelve) vs. el
   estilo `POST .../update` que usa el resto de Fase 2. Propongo PUT/DELETE
   acá porque hay un recurso identificable por URL (el miembro), a
   diferencia de las acciones (`change`/`verify`) que no lo tienen.
