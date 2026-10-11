// win32_shim — reemplazo portable de <windows.h>/<Windows.h> para compilar
// el código vendorizado de ScreenPressor (screencap.*, squad.*, ransmt.h)
// en Linux, SIN portar su threading nativo de Win32.
//
// screencap.cpp sólo crea un CSquad real (multi-hilo) dentro de
// CompressFrame() — el camino de ENCODE, que este proyecto nunca usa (sólo
// decodifica grabaciones ya comprimidas por CyberArk/PSM). Para el decode
// puro, `pSquad` queda en NULL toda la vida del objeto. Por eso estos
// símbolos de threading no necesitan funcionar de verdad — sólo existir
// para que el código vendorizado compile tal cual, sin tocarle una línea.
// Si alguna vez se llegaran a invocar de verdad (no debería pasar en este
// proyecto), abortan en vez de fallar en silencio.
//
// Ver docs/specs/recording-player.md.
#ifndef PAM_WIN32_SHIM_H
#define PAM_WIN32_SHIM_H

#include <cstddef>
#include <cstdlib>
#include <cstdint>
#include <cstring>  // memset/memcpy — <windows.h> real los arrastra transitivamente

typedef uint32_t DWORD;
typedef uint16_t WORD;
typedef unsigned char BYTE;
typedef int32_t LONG;
typedef int64_t __int64;  // builtin de MSVC, no existe en GCC
typedef int BOOL;
typedef void* LPVOID;
typedef void* HANDLE;
typedef void* HMODULE;

#ifndef TRUE
#define TRUE 1
#define FALSE 0
#endif

#define WINAPI
#define INFINITE 0xFFFFFFFFu
#define WAIT_OBJECT_0 0u

// minwindef.h define min/max como macros (sin NOMINMAX) — screencap.cpp los
// usa sin calificar. Una macro de texto los rompería: <algorithm>/<string>
// de libstdc++ usan identificadores "min"/"max" en su propio código interno
// (con otro significado) y una macro sin higiene los corrompe donde sea que
// aparezcan después de este include. Funciones template en el namespace
// global resuelven por sobrecarga normal — sin ese problema — y ningún
// archivo vendorizado tiene un miembro/variable llamado literalmente
// "min"/"max" que pudiera chocar.
// Dos parámetros de tipo (no uno solo): los call sites reales mezclan tipos
// (p.ej. min(uint, int) en screencap.cpp) — la macro original de Windows lo
// resuelve con conversión implícita; con un único T la deducción de
// template falla.
#ifndef max
template <typename A, typename B>
inline auto max(A a, B b) -> decltype(a > b ? a : b) {
  return a > b ? a : b;
}
#endif
#ifndef min
template <typename A, typename B>
inline auto min(A a, B b) -> decltype(a < b ? a : b) {
  return a < b ? a : b;
}
#endif

struct CRITICAL_SECTION {
  int unused;
};
inline void InitializeCriticalSection(CRITICAL_SECTION*) {}
inline void DeleteCriticalSection(CRITICAL_SECTION*) {}
inline void EnterCriticalSection(CRITICAL_SECTION*) {}
inline void LeaveCriticalSection(CRITICAL_SECTION*) {}

inline HANDLE CreateEvent(void*, int, int, void*) {
  std::abort();
  return nullptr;
}
inline HANDLE CreateThread(void*, size_t, DWORD(WINAPI*)(void*), void*, DWORD, DWORD*) {
  std::abort();
  return nullptr;
}
inline DWORD WaitForSingleObject(HANDLE, DWORD) {
  std::abort();
  return 0;
}
inline DWORD WaitForMultipleObjects(DWORD, const HANDLE*, int, DWORD) {
  std::abort();
  return 0;
}
inline DWORD SignalObjectAndWait(HANDLE, HANDLE, DWORD, int) {
  std::abort();
  return 0;
}
inline void SetEvent(HANDLE) {
  std::abort();
}
inline void ResetEvent(HANDLE) {
  std::abort();
}
inline void CloseHandle(HANDLE) {
  std::abort();
}

// SYSTEM_INFO/GetSystemInfo: usado sólo en CompressFrame() para elegir la
// cantidad de hilos del CSquad real — mismo camino de ENCODE que el resto
// del threading, nunca ejercitado por este proyecto (sólo decodifica).
struct SYSTEM_INFO {
  DWORD dwNumberOfProcessors;
};
inline void GetSystemInfo(SYSTEM_INFO*) {
  std::abort();
}

#endif  // PAM_WIN32_SHIM_H
