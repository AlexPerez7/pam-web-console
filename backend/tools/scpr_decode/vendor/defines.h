// Reemplazo portable del defines.h original de ScreenPressor (que incluía
// <windows.h> para DWORD/BYTE/WORD/BOOL/LONG). El autor original ya había
// dejado estos mismos typedefs comentados en el archivo real, como
// alternativa portable — acá quedan descomentados y activos en vez de
// <windows.h>. Ver docs/specs/recording-player.md.
#ifndef _DEFINES_H
#define _DEFINES_H

#include "windows.h"  // nuestro shim portable (vendor/windows.h), no el real

typedef unsigned int uint;

#define INLINE inline
#define Abs(x) ((x) >= 0 ? (x) : -(x))

#endif
