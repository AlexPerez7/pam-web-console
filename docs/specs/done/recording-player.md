# recording-player — reproducción embebida de grabaciones (SCPR/ScreenPressor)

- **Estado:** hecha — probada contra una grabación real de Globex
  (2026-09-16)
- **Roadmap:** `docs/PLAN.md §7` Fase 4, extra sobre "Acceso a grabaciones
  de sesión" (`docs/specs/session-recordings.md`, ya implementada —
  v1 sólo descarga)
- **Fecha:** 2026-09-17

## Problema

`session-recordings.md` (v1) sólo permite **descargar** el archivo de
una grabación PSM. Probando contra Globex se confirmó que el video es
un AVI con el códec propietario **SCPR (ScreenPressor)** — ningún
reproductor estándar lo decodifica (ni VLC), y CyberArk requiere
instalar un codec pack de terceros (`infognition.com/ScreenPressor`,
según KB000003904) para verlo con Windows Media Player Classic/MPC-HC/
MPV. Eso obliga al operador a instalar software adicional en su máquina
y a salir de PAM Web Console para revisar una grabación — justo lo que PAM Web Console busca
evitar ("el PVWA, pero mejor").

## Enfoque descartado: reusar los binarios de Infognition

Se investigó primero reusar `jsplayer.js` (reproductor en JavaScript
puro que Infognition ya construyó, decodifica AVI+SCPR directo en el
navegador). **Se descartó**: ni `jsdec.zip` ni `jsplayer.js` traen un
archivo de licencia — sólo un aviso de copyright sin términos de uso
explícitos, y la propia página pide "contact us" para la versión
extendida. No es prudente vendorizar ese código (binarios/minificado,
sin fuente) en un producto comercial sin confirmación escrita, y esa
confirmación no estaba disponible en el momento de decidir. Decisión con
Alex: **no depender de eso** — construir la reproducción sobre el
código del códec que sí tiene licencia clara.

## Enfoque elegido: compilar el códec MIT + transcodificar server-side

El algoritmo del códec (no el player, el códec en sí) está en
`github.com/thedeemon/screenpressor`, **MIT License** sin ambigüedad.
Es la versión Video-for-Windows original (C++, atada a `windows.h`/
`vfw.h`), pero revisando el código completo se confirmó que es viable
compilarlo en Linux **sin reimplementar el algoritmo**:

- La capa atada a Windows es sólo el driver VfW (`screenpressor.cpp`,
  `screenpressor.h`, `drvproc.cpp`, `conf.h`/`conf.cpp` — este último
  es config vía Registro de Windows, ni siquiera lo necesitamos). Esa
  capa no se porta — **se bypasea completamente**: en vez de pasar por
  la interfaz de códec de Windows Media Player, un driver chico propio
  llama directo a `ScreenCodec::Init()` / `DecompressFrame()`.
- Los tipos de Windows (`DWORD`, `BYTE`, `WORD`, `BOOL`, `LONG`,
  `BITMAPINFOHEADER`) son triviales de reemplazar por typedefs
  portables — el propio autor los dejó **comentados** en `defines.h`,
  como si hubiera anticipado portarlo.
- El único uso real de la API de Windows fuera del driver VfW es
  threading nativo en `squad.cpp`/`squad.h` (`CreateThread`,
  `CreateEvent`, `WaitForMultipleObjects`) — pero revisando el código,
  cuando se instancia `CSquad` con 1 solo hilo (`nThreads=1`),
  `RunParallel()` ejecuta el trabajo **directo en el hilo actual sin
  tocar ninguna API de Windows** (`if (nw>1) {...} else
  job->RunCommand(...)`). v1 corre siempre con 1 hilo — no hace falta
  portar el threading, sólo simplificar `squad.cpp` para que compile sin
  `<windows.h>` (quitar/comentar el branch `nw>1`, nunca alcanzado).

Esto reduce el trabajo de "reimplementar un códec de video" (altísimo
riesgo — compensación de movimiento, bloques de 16×16, 3 codificadores
entrópicos según versión, cualquier desvío corrompe la decodificación
completa desde ese punto) a "conseguir que el código ya probado compile
con un arnés distinto" — mucho más acotado y de menor riesgo.

## Alcance

**Entra:**
- Herramienta nueva `backend/tools/scpr_decode/` (C++, compilada en la
  imagen Docker del `api`): decodifica frames SCPR (recibidos por stdin,
  ya extraídos del AVI por el lado Python) a píxeles RGB crudos
  (stdout), usando el código MIT de `screencap.cpp`/`ans_contexts.cpp`/
  `sub.cpp`/`squad.cpp` (adaptados: sin Windows, un solo hilo) + un
  `main.cpp` propio que llama a `ScreenCodec` directo.
- Demuxer de AVI en Python (`app/domain/avi.py`, puro, sin dependencias
  nuevas): extrae ancho/alto/bpp/fps + la lista de frames comprimidos
  del contenedor RIFF/AVI que devuelve `client.play_recording`.
- Pipeline de transcodeo server-side: AVI (CyberArk) → demux (Python) →
  decode (herramienta C++) → encode a MP4/H.264 (`ffmpeg`, nuevo en la
  imagen Docker) → bytes servidos al frontend.
- Endpoint nuevo `POST /tenants/{id}/recordings/{session_id}/play/mp4`
  — mismo gating que `.../play` (feature `recording.play` +
  `tenant_admin+` + `reason`), devuelve el MP4 en vez del AVI original.
  `.../play` (sin `/mp4`) sigue igual, sin cambios — sigue siendo lo que
  usa "Descargar".
- `<video controls src={blobUrl}>` nativo del navegador en el frontend
  — sin reproductor propio, sin canvas a mano. El `Blob` del MP4 se
  obtiene con el mismo `apiBlob` que ya usa "Descargar", apuntando al
  endpoint nuevo.
- Manejo explícito de error (decodificación falla, códec/versión no
  soportada, transcodeo falla) — mensaje claro, "Descargar" (el AVI
  original) sigue disponible siempre como alternativa.

**Fuera (para una spec futura, sin pedido concreto todavía):**
- Reproducir el componente de **texto/keystrokes** sincronizado con el
  video.
- Soportar todas las versiones del códec (2/3/4) desde el día uno — v1
  apunta a la versión que efectivamente generan los tenants del MSP
  (confirmar versión real contra una grabación de Globex antes de
  codear el decoder; el header AVI trae esa info). Si aparece una
  versión no soportada, error claro (Requisito 4), no un crash.
- Grabaciones con canal de audio (PSM normalmente no graba audio en
  sesiones RDP/Web; si aparece, se ignora/descarta para v1).
- Transcodeo en background/async — v1 es síncrono dentro del request
  (mismo criterio que `credentials_backup.export`, que ya "puede tardar
  varios minutos" con el timeout de nginx extendido); si en la práctica
  el transcodeo de una grabación típica (pocos MB) resulta lento,
  reconsiderar.
- Cachear el MP4 ya transcodeado entre reproducciones (Requisito 6: sin
  caché, se re-transcodifica cada vez, mismo criterio que revelar una
  contraseña).

## Requisitos

1. **Reproducir una grabación con permisos.** Dado un tenant con
   `recording.play` habilitada, un operador `tenant_admin+` y una razón
   válida (≥8 caracteres), al presionar "Reproducir" se transcodifica el
   video y se muestra en un `<video>` nativo, reproduciéndose.
2. **Sin permisos.** Si `recording.play` está apagada o el rol es menor
   a `tenant_admin`, ni "Reproducir" ni "Descargar" aparecen (mismo
   criterio ya vigente en `session-recordings.md`).
3. **Versión de códec no soportada.** Si el AVI declara una versión de
   SCPR que el decoder no implementa (v1 sólo soporta la versión real
   verificada contra Globex), error claro — "Descargar" sigue
   disponible.
4. **Error de decodificación/transcodeo.** Frame corrupto, códec
   inesperado, `ffmpeg` falla — mensaje de error dentro del panel, sin
   romper la página, "Descargar" siempre disponible como alternativa.
5. **Auditoría.** Reproducir queda auditado con su propia acción
   (`recording.play.mp4` o similar — endpoint distinto de
   `recording.play`, mismo criterio que separar `account.reveal` de
   `account.reveal.bulk`: mismo nivel de exposición, acción diferenciada
   para que el audit trail distinga "vio" de "bajó").
6. **Sin caché entre aperturas.** Cerrar el panel y volver a abrir la
   misma grabación pide la razón de nuevo y retranscodifica — no se
   persiste el MP4 ya generado (mismo criterio que `RevealPasswordDialog`).

## Diseño técnico

### `backend/tools/scpr_decode/` (C++, nuevo)

- Vendoriza (con atribución MIT + link al repo original) versiones
  **modificadas** de: `screencap.cpp`/`screencap.h`, `ans_contexts.cpp`/
  `ans_contexts.h`, `sub.cpp`/`sub.h`, `squad.cpp`/`squad.h`,
  `rans_byte.h`, `ransmt.h`, `logging.cpp`/`logging.h`, y una versión
  propia de `defines.h` (typedefs portables en vez de `<windows.h>`).
  **NO se vendoriza** `screenpressor.cpp`/`.h` (driver VfW, no hace
  falta), `conf.h`/`.cpp` (config por Registro de Windows, no aplica),
  `drvproc.cpp`, los recursos `.rc`/`.def`/`.bmp` (específicos del
  driver VfW de Windows).
- `squad.cpp` se simplifica: se elimina/comenta el branch `nThreads>1`
  (`CreateThread`/`CreateEvent`/etc.), dejando sólo el camino
  single-thread que ya existía en el código original.
- `main.cpp` (nuevo, nuestro): lee de stdin un stream simple
  (`ancho:uint32, alto:uint32, bpp:uint16` una vez, después por cada
  frame `tamaño:uint32` + bytes comprimidos), llama
  `ScreenCodec::Init(&params)` una vez y `DecompressFrame(...)` por cada
  frame, escribe a stdout los píxeles RGB crudos de cada frame en
  secuencia (formato que `ffmpeg -f rawvideo` puede leer directo).
- Build: `CMakeLists.txt` chico, sin dependencias externas (todo el
  código necesario es autocontenido). Se compila en un stage de build
  del `Dockerfile` del backend (imagen con `g++`/`cmake`), el binario
  resultante se copia a la imagen final (multi-stage, mismo patrón que
  ya usa `frontend/Dockerfile` para node→nginx).

### `app/domain/avi.py` (Python, nuevo)

- Parser RIFF/AVI puro (sin dependencias nuevas — `struct` de la
  stdlib alcanza): camina los chunks (`hdrl`→`avih` para
  ancho/alto/fps/cantidad de frames, `strl`→`strh`+`strf` para
  confirmar el FourCC `SCPR` y el `biBitCount`), después `movi`→ lista
  de chunks `00dc` (frame de video del stream 0) en orden.
- Devuelve `{width, height, bpp, fps, frames: list[bytes]}` — puro,
  testeable con un AVI de prueba chico (fixture en
  `tests/unit/fixtures/`, generado a mano o recortado de una grabación
  real de Globex si el tamaño lo permite).

### Pipeline en el backend

- `app/core/recording_transcode.py` (nuevo): orquesta
  `avi.parse(raw_avi_bytes)` → arma el stream de entrada para
  `scpr_decode` → `asyncio.create_subprocess_exec(SCPR_DECODE_BIN, ...)`
  (mismo patrón que `secrets.py` invoca el binario `age`) → pipea la
  salida (RGB crudo) a un segundo subprocess `ffmpeg -f rawvideo -pix_fmt
  rgb24 -s WxH -r FPS -i - -c:v libx264 -f mp4 -movflags
  frag_keyframe+empty_moov -` (flags para poder escribir a un pipe sin
  necesitar seek, ya que la salida va directo a la respuesta HTTP, no a
  un archivo) → devuelve los bytes del MP4.
- `app/api/recordings.py`: nuevo `POST .../recordings/{session_id}/
  play/mp4` — mismo gating manual que `.../play` (feature + reason,
  sin `writeop.run_write`) → `client.play_recording(session_id)` (AVI
  original) → `recording_transcode.to_mp4(avi_bytes)` → `Response(
  media_type="video/mp4")`. Audita `recording.play.mp4` (acción
  distinta de `recording.play`, Requisito 5).

### Docker / infraestructura

- `backend/Dockerfile`: nuevo build stage (`g++`/`cmake`) que compila
  `backend/tools/scpr_decode/` a un binario; se copia al stage final.
- Imagen final del `api` suma `ffmpeg` (paquete del sistema, `apt-get
  install ffmpeg` en el stage final — sin compilarlo, viene empaquetado
  en Debian/Ubuntu).
- `app/config.py`: settings nuevos `scpr_decode_bin` (path al binario,
  default `/usr/local/bin/scpr_decode`) + timeout de transcodeo.

### Frontend

- `components/RecordingPlayer.tsx` (nuevo, mucho más simple que el
  diseño descartado): `<video controls src={blobUrl} />` con manejo de
  `onError` (Requisito 4).
- `RecordingDownloadSection.tsx` → **renombrado**
  `RecordingPlayerSection.tsx`: razón compartida, dos botones —
  "Reproducir" (`apiBlob` a `.../play/mp4`, guarda el Blob, monta
  `<RecordingPlayer>`) / "Descargar" (`apiBlob` a `.../play` como hoy,
  `saveBlob`).

## Tareas

- [x] Investigar viabilidad de portar el códec MIT sin reimplementar el
      algoritmo (confirmado: sólo bypasear el driver VfW + single-thread).
- [x] Confirmar contra una grabación real de Globex qué versión de SCPR
      generan los tenants del MSP — **v2** (primer byte del primer
      frame `0x12` → `(0x12>>4)+1 = 2`), el entropy coder más simple
      (`UseRC`, sin threading — `ransmt.h`/`UseANS` sólo hacen falta para
      que el `switch` de `CreateCodec` compile, nunca se ejercen con
      datos reales del MSP). Muestra: `46_1132491.avi` (1920×1080,
      32 bpp, 5306 frames, 3 fps).
- [x] `backend/tools/scpr_decode/`: vendorizado (`screencap`/
      `ans_contexts`/`sub`/`squad`/`logging`/`rans_byte` byte-idénticos
      al repo original) + `windows.h` (shim portable) + `defines.h`/
      `ransmt.h` (adaptados) + `main.cpp` + `CMakeLists.txt` +
      `THIRD_PARTY_NOTICE.md`. **Sin compilar todavía** — sin g++/cmake/
      Docker en la máquina de desarrollo, pendiente que Alex instale
      Docker Desktop para la primera compilación de prueba.
- [x] `app/domain/avi.py`: parser RIFF/AVI + 5 tests unit — **validado
      además contra la grabación real** (`46_1132491.avi`): extrajo los
      5306 frames exactos que declara el header, dimensiones y códec
      correctos.
- [x] `app/core/recording_transcode.py`: pipeline subprocess (decode +
      ffmpeg encadenados por pipe real, sin bufferizar el RGB crudo
      completo en memoria) + settings nuevos en `app/config.py`
      (`scpr_decode_bin`, `ffmpeg_bin`, `recording_transcode_timeout`).
      **Riesgo real anotado sin resolver todavía:** la muestra de
      Globex son ~44 min de video a 1920×1080 — el RGB crudo sin
      comprimir de una grabación así pesa varios GB; el pipe evita
      bufferizarlo en Python, pero la velocidad real de `scpr_decode`
      (single-thread) + `ffmpeg` para algo de ese tamaño está sin medir.
- [x] `app/api/recordings.py`: endpoint `.../play/mp4` (gating
      compartido con `.../play` vía `_gate_and_fetch_avi`, refactor sin
      cambiar el comportamiento de `.../play`) + audit action
      `recording.play.mp4` (distinta de `recording.play`).
- [x] `backend/Dockerfile`: nuevo build stage `scpr_build`
      (`debian:bookworm-slim` + g++/cmake, compila `scpr_decode`) +
      `ffmpeg` instalado en la imagen final — **sin probar todavía** (el
      build stage nunca corrió, ver pendiente crítico abajo).
- [x] `components/RecordingPlayer.tsx` (`<video controls>` nativo, nada
      más — el backend ya entrega MP4) + `RecordingPlayerSection.tsx`
      (reemplaza `RecordingDownloadSection.tsx`, borrado: misma razón
      para "Reproducir"/"Descargar", Object URL revocado al reemplazar o
      desmontar). `RecordingDetailPanel.tsx` actualizado.
- [x] Tests unit: `avi.py` (5), gating de `.../play/mp4` (feature
      apagada, transcodeo falla → 502, ok) con `recording_transcode.
      to_mp4` mockeado — 3 tests nuevos en `test_recordings.py` (12 en
      total en el archivo). `ruff`+`mypy`+`pytest` del backend completo
      verdes.
- [x] Compilar `scpr_decode` por primera vez — Docker Desktop instalado
      (2026-09-16), reveló y se corrigieron varios problemas reales de
      portabilidad GCC vs MSVC en el shim (`min`/`max` sin calificar,
      `HMODULE`/`SYSTEM_INFO`/`__int64` faltantes, macro `lprintf` de 0
      argumentos, `memset`/`memcpy` sin `<cstring>`,
      `GetThreadLocalInt`/`SetThreadLocalInt` sin definir — resueltas en
      `main.cpp`, código propio, con `thread_local`). Compila limpio en
      local y en el server (`pam-primary`).
- [x] Correr el pipeline completo contra una grabación real de Globex —
      **funciona de punta a punta.** Bugs reales encontrados y corregidos
      en el camino:
      - `libx264` rechazaba alturas impares (1600×731 real) — filtro
        `pad=ceil(iw/2)*2:ceil(ih/2)*2` en el comando de ffmpeg.
      - Frame de 0 bytes (probablemente "sin cambios", frecuente en
        períodos sin actividad) crasheaba `scpr_decode` con
        `std::length_error` sin atrapar (SIGABRT sin info) — `main.cpp`
        ahora lo trata como "repetir el frame anterior" sin llamar al
        códec, y además atrapa cualquier excepción del códec vendorizado
        con diagnóstico (frame/ftype/firstByte).
      - Deadlock real de pipes con grabaciones grandes (el write de
        stdin_data era síncrono y bloqueante antes de drenar la salida de
        ffmpeg — con datos > 64KB los tres procesos quedaban trabados
        entre sí, sin timeout que lo detectara) — el write se mueve a un
        hilo aparte en `recording_transcode.py`.
      - CSP sin `media-src` bloqueaba el `blob:` URL del `<video>` —
        agregado a `frontend/security-headers.conf`.
      - **Orientación de filas: confirmada bottom-up** (como
        BITMAPINFOHEADER/DIB de Windows) — el video salía invertido
        verticalmente, corregido con `-vf vflip` en ffmpeg.
      - **Orden de canales BGR/BGRA: sin problema** — `bgr24`/`bgra` como
        `_PIX_FMT` ya asumía BGR, quedó confirmado, no hizo falta tocarlo.
      - **Metadata de color faltante** (encontrado después, revisando el
        MP4 descargado desde el `<video>` del navegador): reproducía
        perfecto en el reproductor nativo de Windows pero con colores
        raros en VLC — `ffprobe` confirmó `color_range`/`colorspace`/
        `color_primaries`/`color_transfer` todos "unknown" en el stream,
        cada reproductor los interpretaba distinto. Se agregan tags
        `bt709` + rango limitado explícitos, forzados también vía
        `-x264-params` porque las flags genéricas de ffmpeg no le llegan
        a los VUI que `libx264` escribe por su cuenta.
- [x] `lint` + `tsc` + `build` del frontend verdes.
- [x] Probado contra Globex con una grabación real de 4 minutos:
      reproduce correctamente en el `<video>` embebido.

## Fix post-cierre: timeout fijo no alcanzaba para grabaciones largas (2026-09-17)

Probando contra una grabación real de Globex de **20 minutos**: tardó
varios minutos y falló con `"transcodeo superó el timeout de 120.0s"`.
`recording_transcode_timeout` era un valor **fijo** de 120s — alcanzaba
para la grabación de 4 minutos con la que se probó originalmente, pero
transcodear es CPU-bound (decode SCPR + encode H.264), no en tiempo real:
el tiempo que toma escala con la duración de origen, un valor fijo no
tiene sentido.

**Fix:** `recording_transcode._transcode_timeout(frame_count, fps, *,
base, factor, cap)` — nueva función pura, calcula el timeout como
`duration_real_s * recording_transcode_timeout_factor` (default 3.0),
con un piso (`recording_transcode_timeout`, sigue en 120s, para
grabaciones cortas) y un techo (`recording_transcode_timeout_max`, nuevo,
default 540s). El techo importa porque nginx (`frontend/nginx.conf`)
corta la conexión a los 600s (`proxy_read_timeout`) sin importar qué diga
PAM Web Console — dejarlo con margen bajo eso asegura que el error de PAM Web Console (con el
motivo real) llegue antes que un 504 genérico sin contexto. 4 tests
nuevos (`test_recording_transcode.py`) sobre la función pura — el resto
de `to_mp4` sigue sin test unitario (shellea a binarios reales). De paso,
el texto de `RecordingPlayerSection.tsx` ("puede tardar unos segundos")
se corrigió para avisar que una grabación larga puede tardar varios
minutos. Pendiente reprobar contra la grabación de 20 minutos que
disparó esto.
