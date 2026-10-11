# account-change-platform — cambiar de plataforma (y por qué no "Move" de safe)

- **Estado:** hecha (implementada 2026-09-14, probada por Alex contra Globex; cerrada el 2026-10-07)
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — extra a pedido de Alex
- **Fecha:** 2026-09-14
- **Revisitado (2026-09-22):** la decisión de "no implementar Move" de
  este documento se reconsideró — ver `account.move` en `CLAUDE.md`
  (`app/api/writes.py`). Se implementó igual, pero **no** llamando a este
  `AccountsMgt.asmx/Move` (sigue confirmado fuera de alcance, ver abajo):
  es un workaround por clonación (retrieve + create en el safe destino +
  delete de la original), `platform_admin` únicamente. Ese mismo día se
  volvió a capturar la request real contra Globex (sin saber que ya
  estaba acá) — coincide exacto con lo que sigue documentado abajo,
  incluida la ausencia de `Authorization: Bearer`. Los 9 warnings que
  muestra el diálogo nativo "Move Account" de Privilege Cloud (Accounts
  View Classic UI) se portaron tal cual a `build_account_move_plan`.

## Problema

La vista clásica del PVWA, además de editar `address`/`userName`, permite
sobre una o varias cuentas: **cambiar de plataforma** y **mover a otro
safe** ("Move"). De las dos, sólo la primera tiene una API REST
documentada — ver "Investigación" abajo.

## Alcance

**Entra:**
- `platformId` se agrega a `EDITABLE_TOP_LEVEL` de `account.update` — mismo
  endpoint, misma feature, mismo RBAC `operator+` que `address`/`userName`/
  `name` (individual **y** en lote vía `account.update`/`bulk-update`,
  `docs/specs/account-bulk-edit.md`). Sin feature ni RBAC nuevos: es el
  mismo tipo de operación, ya con su propio warning.
- Selector de plataforma (`PlatformField.tsx`, reusa la lista ya cacheada
  de `/platforms`) en vez de un input de texto libre, tanto en
  `AccountEditSection` como en `AccountBulkEditDialog`, para no depender
  de que el operador tipee un `platformId` exacto.
- Warning explícito (backend + UI) cuando `platformId` cambia: no valida
  ni completa las propiedades que la plataforma nueva requiere.

**Fuera de ESTE documento (`account.update`/`platformId`):**
- **Mover cuentas a otro safe llamando al `Move` nativo de CyberArk.**
  Investigado y descartado — ver abajo, sigue confirmado (revisitado
  2026-09-22). Mover de safe SÍ se implementó después, pero por un
  workaround aparte (`account.move`, clonar + borrar), no llamando a este
  endpoint. Ver la nota de "Revisitado" arriba.

## Investigación: por qué "Move" no entra

CyberArk documenta oficialmente las "Properties that can be updated" del
`PATCH /API/Accounts/{id}` (Update Account v10,
`docs.cyberark.com/.../sdk/updateaccount%20v10.htm` — la página bloquea el
fetch automatizado estándar, pero responde bien con un User-Agent de
navegador real vía `curl`, a diferencia de casi todo el resto de
docs.cyberark.com): `Name`, `address`, `userName`, **`platformId`**,
`platformAccountProperties`, `secretManagement.*`, `remoteMachinesAccess.*`.
**`safeName` no está en esa lista** — el Safe de una cuenta no es una
propiedad editable de ese endpoint.

Alex capturó (DevTools, Network tab) la llamada real que hace el PVWA
clásico al mover una cuenta:

```
POST https://<tenant>.cyberark.cloud/PasswordVault/services/AccountsMgt.asmx/Move
Body: {"srcSafe": "...", "folder": "Root", "destSafe": "...", "name": "..."}
```

Es un servicio `.asmx` (ASP.NET clásico) **sin `Authorization: Bearer`** —
se autentica enteramente con la sesión del navegador del operador
(cookies `ASP.NET_SessionId`, `idToken-*` de la SSO, `XSRF-TOKEN`), no con
el token OAuth2 del service account que usa el resto de PAM Web Console. Para
llamarlo desde el backend habría que automatizar un login SSO completo
(navegador headless) e impersonar una sesión humana — arquitectura
completamente distinta a la del resto de PAM Web Console (un cliente OAuth2 por
tenant, sin sesión de navegador en ningún lado), fuera de lo que
CyberArk documenta o soporta para integraciones, y frágil ante cualquier
cambio de la UI clásica.

Además, la documentación de "Moving accounts between Safes" vive en la
guía de administración (`pasimp/`), no en la referencia de API/SDK
(`webservices/`/`sdk/`) — coherente con que sea una función sólo de UI.

**Decisión original:** no se implementa llamando a este endpoint. Sigue
sin haber una API REST soportada para esto — confirmado de nuevo el
2026-09-22 contra Globex, incluso en el diálogo nativo de Privilege
Cloud (no sólo PVWA on-prem clásico). Mover de safe terminó
implementándose de todos modos, pero por un workaround que no depende de
este endpoint — ver la nota de "Revisitado" arriba y `account.move` en
`CLAUDE.md`.

## Requisitos

1. **Cambiar plataforma, individual.** Dado `platformId: WinServerLocal`,
   cuando se pide `changes: {platformId: "WinDomain"}`, el diff muestra
   `platformId: WinServerLocal -> WinDomain` + warning sobre propiedades
   no validadas.
2. **Cambiar plataforma, en lote.** Mismo criterio que
   `docs/specs/account-bulk-edit.md` — sólo se tocan las cuentas cuyo
   `platformId` actual difiere del pedido.
3. **Selector, no texto libre.** El campo "Plataforma" en ambos diálogos
   lista las plataformas activas del tenant (`GET /platforms`), no acepta
   un `platformId` inventado por typo.

## Tareas

- [x] `app/domain/accounts.py`: `platformId` en `EDITABLE_TOP_LEVEL` +
      warning al cambiar.
- [x] `src/components/PlatformField.tsx` (selector compartido).
- [x] `AccountEditSection.tsx` + `AccountBulkEditDialog.tsx`: campo
      "Plataforma" vía `PlatformField`.
- [x] Tests unit: diff + warning al cambiar `platformId`, ejecución.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] Probar contra Globex antes de cerrar (incluye confirmar que el
      CPM sigue gestionando la cuenta después del cambio de plataforma).
      Probado por Alex; cerrada el 2026-10-07.
