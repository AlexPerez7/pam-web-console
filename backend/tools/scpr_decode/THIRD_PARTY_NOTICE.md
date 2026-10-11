# scpr_decode — aviso de terceros

Esta herramienta decodifica el códec de video **SCPR (ScreenPressor)**
que usa CyberArk PSM para grabar sesiones. Ver
`docs/specs/recording-player.md`.

## Código vendorizado

`vendor/{ans_contexts,sub,screencap,squad,logging}.{h,cpp}` y
`vendor/rans_byte.h` son del proyecto
[`thedeemon/screenpressor`](https://github.com/thedeemon/screenpressor)
(Dmitry Popov / Infognition Co. Ltd.), **MIT License** — texto completo en
`vendor/LICENSE`. `rans_byte.h` es además dominio público (Fabian 'ryg'
Giesen, 2014, ver el aviso al inicio del archivo).

Los archivos de arriba están **vendorizados sin modificar** (verificable
contra el repo original). Dos archivos SÍ son adaptaciones nuestras, no
vendorizado directo:

- `vendor/windows.h`: reemplazo portable de `<windows.h>`/`<Windows.h>`.
  El único uso real de threading nativo de Windows en el código original
  (`CSquad` con más de un hilo) vive exclusivamente en el camino de
  **codificación** (`CScreenCapt::CompressFrame`), que este proyecto
  nunca ejecuta — sólo decodifica grabaciones ya comprimidas. Este shim
  define los tipos/símbolos necesarios para que el código vendorizado
  compile tal cual, con funciones de threading que abortan si alguna vez
  se llegaran a invocar de verdad (no debería pasar, dado que `pSquad`
  queda en `NULL` toda la vida de un objeto de sólo-decodificación).
- `vendor/ransmt.h`: adaptación de `ransmt.h` original — ese archivo SÍ
  crea un hilo real en su constructor (a diferencia de `CSquad`, es un
  miembro de valor, no lazy), pero sigue siendo parte exclusiva del
  camino de codificación (`RansMTCoder::put`/`start`/`finish`, invocados
  sólo por `UseANS::encodeBegin`/`encodeC`, nunca por el camino de
  decodificación). Se gutéo el threading, dejando stubs seguros — ver el
  comentario al inicio de ese archivo para el detalle.
- `vendor/defines.h`: reemplazo del `defines.h` original — el propio
  autor había dejado estos mismos typedefs portables **comentados** en
  el archivo real (como si hubiera anticipado portarlo), acá quedan
  activos en vez de `#include <windows.h>`.

**No se vendorizó** `screenpressor.cpp`/`.h` (driver del códec de Video
for Windows, la interfaz que usa Windows Media Player — no aplica acá,
`main.cpp` llama directo a `ScreenCodec`), `conf.h`/`.cpp` (configuración
vía Registro de Windows, no aplica), ni los recursos `.rc`/`.def`/`.bmp`
específicos del driver de Windows.

## `main.cpp` y `CMakeLists.txt`

Código nuestro (no vendorizado), calcado de la lógica real de
`screenpressor.cpp::CodecInst::Decompress`/`InferFrameType` (leída del
repo original para entender el protocolo, no copiada — son ~15 líneas de
lógica de despacho, reescritas desde cero en `main.cpp`).
