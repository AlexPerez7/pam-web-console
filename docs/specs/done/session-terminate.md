# session-terminate — terminar una sesión PSM activa

- **Estado:** **probada de punta a punta contra Globex (2026-09-24)** —
  ver "Bug encontrado contra Globex" más abajo para la investigación
  completa (3 bugs de código + 1 permiso, los 4 resueltos). De paso,
  esta prueba destapó un bug de UX en `DryRunDialog` (componente
  compartido por ~22 lugares de PAM Web Console, no sólo Sesiones) — ver
  "Bug de UX" al final.
- **Roadmap:** `docs/PLAN.md §7` Fase 3, ítem "Terminar sesiones activas"
- **Fecha:** 2026-09-14

## Problema

El tab "Sesiones" de Inventario (Fase 1) sólo lee — lista las sesiones
PSM activas de un tenant, sin ninguna acción. Terminar una sesión hoy
requiere ir al PVWA clásico.

## Alcance

**Entra:**
- `POST /tenants/{id}/sessions/{session_id}/terminate` — botón "Terminar"
  por fila en la grilla de Sesiones.
- Feature `session.terminate`, RBAC `operator+`, **sin confirmación
  reforzada**.

**Fuera (para una spec futura, sin pedido concreto todavía):**
- **Suspender/Reanudar** (`Suspend`/`Resume` — psPAS los expone,
  `Suspend-PASPSMSession`/`Resume-PASPSMSession`) — el roadmap sólo pide
  "terminar", y son casos de uso distintos (monitoreo en vivo vs. cortar
  el acceso).
- Terminar en lote (varias sesiones a la vez).

**Por qué sin confirmación reforzada** (a diferencia de
`safe.member.remove`/`user.delete`, que sí la llevan): terminar una
sesión no borra nada persistente ni saca acceso de forma permanente — el
usuario puede reconectarse. Es una acción operativa/de seguridad de bajo
costo de error, igual criterio que `account.verify`/`user.activate`
(`operator+`, sin confirmar). Si en la práctica se usa como respuesta a
incidentes y hace falta subir el techo, se ajusta después con feedback
real.

## Investigación: endpoints

`docs.cyberark.com` bloqueó el fetch automatizado también para estas
páginas. Verificado contra `psPAS`
(`psPAS/Functions/Monitoring/Stop-PASPSMSession.ps1` +
`Get-PASPSMSession.ps1`):

```
GET  /API/LiveSessions/{liveSessionId}    Get-PASPSMSession (by id)
POST /api/LiveSessions/{liveSessionId}/Terminate   Stop-PASPSMSession
```

- `{liveSessionId}` es el `SessionID` (así, con "ID" en mayúscula — mismo
  campo que `Recording.SessionID`, confirmado contra Globex el
  2026-09-24, ver "Bug encontrado" más abajo) que ya trae
  `client.live_sessions()` ("Get Live Sessions", usado hoy por el tab
  Sesiones) — no hace falta resolver ningún otro identificador.
- El `fetch` de la escritura usa el **Get Live Session por id** (en vez de
  volver a listar todas y buscar) — más barato y evita el caso borde de
  que la sesión haya terminado sola entre que se lista la grilla y se
  aprieta "Terminar" (ahí devuelve 404, que `run_write` ya mapea).

## Requisitos

1. **Terminar una sesión activa.** Dado un `SessionId` real, al confirmar
   se llama a `Terminate` y la sesión desaparece de la grilla (recarga).
2. **Sesión ya terminada.** Si el `SessionId` ya no existe (terminó sola,
   o ya la terminó otro operador), 404 antes de intentar nada más.
3. **Dry-run.** Antes de confirmar, muestra el efecto ("Se termina la
   sesión de **{user}** en **{remote_machine}**") sin llamar a CyberArk.

## Diseño técnico

- `backend/app/cyberark/client.py`: `get_live_session(session_id) ->
  dict` (`GET /API/LiveSessions/{id}`), `terminate_session(session_id) ->
  None` (`POST /api/LiveSessions/{id}/Terminate`).
- `backend/app/domain/sessions.py` (nuevo): `plan_session_terminate(current,
  _changes) -> Plan` — `current` = el objeto de la sesión; `effect`
  legible con usuario/target; sin diff, sin `confirm_phrase`.
- `backend/app/api/writes.py`: `POST /tenants/{id}/sessions/{session_id}/
  terminate`, `WriteActionIn`, RBAC `_operator`.
- Frontend: `frontend/src/components/SessionTerminateButton.tsx` (nuevo,
  chico) — botón "Terminar" que abre directo un `<DryRunDialog>` (sin el
  paso de confirmación inline previo que usan las secciones de Fase 3,
  porque acá no hay `confirm_phrase`); columna nueva "Acciones" en
  `sessionCols` de `pages/Sessions.tsx`, gateada por el rol (`operator+`).

## Bug encontrado contra Globex (2026-09-24)

Primera prueba real: la columna "Acciones" aparecía vacía en TODAS las
filas, sin ningún botón "Terminar". Causa: `LiveSession.SessionId`
(inferido del nombre de parámetro que usa `psPAS`, nunca confirmado
contra una respuesta real) — el campo real que devuelve
`GET /API/LiveSessions` es `SessionID` (con "ID" en mayúscula), igual
que `Recording.SessionID` (ya corregido en `session-recordings.md`).
`sessionCols` chequeaba `s.SessionId ? <Button/> : null`, así que
siempre era `undefined` y el botón nunca se renderizaba — sin error
visible, la grilla se veía normal.

**Bug hermano, misma causa raíz:** la columna "Cuenta" usaba
`s.AccountId`, campo que tampoco existe en la respuesta real — los
campos reales son `AccountUsername`/`AccountPlatformID`/
`AccountAddress`. Se cambió a `AccountUsername`. Fix: `LiveSession` en
`lib/types.ts` reescrito con los nombres reales confirmados (también
`ConnectionComponentID`, antes `ConnectionComponentId`, sin uso visible
todavía pero corregido igual) + `CanTerminate` (campo real que trae
CyberArk, sin usar en la UI por ahora).

**Segundo bug, con el primero ya arreglado:** con el botón visible, el
`Terminate` real devolvió `400 CAWS00001E "Live Session value is
invalid"`. Causa: `POST /api/LiveSessions/{id}/Terminate` necesita el
**`SessionGuid`** (formato UUID, ej.
`71410687-34b1-4a68-abbb-c6ab570d7d67`), no el `SessionID` (formato
`"46_1142147"`) — confirmado leyendo el código fuente real de
`Stop-PASPSMSession.ps1` en `psPAS`: su parámetro `LiveSessionId` tiene
**alias `SessionGuid`**. **No era el campo `CanTerminate: false`** el
que bloqueaba — confirmado que no es eso lo que causaba el rechazo.

**Tercer intento, mismo día:** el primer fix (resolver `SessionGuid` del
objeto que ya trajo `fetch`, vía `GET /API/LiveSessions/{id}`) no
alcanzó — Alex probó de nuevo y CyberArk volvió a rechazar con el mismo
error, mandando otra vez el `SessionID` corto. Eso reveló que el **GET
puntual de una sesión no trae `SessionGuid` en su respuesta** (a
diferencia del listado, `GET /API/LiveSessions`, que sí lo trae
confirmado con datos reales). Fix definitivo: `apply` resuelve el guid
contra **`client.live_sessions()`** (el listado) buscando la fila con el
mismo `SessionID`, en vez de confiar en el objeto de `fetch` — con
fallback final al `session_id` de la URL si tampoco apareciera ahí.

**De paso** (pedido de Alex en la misma prueba): botón "Actualizar" en
`pages/Sessions.tsx` (mismo ícono/patrón que `SystemHealth.tsx`,
`q.refetch()` simple — Sesiones no tiene caché Redis como Dashboard, no
hace falta un `?refresh=1`).

## Tareas

- [x] `client.py`: `get_live_session`, `terminate_session` (ahora recibe
      `session_guid`, no `session_id`).
- [x] `domain/sessions.py` (nuevo): `plan_session_terminate`.
- [x] `writes.py`: endpoint — resuelve `SessionGuid` contra
      `live_sessions()` antes de terminar (no contra el `fetch` puntual,
      que no lo trae).
- [x] `SessionTerminateButton.tsx` + columna "Acciones" en `sessionCols`.
- [x] Botón "Actualizar" en `pages/Sessions.tsx`.
- [x] Tests unit: ejecución ok (guid resuelto vía listado), fallback sin
      `SessionGuid` en ningún lado, sesión inexistente 404, RBAC.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] Probado contra Globex (2026-09-24) — encontró y corrigió los dos
      bugs de código (casing `SessionID`/`AccountUsername`, y resolver
      `SessionGuid` contra el listado en vez del GET puntual). Con
      ambos fixes, PAM Web Console manda el request correcto (guid + endpoint
      correctos) — **pero CyberArk lo rechaza con 403** (ver abajo).
- [x] **Encontró un permiso de CyberArk que faltaba, no era código.** El 403
      trae `CAWS00001E` con `ErrorMessage` vacío — coincide con que
      TODAS las sesiones de la respuesta traían `CanTerminate: false`.
      En **Self-Hosted** esto se gestiona con el grupo de Vault
      `PSMLiveSessionTerminators` (o estar listado en PVWA →
      Configurations → Privileged Session Management → Live Sessions
      Monitoring Settings → "Terminating Live Sessions Users and
      Groups") — confirmado por búsqueda web, aunque no se pudo leer el
      artículo completo (bloqueo de fetch automatizado de
      `docs.cyberark.com`, como siempre). Como Globex es **Privilege
      Cloud** (el único deployment que soporta PAM Web Console v1), la gestión real
      es por **rol de Identity**, no por grupo de Vault directo. **Rol
      confirmado (2026-09-24):** `Privilege Cloud Session Risk Managers`
      — Alex lo asignó al service account de Globex y el `Terminate`
      real funcionó de punta a punta.

## Bug de UX encontrado en la misma prueba: `DryRunDialog` no mostraba el resultado

Con el `Terminate` ya funcionando, Alex notó que al confirmar el
diálogo se demoraba unos segundos y se cerraba solo, sin mostrar ningún
mensaje de "sesión terminada" — aunque `DryRunDialog` (el componente
genérico que usan **todas** las operaciones de escritura de PAM Web Console, no
sólo `session.terminate`) ya tenía preparado un `<Alert variant=
"success">` con "Cambio aplicado." para ese momento.

Causa: `confirm.onSuccess: () => onDone()` disparaba `onDone` de
inmediato al terminar la escritura, y casi todos los ~22 lugares que
usan `<DryRunDialog>` implementan su `onDone` para cerrar su propio
wrapper (`setOpen(false)`, cerrar un `DetailDrawer`, etc.) — el mensaje
de éxito nunca llegaba a pintarse porque todo se desmontaba en el mismo
tick. No era un bug exclusivo de `SessionTerminateButton`: es el
componente compartido, así que afectaba (en silencio, sin que nadie lo
hubiera reportado antes) a cualquier escritura de PAM Web Console.

Fix (`frontend/src/components/DryRunDialog.tsx`, un solo archivo, sin
tocar ningún caller): `onDone` ya no se dispara en `onSuccess` — se
difiere hasta que el usuario cierra el diálogo a mano (botón "Cerrar",
o backdrop/ESC vía `onOpenChange`, mismo criterio para los dos
caminos). Antes de confirmar, "Cancelar" sigue comportándose igual
(sólo cierra, nunca llama a `onDone`). El diálogo ahora se queda
abierto mostrando el resultado hasta que el operador lo cierra.

**Segundo bug de UX, mismo diálogo, encontrado al toque:** con el
mensaje de éxito ya visible, Alex notó que **duplicar una plataforma**
(nada que ver con el CPM) mostraba "Pedido enviado al CPM. El
resultado aparece en 'Actividad en CyberArk'…" — el mensaje pensado
para `account.change`/`verify`/`reconcile`. Causa: el frontend
deducía "es del CPM" con un heurístico (`Plan.effect` presente + sin
`diff`) que también matchea `session.terminate`,
`platform.activate`/`duplicate`, `discovered_account.onboard`/
`ignore`/`restore`/`delete`, `identity_user.lock`, `user.activate` —
cualquier operación de tipo "acción" comparte esa forma, no sólo las
del CPM. Fix: `Plan` gana `is_cpm_action` (default `False`), seteado
en `True` únicamente en `plan_cpm` (`app/domain/accounts.py`, la
factory de `change`/`verify`/`reconcile`); se expone en la respuesta
del dry-run y el frontend lo usa en vez del heurístico.
