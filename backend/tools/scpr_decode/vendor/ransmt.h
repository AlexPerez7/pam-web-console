#ifndef RANSMT_H
#define RANSMT_H
#include <vector>
#include "defines.h"
#include "rans_byte.h"
#include "ans_contexts.h"

// Adaptado del ransmt.h original de Infognition (MIT) — SIN threading real.
//
// El original usa un hilo de Windows nativo para comprimir en paralelo
// mientras se sigue generando el siguiente bloque de datos a codificar —
// es exclusivamente parte del camino de CODIFICACIÓN (compresión). Este
// proyecto sólo DECODIFICA grabaciones ya comprimidas por CyberArk/PSM, y
// el camino de decodificación de `UseANS` (en screencap.h: `decodeBegin`/
// `decodeC`/`decodeF`) usa `RansDecInit`/`RansDecGet`/`RansDecAdvance` de
// `rans_byte.h` DIRECTO — nunca llama a los métodos de esta clase.
//
// La única razón por la que este archivo hace falta igual es que
// `UseANS` tiene `RansMTCoder rmtc;` como miembro de valor (no puntero) y
// `CScreenCapt::Deinit()` llama `ec.stop()` incondicionalmente (también al
// decodificar) — así que el struct debe existir y compilar, pero sus
// métodos de codificación (`start`/`put`/`finish`) nunca se ejecutan en
// este proyecto, quedan como stubs seguros en vez de portar el threading.
// Ver docs/specs/recording-player.md.
struct RansMTCoder {
  static const int B = 128 * 1024;
  RansState ransInitState;  // usado por UseANS::decodeBegin/encodeBegin

  void start(BYTE*) {}
  void put(Freq) {}
  BYTE* finish() { return nullptr; }
  void stop() {}
};  // RansMTCoder

#endif
