# safe-update — Fase 3: editar una safe (descripción, CPM, retención, OLAC)

- **Estado:** hecha. Probada contra Globex (2026-09-12).
- **Roadmap:** `docs/PLAN.md §7` Fase 3 — "Editar safes (retención, OLAC,
  CPM)"
- **Fecha:** 2026-09-12

## Problema

`safe.create` (Fase 2) da de alta una safe, pero ajustar la configuración de
una que ya existe (cambiar el CPM que la gestiona, la política de
retención, activar OLAC, corregir la descripción) hoy se hace a mano en el
PVWA. Es Fase 3 (no Fase 2) porque, a diferencia de editar la metadata de
una cuenta puntual, cambiar el CPM o la retención de una safe **afecta a
todas las cuentas que vive ahí** — puede cortar la rotación automática de
decenas de cuentas si se apunta a un CPM mal configurado, o reducir cuántas
versiones de contraseñas anteriores quedan disponibles.

## Alcance

**Entra:**
- `safe.update` — `PUT /tenants/{id}/safes/{safe_id}`: editar `description`,
  `managing_cpm`, retención (`days` o `versions`, mutuamente excluyentes,
  igual que `safe.create`) y `olac_enabled`.
- Misma maquinaria de Fase 3 ya probada en `safe.member.update`/`remove`:
  RBAC `approver+`, confirmación reforzada (`confirm` = el nombre de la
  safe).
- Botón "Editar" en el detalle de una safe (`DetailDrawer`), gateado por
  `enabled_features["safe.update"]` + `my_role ≥ approver`.
- Warnings específicos: cambiar el CPM avisa que afecta la rotación de
  todas las cuentas de la safe; bajar la retención avisa que reduce
  versiones/días disponibles (no revierte lo ya purgado).

**Fuera:**
- **Renombrar la safe** (`safeName`) — CyberArk lo trata distinto (mueve
  contenido, referencias rotas en otros lados) y no está en el pedido del
  roadmap ("retención, OLAC, CPM"), no en el nombre.
- Cambiar `location`, `numberOfVersionsRetention`/`numberOfDaysRetention`
  simultáneos (la API real es mutuamente excluyente, igual que en
  `safe.create`).
- Borrar la safe — spec propia ("Borrar cuenta / safe", más adelante en el
  roadmap de Fase 3).

## Requisitos

1. **Editar CPM y retención (dry-run).**
   Dado la safe `SQL-PROD` con `managingCPM=CPM1`, retención de 30 días,
   Cuando el operador pide `managing_cpm=CPM2`, `retention_type=days`,
   `retention_value=90` (`dry_run=true`),
   Entonces el diff muestra `CPM: CPM1 -> CPM2` y `retención: 30 días -> 90
   días`, `confirm_phrase: "SQL-PROD"`, sin tocar CyberArk. Warning: "cambiar
   el CPM afecta la rotación de todas las cuentas de esta safe."

2. **Confirmación reforzada obligatoria.**
   Dado el dry-run anterior,
   Cuando el operador ejecuta con `reason` válida y `confirm: "SQL-Prod"`
   (case distinto),
   Entonces `422` "la confirmación no coincide", CyberArk no se toca.

3. **Bajar la retención dispara un warning.**
   Dado la safe retiene 90 días,
   Cuando el operador pide `retention_value=30`,
   Entonces `warnings: ["se reduce la retención de 90 a 30 días; las
   versiones/días fuera de la nueva ventana no se recuperan."]`.

4. **Activar OLAC.**
   Dado `olacEnabled=false`,
   Cuando el operador pide `olac_enabled=true`,
   Entonces diff `OLAC: false -> true` y warning "OLAC activa listas de
   control de acceso por objeto — revisar los permisos de los miembros
   después de activarlo."

5. **RBAC: `operator` rechazado.**
   Igual que `safe.member.update` — `403` antes de tocar CyberArk.

6. **Sin cambios reales.**
   Dado `changes` idéntico al estado actual,
   Cuando se ejecuta,
   Entonces `no_op: true`, no se llama a CyberArk.

## Diseño técnico

### Cliente CyberArk — `app/cyberark/client.py`

```python
async def update_safe(self, safe_id: str, body: dict) -> dict:
    """PUT /API/Safes/{safeUrlId} — Update Safe. Idempotente."""
    data = await self.put(f"/API/Safes/{safe_id}", json=body)
    return data if isinstance(data, dict) else {}
```

### Dominio — `app/domain/safes.py`

```python
def build_safe_update_plan(current: dict, changes: dict) -> Plan:
    """`current` = el objeto de `get_safe` (fetch). Compara description/
    managing_cpm/retención/olac_enabled contra `changes`; diff sólo de lo
    que cambia. confirm_phrase = current['safeName']."""

def safe_update_body(current: dict, changes: dict) -> dict:
    """Cuerpo de PUT /API/Safes/{id} — parte de `current` (la API puede
    exigir el objeto completo, no un patch parcial) y sólo pisa los campos
    presentes en `changes`."""
```

Igual criterio que `build_safe_create_body`/`plan_safe_create` para la
retención mutuamente excluyente (si `retention_type=="days"` sólo manda
`numberOfDaysRetention`, y viceversa).

### Endpoint — `app/api/writes.py`

```
PUT /tenants/{id}/safes/{safe_id}
Body: { changes: {description?, managing_cpm?, retention_type?,
        retention_value?, olac_enabled?}, dry_run, reason, confirm }
```

RBAC `_approver`. `fetch` = `client.get_safe(safe_id)` (404 nativo de
CyberArk si no existe, ya mapeado por el try/except de `run_write`).

### Selector de CPM — `GET /tenants/{id}/cpm-names`

CyberArk no expone un "listar CPMs disponibles" directo. En vez de texto
libre (la propuesta original), se infieren los CPMs de las safes que **ya
existen** en el tenant — mismo dato que ya calcula `suggest_safe_defaults`,
sólo que como lista completa en vez de "el más frecuente":

```python
def list_cpm_names(safes: list[dict]) -> list[str]:
    return sorted({s["managingCPM"] for s in safes if isinstance(s, dict) and s.get("managingCPM")})
```

`GET /cpm-names` reusa `safes_rows_cached()` (el mismo caché de Redis que ya
usan `/safe-names` y `/safes/defaults` — sin round-trip extra a CyberArk).
Frontend: `CpmField.tsx` (nuevo, compartido por `SafeCreateDialog` y
`SafeEditSection`) — `<Select>` con los CPMs conocidos + "…o ingresarlo a
mano" para uno que todavía no gestiona ninguna safe (mismo patrón
buscador/manual que `SafeMemberAddSection`).

### Frontend

- `SafeEditSection.tsx` (nuevo, mismo patrón que `AccountEditSection`):
  botón "Editar safe" en el detalle de una safe → form con `description` /
  `CpmField` / retención (select días vs versiones + número) / checkbox
  OLAC, prefilled con el objeto actual (`props` del `DetailDrawer`, mismo
  dato que ya trae la grilla) → `<DryRunDialog method="PUT">`.

## Tareas

- [x] `app/core/features.py`: `safe.update` en `KNOWN_FEATURES`.
- [x] `app/cyberark/client.py`: `update_safe`.
- [x] `app/domain/safes.py`: `build_safe_update_plan`, `safe_update_body`,
      `list_cpm_names`.
- [x] `app/api/inventory.py`: `GET /cpm-names` (reusa `safes_rows_cached`).
- [x] `app/api/writes.py`: endpoint `PUT /safes/{safe_id}`, RBAC `_approver`,
      invalida el caché de safes al ejecutar (el CPM puede cambiar).
- [x] `pages/Tenants.tsx`: agregado `safe.update` a la lista `FEATURES` (no
      se repitió el gotcha de las últimas dos features).
- [x] Tests unit (`test_safe_update.py`, 19 casos): diff CPM/retención/OLAC,
      warnings, confirm mismatch, RBAC, no-op, retención mutuamente
      excluyente, `list_cpm_names`.
- [x] Frontend: `CpmField.tsx`, `SafeEditSection.tsx`, wiring en
      `DetailDrawer` y en `SafeCreateDialog` (mismo selector para crear).
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Probar contra Globex. Confirmado 2026-09-12.

## Decisiones abiertas

1. ~~Lista de CPMs para el selector~~ — **resuelta**: se infiere de las
   safes existentes (`/cpm-names`), con fallback manual. Ver "Selector de
   CPM" arriba.
2. **¿OLAC se puede desactivar una vez activado?** A confirmar contra el
   comportamiento real de CyberArk al probar — si no se puede, el warning
   actual ("puede no estar soportado... si falla, es un límite de la
   plataforma") ya cubre el caso; si hace falta, se puede bloquear
   `olac_enabled: false` cuando ya está en `true` en una iteración
   siguiente.
