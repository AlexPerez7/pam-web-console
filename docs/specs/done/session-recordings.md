# session-recordings — acceso a grabaciones de sesión PSM

- **Estado:** hecha — probada contra Globex (2026-09-17). La
  reproducción embebida que quedaba "fuera de alcance" abajo se hizo
  después: ver `docs/specs/done/recording-player.md`.
- **Roadmap:** `docs/PLAN.md §7` Fase 4, ítem "Acceso a grabaciones de
  sesión" (tabla de paridad: "Acceso a grabaciones" / permiso nativo "View
  recordings")
- **Fecha:** 2026-09-16

## Problema

El tab Sesiones (Fase 1) sólo muestra sesiones PSM **activas** en vivo, y
`session.terminate` (Fase 3) sólo corta una sesión activa. Ninguna de las
dos cubre las sesiones **ya terminadas**: hoy, para revisar qué hizo un
usuario en una sesión pasada (auditoría, investigación de un incidente,
pedido de un cliente), el operador tiene que ir al PVWA clásico. CyberArk
expone esto vía un dominio de API separado (`/API/Recordings`, no
`/API/LiveSessions`), documentado oficialmente (no sólo inferido de
`psPAS` como el resto de Fase 3/4) en `docs.cyberark.com` — ver
"Investigación: endpoints".

## Alcance

**Entra:**
- Listar grabaciones de un tenant, filtrando por rango de fechas (por
  defecto últimas 48 h, igual que el default de la API), Safe y texto
  libre — página nueva `/recordings`.
- Ver el detalle de una grabación puntual: metadata (usuario, target,
  protocolo, Safe, inicio, duración) + línea de tiempo de actividades
  (`GET .../activities` — comandos/eventos capturados durante la sesión).
- **Descargar** el archivo de la grabación.

**Fuera (para una spec futura, sin pedido concreto todavía):**
- **Reproducir embebido en el navegador.** El archivo que devuelve
  `.../Play` es el paquete nativo del PSM Universal Recorder (típicamente
  `.avi` con un códec propio para sesiones gráficas, o un formato de texto
  para sesiones de consola) — no es necesariamente reproducible por un
  `<video>` HTML5 estándar. v1 se limita a **descargar** (mismo patrón ya
  usado por "Exportar plataforma"), el operador lo abre con el visor que
  ya usa hoy (PSM Universal Player / VLC). Evaluar un player embebido
  después, si hace falta.
- "Get recording details" (`GET .../recordings/{id}/`) y "Get recording
  properties" (`.../properties/`) — devuelven el mismo set de campos que
  ya trae cada fila de "Get recordings" (confirmado contra la doc
  oficial: son el mismo objeto, sólo que "details" lo envuelve en
  `{"Recordings": [...]}` y "properties" lo devuelve plano). El detalle
  de una grabación reusa directo la fila que ya trajo la lista (mismo
  criterio que `safe.update`, que reusa el `current` del `fetch` en vez
  de pedirlo de nuevo) — sin llamada extra a CyberArk.
- "Test recording valid" (`.../valid`, requiere v11.2+, no documentado en
  el portal oficial revisado) — sin caso de uso concreto todavía.
- Descargar varias grabaciones a la vez (zip en lote).

## Investigación: endpoints

A diferencia del resto de Fase 3/4 (donde `docs.cyberark.com` bloqueaba el
fetch automatizado y había que inferir contra `psPAS`), Alex encontró el
portal correcto donde SÍ responde: la documentación de **Privilege Cloud
con Secrets Rotation** (`docs.cyberark.com/snapshot/
privilege-cloud-secrets-rotation/en/content/webservices/`, distinta URL
base del portal genérico que venía bloqueando el fetch). Confirmado con
`curl` + User-Agent de navegador real, páginas oficiales "Get recordings",
"Get recording details", "Get recording activities", "Get recording
properties", "Play recording" — y cruzado contra `psPAS`
(`Get-PASPSMRecording.ps1`/`Get-PASPSMRecordingActivity.ps1`/
`Export-PASPSMRecording.ps1`), que coincide:

```
GET  /API/recordings/                       Get recordings (listar)
     ?Limit&Sort&Offset&Search&Safe&FromTime&ToTime&Activities
GET  /API/recordings/{recordingId}/         Get recording details
GET  /API/recordings/{recordingId}/activities/   Get recording activities
GET  /API/recordings/{recordingId}/properties/   Get recording properties
POST /API/recordings/{recordingId}/Play/    Play recording — "the result
                                             is the video recording data
                                             stream" (no JSON).
```

- **El identificador real es `SessionID`**, no `RecordingID` — ese es sólo
  el nombre que le puso `psPAS` al parámetro. El objeto de una grabación
  trae `SessionID`/`SessionGuid`, y ES el valor que va en `{recordingId}`
  de la URL (confirmado en el `Result` de "Get recording details": el
  cuerpo tiene `"SessionID": "<id>"` como primer campo).
- `FromTime`/`ToTime` van en Unix time; sin argumentos, "Get recordings"
  trae **desde siempre** hasta ahora (la doc oficial no documenta un
  default de 2 días para esta operación puntual — a diferencia de
  `Get-PASPSMRecording` de `psPAS`, que sí asume las últimas 48 h del lado
  cliente; para no repetir esa suposición sin confirmarla, PAM Web Console replica
  el mismo default explícito de 48 h, del lado del backend).
  `Limit` por defecto 25 (igual que "Get active sessions").
- El objeto de cada grabación es más rico que el de `LiveSessions`: además
  de `User`/`RemoteMachine`/`Protocol`/`SafeName`/`Start`/`Duration`, trae
  `RiskScore`/`Severity`/`IncidentDetails` (análisis de riesgo de la
  sesión, si PTA/comportamiento anómalo lo marcó) y `RecordingFiles[]`
  (nombre/tamaño/formato de cada archivo de la grabación) — vale sumar
  `RiskScore`/`Severity` como columnas en la grilla, más útil que sólo
  fecha/duración para priorizar qué revisar.
- `.../Play` es la misma familia de endpoint que `client.export_platform`
  (POST que devuelve bytes crudos, no JSON) — mismo patrón de cliente
  (`raw=True`).
- Nota de casing: la doc oficial escribe los segmentos en minúscula
  (`/recordings/`) mientras que el resto del cliente de PAM Web Console sigue el
  casing de `psPAS` (`/API/LiveSessions`, `/API/Platforms`, PascalCase) —
  IIS/CyberArk no distingue mayúsculas de minúsculas en la ruta, así que
  se mantiene `/API/Recordings/...` por consistencia con el resto de
  `client.py`, no porque la minúscula sea incorrecta.

**Gotcha real encontrado probando contra Globex (2026-09-16/17):** la
primera versión de `play_recording` devolvía el componente de
texto/keystrokes de la grabación (`.Keystrokes.txt`, unos KB) en vez del
video, **aun en grabaciones con `VideoSize` de varios MB** (confirmado
comparando el JSON de `list_recordings` contra lo que bajaba PAM Web Console). Dos
intentos:

1. **1er intento — `Accept: */*`** (en vez del `Accept: application/json`
   que `CyberArkClient` manda por default en TODAS las requests):
   insuficiente solo, no arregló el problema.
2. **2do intento — body `data={"isVideo": "true"}`
   (`application/x-www-form-urlencoded`, NO JSON):** la doc oficial dice
   "Body parameters: None" para "Play recording", pero es incompleta —
   comparando los Request Headers reales de una llamada exitosa del PVWA
   (inspeccionados con las devtools del navegador contra Globex) contra
   los de PAM Web Console, la del PVWA manda un `content-length: 12` con
   `content-type: application/x-www-form-urlencoded`, que calza exacto
   con `isVideo=true` (12 caracteres). Sin ese parámetro, CyberArk
   devuelve el componente de texto en vez del video. `_request`/`post`
   ganaron parámetros `headers`/`data` opcionales para esto.

**Sin confirmar todavía si existe un `isVideo=false` explícito** para
pedir el componente de texto a propósito (no hace falta para el alcance
actual).

**Confirmado (2026-09-17): el fix de `isVideo=true` SÍ funcionó.** El
`Content-Disposition` seguía nombrando el archivo con el `FileName` del
componente de texto (`RecordingFiles[0]`, ver bug de nombre abajo), lo
que hacía pensar que seguía trayendo texto — probando con VLC
(renombrando a `.avi`) el error fue *"VLC no puede decodificar el
formato «SCPR» (No description for this codec)"*: **SCPR es un códec de
video real** (ScreenPressor, usado por PSM para grabaciones de sesión,
contenedor AVI) — confirma que el contenido SÍ es el video, sólo que VLC
no tiene ese códec instalado.

**Reproducción del video (no requiere cambios en PAM Web Console):** según el
artículo oficial de CyberArk (KB000003904, "How to watch PSM .avi files
on desktop and where is the PSM Codec?") el códec SCPR viene incluido en
los archivos de instalación de PSM, y también se puede descargar de
`infognition.com/ScreenPressor` — una vez instalado en Windows, los
`.avi` se reproducen con **Windows Media Player Classic, MPC-HC o MPV**
(VLC estándar no queda cubierto por ese codec pack, consistente con el
error visto). Construir un reproductor propio adentro de PAM Web Console para este
códec propietario no es viable (sin spec pública, ni siquiera VLC lo
soporta) — instalar el codec pack una vez resuelve la reproducción sin
tocar el proyecto.

**Bug de nombre de archivo (2026-09-17, corregido):**
`RecordingDetailPanel.tsx` mandaba `RecordingFiles[0]?.FileName` como
nombre de descarga — pero ese es el nombre del componente de **texto**
de la grabación (no el que efectivamente se descarga con
`isVideo=true`), así que el archivo quedaba con extensión `.txt` aunque
el contenido real fuera video — el Bloc de notas "explotaba" al intentar
abrir varios MB de binario como texto (comportamiento esperado, no es
un bug de Notepad). Fix: nombre fijo `${sessionId}.avi` en el frontend
(ya no se usa `RecordingFiles` para esto) + mismo default `.avi` en el
backend si no llega `filename`. Sección "Descargar" del frontend suma
una nota con el códec SCPR/ScreenPressor + reproductores compatibles +
link de descarga.

## Requisitos

1. **Listar grabaciones de un rango de fechas.** Dado un tenant con
   grabaciones en las últimas 48 h, `GET /tenants/{id}/recordings`
   (sin parámetros) devuelve esas grabaciones ordenadas por inicio.
2. **Filtrar por Safe / texto.** `?safe=` y `?search=` acotan la lista
   (delegado a la API de CyberArk, no post-filtrado en PAM Web Console).
3. **Ver actividades de una grabación.** Al abrir el detalle de una
   grabación se listan sus eventos (`GET .../recordings/{id}/activities`)
   en orden cronológico.
4. **Descargar sin la feature habilitada.** Si `recording.play` no está
   habilitada para el tenant, el botón "Descargar" no aparece y el POST
   devuelve 403 (mismo criterio que `account.reveal`).
5. **Descargar sin razón.** Si `reason` tiene menos de 8 caracteres, 422
   sin llamar a CyberArk.
6. **Descargar una grabación válida.** Con la feature habilitada, rol
   `tenant_admin+` y una razón válida, el POST devuelve el archivo
   (`content-disposition: attachment`) y queda auditado `recording.play`
   con la razón, **sin el contenido del archivo**.
7. **Grabación inexistente.** Un `SessionID` que ya no existe (purgado por
   retención) devuelve 404 antes de intentar nada más.

## Diseño técnico

- **RBAC en dos niveles**, mismo criterio que "listar sesiones" (bajo
  riesgo) vs. "revelar contraseña" (alto riesgo):
  - Listar grabaciones + ver actividades: **`viewer+`, sin feature-flag**
    — es metadata (quién, cuándo, dónde), igual que el tab Sesiones hoy.
  - Descargar el archivo: **`tenant_admin+`** (el techo más alto de PAM Web Console,
    igual que `account.reveal`/`account.reveal.bulk`) **+ feature
    `recording.play`** (opt-in por tenant, apagada por defecto) **+
    `reason` obligatoria**, porque el video puede mostrar contraseñas
    tipeadas en pantalla, datos de clientes u otro contenido sensible —
    mismo nivel de exposición que revelar una contraseña, no el de "ver
    metadata de una sesión".
  - **No pasa por `writeop.run_write`**: no hay dry-run posible (descargar
    ES la acción), mismo criterio que `account.reveal`.

- `backend/app/cyberark/client.py` (nuevos métodos, junto a
  `live_sessions`/`get_live_session`; sin `get_recording`/
  `get_recording_properties` — el detalle reusa la fila de la lista):
  - `list_recordings(from_time, to_time, safe=None, search=None, limit=25)
    -> list` — `GET /API/Recordings`, `from_time`/`to_time` siempre
    explícitos (ver nota del default de 48 h arriba, PAM Web Console no confía en
    que CyberArk lo asuma solo).
  - `get_recording_activities(session_id) -> list` — `GET
    /API/Recordings/{id}/activities`, mismo shape de respuesta
    (`{"Activities": [...]}`) que `get_live_session_activities`.
  - `play_recording(session_id) -> bytes` — `POST
    /API/Recordings/{id}/Play`, `raw=True`, `retry_safe=False` (igual que
    `retrieve_password`: un reintento duplicaría el evento "Play" en el
    audit nativo de CyberArk).

- `backend/app/api/recordings.py` (router nuevo, no `writes.py` ni
  `inventory.py` — dominio propio, paralelo a Sesiones):
  - `GET /tenants/{id}/recordings?from_time&to_time&safe&search&limit` —
    `require_tenant_access(viewer)`, audita `recording.list`.
  - `GET /tenants/{id}/recordings/{session_id}/activities` — mismo rol,
    audita `recording.activities`, target = `session_id`.
  - `POST /tenants/{id}/recordings/{session_id}/play {reason}` —
    `require_tenant_access(tenant_admin)`, replica a mano el gating de
    `account_reveal` (feature check → 403 auditado si está apagada →
    `reason` ≥ 8 → `client.play_recording` → 404/403 mapeados → audita
    `recording.play` con la razón, sin el archivo) → `Response(content=...,
    media_type="application/octet-stream", content-disposition attachment)`
    igual que `platform_export`.

- `backend/app/core/features.py` + `frontend/src/pages/Tenants.tsx`:
  `recording.play` en **las dos** listas (`KNOWN_FEATURES` y `FEATURES`),
  en el mismo commit — evitar el gotcha ya repetido varias veces en el
  proyecto.

- Frontend:
  - `frontend/src/pages/Recordings.tsx` (ruta `/recordings`, espacio
    Operación, después de Sesiones) — `TenantScope` + filtro de fechas
    (default últimas 48 h) + Safe + búsqueda, `DataTable` con columnas
    calcadas de `sessionCols` de `Sessions.tsx` (Usuario/Target/Safe/
    Protocolo/Inicio/Duración) más **Riesgo** (`RiskScore`/`Severity`,
    nuevo respecto a Sesiones — CyberArk lo calcula sólo para grabaciones,
    no para sesiones en vivo).
  - `frontend/src/components/RecordingDetailPanel.tsx` — actividades de
    la grabación (fetch al abrir, un solo panel a la vez, mismo patrón
    que `TroubleshootingAccountPanel`) + botón "Descargar grabación",
    gateado por `recording.play` + `tenant_admin+`, mismo flujo de razón
    que `RevealPasswordDialog` pero terminando en una descarga (`apiBlob`
    con `method: "POST", body: {reason}` + `saveBlob`, ya soportado por
    `lib/api.ts`/`lib/download.ts` — sin cambios ahí).

## Tareas

- [x] `client.py`: `list_recordings`, `get_recording_activities`,
      `play_recording`.
- [x] `app/api/recordings.py` (router nuevo): listar, actividades,
      descargar.
- [x] `features.py` + `Tenants.tsx`: `recording.play` en ambas listas.
- [x] `pages/Recordings.tsx` + entrada de nav en el espacio Operación.
- [x] `components/RecordingDetailPanel.tsx` (actividades + descarga con
      razón, vía `components/RecordingDownloadSection.tsx`).
- [x] Tests unit: listar, actividades, descarga ok, feature apagada
      (403), razón corta (422), grabación inexistente (404), RBAC
      (`tests/unit/test_recordings.py`, 9 tests).
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] Probado contra Globex (2026-09-16/17): encontró y corrigió el bug
      del `Accept` header + `isVideo=true`, el bug de nombre de archivo, y
      confirmó que el contenido es video real (códec SCPR/ScreenPressor),
      todo documentado arriba. La reproducción embebida (que quedaba
      "fuera de alcance" en este corte) se implementó después — ver
      `docs/specs/done/recording-player.md`.
