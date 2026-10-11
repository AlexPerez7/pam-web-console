# account-password-versions — pestaña "Versiones" con historial de contraseñas

- **Estado:** aprobada
- **Roadmap:** `docs/PLAN.md §6` (Accounts), extra a pedido de Alex
  (2026-09-16), comparando contra la pestaña "Versions" real del PVWA
- **Fecha:** 2026-09-16

## Problema

El PVWA tiene una pestaña "Versions" en el detalle de cuenta con el
historial de contraseñas anteriores (fecha, hora, quién la modificó) y un
botón "Show"/"Copy" por fila para revelar cada versión histórica. PAM Web Console no
tiene equivalente — el detalle de cuenta (`AccountDetailPanel.tsx`) sólo
tiene Resumen/Detalles/Actividad.

Esto es distinto de `account.reveal` (revela la contraseña **vigente**) y
de los backups de `account.delete` (revela el secreto de una cuenta **ya
borrada**, guardado localmente cifrado): acá se trata del historial de
contraseñas de una cuenta **activa**, todas en vivo contra CyberArk.

## Investigación: endpoints

`docs.cyberark.com` bloqueó el fetch automatizado (mismo patrón de
siempre) — confirmado vía el código fuente de `psPAS`
(`Get-PASAccountPasswordVersion.ps1` / `Get-PASAccountPassword.ps1`):

```
GET  /API/Accounts/{id}/Secret/Versions?showTemporary=<bool>   Listar versiones (metadata)
POST /API/Accounts/{id}/Password/Retrieve   {"reason": "...", "Version": N}
```

El endpoint de retrieve es **el mismo** que ya usa `account.reveal`
(`client.retrieve_password`) — con un `Version` (int) opcional en el body
devuelve el valor histórico en vez del vigente. No hace falta un endpoint
nuevo del lado de CyberArk para revelar, sólo extender el método existente.

**Confirmado contra Globex (2026-09-18):** los nombres de campo
inferidos de `psPAS` (`VersionID`/`ModificationTime`/`ModifiedBy`)
coinciden con la respuesta real de CyberArk — la pestaña "Versiones"
muestra fecha/hora/modificado por correctamente, sin ajustes de código.

## Alcance

**Entra:**
- `GET /tenants/{id}/accounts/{account_id}/password-versions` — lista
  metadata de versiones (sin valores). RBAC `tenant_admin+` (mismo techo
  que backups: exponer la EXISTENCIA/historial de cambios de un secreto ya
  se trata como sensible, aunque no incluya el valor — mismo criterio que
  `GET .../account-backups`). **Sin feature-flag propia** (igual que
  listar backups) — es metadata, no el secreto.
- `POST /tenants/{id}/accounts/{account_id}/reveal-version` — revela el
  valor de UNA versión puntual. RBAC `tenant_admin+` + feature-flag nueva
  `account.reveal.history` (opt-in por tenant, apagada por defecto) +
  `reason` obligatoria — mismo criterio que `account.reveal`, pero
  **separada** de esa feature (mismo patrón que `account.reveal.bulk`:
  mismo nivel de RBAC, radio de exposición distinto — acá potencialmente
  *varias* contraseñas históricas de la misma cuenta, no sólo la vigente
  — así que un tenant puede habilitar una sin la otra).
- Pestaña "Versiones" en `AccountDetailPanel.tsx`: tabla (Fecha, Hora,
  Modificado por, botón "Revelar"). Visible con `tenant_admin+` (aunque la
  feature de reveal esté apagada — se ve el historial, no se puede revelar
  hasta habilitarla, mismo patrón que otras secciones). Cada fila abre
  `RevealPasswordDialog` (razón → valor de sólo lectura + copiar) apuntado
  a `reveal-version` con esa versión puntual.
- `RevealPasswordDialog` gana un prop `extraBody` opcional (mismo patrón
  ya usado por `DryRunDialog`) para mandar `{version}` junto a `reason`.

**Fuera (a propósito):**
- Toggle "Hide CPM temporary password versions" del PVWA — v1 pide
  siempre `showTemporary=false` (oculta las versiones temporales del CPM,
  que es el estado por defecto del PVWA también). Si hace falta verlas,
  spec futura.
- Revelar varias versiones de una — cada "Revelar" es una acción propia
  con su razón, igual que "Show" en el PVWA es por fila.
- Cualquier acción de escritura sobre una versión histórica (ej. "volver
  a esta versión") — el PVWA tampoco la tiene en esta pestaña.

## Requisitos

1. **Listar sin la feature de reveal habilitada.** Dado un tenant sin
   `account.reveal.history`, un `tenant_admin` abre la pestaña Versiones →
   ve la tabla igual (fecha/hora/modificado por), pero el botón "Revelar"
   por fila no aparece (o aparece deshabilitado con el motivo).
2. **RBAC insuficiente para listar.** Un `approver` (no `tenant_admin+`)
   no ve la pestaña Versiones en absoluto.
3. **Revelar con feature apagada.** `POST .../reveal-version` con la
   feature apagada → `403`, auditado `denied`, sin llamar a CyberArk.
4. **Revelar sin `reason`.** `reason` < 8 caracteres → `422`, sin llamar a
   CyberArk.
5. **Revelar OK.** Con feature habilitada + `tenant_admin+` + `reason`
   válida + un `version` que existe → `client.retrieve_password(id,
   reason, version=N)`, devuelve `{"password": "..."}`, audita
   `account.reveal.history` (`result: ok`, `detail: {reason, version}`,
   **nunca** el valor).
6. **Cuenta o versión inexistente.** 404 mapeado desde `NotFound` del
   cliente, auditado `result: error`.

## Diseño técnico

### `app/cyberark/client.py`

```python
async def list_password_versions(self, account_id: str) -> list:
    """GET /API/Accounts/{id}/Secret/Versions?showTemporary=false —
    metadata de versiones históricas, sin el valor."""
    data = await self.get(
        f"/API/Accounts/{account_id}/Secret/Versions",
        params={"showTemporary": "false"},
    )
    return data.get("Versions", []) if isinstance(data, dict) else []
```

`retrieve_password` gana un parámetro `version: int | None = None` — si
viene, se agrega `"Version": version` al body del POST existente (mismo
endpoint, `retry_safe=False` sin cambios — mismo motivo que ya tiene: un
retry duplicaría el evento "Retrieve" en el audit nativo de CyberArk).

### `app/api/inventory.py`

`GET /accounts/{account_id}/password-versions` — `require_tenant_access
(tenant_admin)`, audita `account.password_versions.list` con el `count`
(sin datos de cada versión). Devuelve las filas crudas de CyberArk tal
cual (el frontend decide qué campos mostrar, con `dash()` de fallback si
un nombre de campo esperado no aparece).

### `app/api/writes.py`

`POST /accounts/{account_id}/reveal-version` — calcado de `account_reveal`
(mismo `_tenant_admin`, mismo chequeo de `reason`), gateado por la feature
`account.reveal.history` en vez de `account.reveal`, body
`{reason, version: int}`. Audita `account.reveal.history`.

### `app/core/features.py` + `pages/Tenants.tsx`

`account.reveal.history` en `KNOWN_FEATURES` **y** en `FEATURES`, mismo
commit (gotcha ya repetido en el proyecto si se separan).

### Frontend

- `components/RevealPasswordDialog.tsx`: prop `extraBody?: Record<string,
  unknown>` — se manda junto a `reason` en el body del POST.
- `components/AccountDetailPanel.tsx`: `Tab` gana `"versiones"`, tab
  visible si `canTenantAdmin`. Query `versions` (`enabled: opened && !!
  accountId && canTenantAdmin`) a `password-versions`. Tabla con columnas
  Fecha/Hora/Modificado por (usando `fmtDate`/`fmtDateTime`/`dash()` sobre
  `VersionID`/`ModificationTime`/`ModifiedBy`, confirmados contra
  Globex) + botón "Revelar" por fila (gateado por `canRevealHistory =
  canTenantAdmin && feats["account.reveal.history"]`) que abre
  `RevealPasswordDialog` con `extraBody={{version: row.<campo>}}`.

## Tareas

- [x] `client.py`: `list_password_versions` + `retrieve_password` con
      `version` opcional.
- [x] `app/api/inventory.py`: `GET .../password-versions`.
- [x] `app/api/writes.py`: `POST .../reveal-version`.
- [x] `app/core/features.py` + `pages/Tenants.tsx`: `account.reveal.history`.
- [x] `RevealPasswordDialog.tsx`: prop `extraBody`.
- [x] `AccountDetailPanel.tsx`: tab "Versiones" + tabla + wiring del reveal.
- [x] Tests unit: listar (RBAC), revelar (feature apagada/reason corta/OK
      sin el valor en audit/404).
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] Probado contra Globex (2026-09-18): nombres de campo confirmados
      (`VersionID`/`ModificationTime`/`ModifiedBy`), sin ajustes de
      código. Spec cerrada.
