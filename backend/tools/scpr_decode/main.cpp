// scpr_decode — decodifica una grabación PSM (frames SCPR ya extraídos
// del contenedor AVI por el lado Python, `app/domain/avi.py`) a píxeles
// RGB crudos en stdout, listos para que ffmpeg los recodifique a MP4. Ver
// docs/specs/recording-player.md y THIRD_PARTY_NOTICE.md.
//
// Protocolo de entrada por stdin (todo little-endian, sin padding):
//   uint32 width
//   uint32 height
//   uint16 bits_per_pixel     (24 es lo único probado; 16/32 deberían
//                              andar igual, el códec original los
//                              soporta, pero sin verificar contra datos
//                              reales todavía)
//   luego, por cada frame:
//     uint32 frame_size
//     <frame_size> bytes de datos comprimidos SCPR
//   fin de stream: EOF (sin más frames a esperar)
//
// Salida por stdout: los frames decodificados, RGB/BGR crudo (ver nota de
// orden de canales más abajo), uno atrás del otro, cada uno de
// `height * width * (bpp/8)` bytes SIN padding de alineación por fila —
// el códec internamente trabaja con filas alineadas a 4 bytes (como
// BITMAPINFOHEADER), pero acá se descarta ese padding al escribir, para
// que la salida quede empaquetada tal cual espera el demuxer `rawvideo`
// de ffmpeg (que no acepta stride, sólo filas compactas).
//
// Errores: mensaje por stderr + código de salida != 0, sin escribir
// frames parciales a stdout después del error.
//
// CONFIRMADO CONTRA UNA GRABACIÓN REAL (Globex, 2026-09-16):
//   - Orientación de filas: bottom-up (como BITMAPINFOHEADER/DIB de
//     Windows) — el video salía invertido verticalmente; se corrigió con
//     `-vf vflip` en el comando de ffmpeg (`app/core/recording_transcode.py`),
//     sin tocar este archivo.
//
// PENDIENTE DE VERIFICAR CONTRA UNA GRABACIÓN REAL (ver checklist de la
// spec):
//   - Orden de canales (RGB vs BGR) — el código original nombra a los
//     bytes "r,g,b" en ese orden, pero es una convención típica de
//     Windows (BITMAPINFOHEADER/DIB) guardar los píxeles como BGR — si
//     los colores salen invertidos (rojo/azul cambiados), es acá, se
//     ajusta el `-pix_fmt` de ffmpeg del lado Python (bgr24/bgra en vez
//     de rgb24/rgba), no hace falta tocar este archivo.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>
#include <vector>

#include "defines.h"
#include "screencap.h"

// GetThreadLocalInt/SetThreadLocalInt: declaradas `extern` en screencap.h/
// ans_contexts.h (guardan el f0 de Cx6 entre encodeBegin/decodeBegin y su
// lectura en Cx6::init) pero nunca definidas en el subset vendorizado — el
// proyecto original las resuelve en un archivo no vendorizado (usa TLS real
// de Windows para el encoder multi-hilo). Este binario decodifica un solo
// stream por proceso, en un único hilo, así que una variable `thread_local`
// simple es equivalente en este caso — sin necesidad de TLS de verdad.
thread_local int g_threadLocalInt = 0;
void SetThreadLocalInt(int v) {
  g_threadLocalInt = v;
}
int GetThreadLocalInt() {
  return g_threadLocalInt;
}

namespace {

bool readExact(FILE* f, void* buf, size_t n) {
  return std::fread(buf, 1, n, f) == n;
}

bool writeExact(FILE* f, const void* buf, size_t n) {
  return std::fwrite(buf, 1, n, f) == n;
}

// Calcado de CodecInst::InferFrameType (screenpressor.cpp del proyecto
// original, no vendorizado — ver THIRD_PARTY_NOTICE.md) — determina si un
// frame es I (0, keyframe) o P (1, delta) a partir de su primer byte.
int inferFrameType(uint8_t firstByte, uint32_t dataSize) {
  switch (firstByte) {
    case 0:
      return 1;
    case 1:
      return dataSize <= 4 ? 0 : 1;
    case 0x02:
    case 0x11:
    case 0x12:
      return 0;
  }
  return -1;
}

}  // namespace

int main() {
  uint32_t width = 0, height = 0;
  uint16_t bitCount = 0;
  if (!readExact(stdin, &width, 4) || !readExact(stdin, &height, 4) ||
      !readExact(stdin, &bitCount, 2)) {
    std::fprintf(stderr, "scpr_decode: header truncado\n");
    return 1;
  }
  if (width == 0 || height == 0 || (bitCount != 16 && bitCount != 24 && bitCount != 32)) {
    std::fprintf(stderr, "scpr_decode: header invalido (width=%u height=%u bpp=%u)\n", width,
                 height, (unsigned)bitCount);
    return 1;
  }

  CodecParameters params;
  params.width = width;
  params.height = height;
  params.bits_per_pixel = bitCount;
  params.redmask = params.greenmask = params.bluemask = 0;  // sólo 24bpp probado hasta ahora
  params.high_range_x = 256;
  params.high_range_y = 256;
  params.low_range_x = 8;
  params.low_range_y = 8;
  params.loss = 0;

  ScreenCodec codec;
  codec.Init(&params);

  const int bpp = bitCount / 8;
  const int pitch = (width * bpp + 3) & (~3);  // el códec exige filas alineadas a 4
  const size_t rowBytes = static_cast<size_t>(width) * bpp;  // sin padding, para stdout
  std::vector<uint8_t> outBuf(static_cast<size_t>(pitch) * height);
  std::vector<uint8_t> packedRow(rowBytes);
  std::vector<uint8_t> inBuf;

  int ftype = 0;  // primer frame: asumir I (keyframe) por defecto
  uint32_t frameIndex = 0;
  for (;;) {
    uint32_t frameSize = 0;
    size_t got = std::fread(&frameSize, 1, 4, stdin);
    if (got == 0) break;  // EOF prolijo, fin del stream
    if (got != 4) {
      std::fprintf(stderr, "scpr_decode: frame %u con header truncado\n", frameIndex);
      return 1;
    }
    inBuf.resize(frameSize);
    if (frameSize > 0 && !readExact(stdin, inBuf.data(), frameSize)) {
      std::fprintf(stderr, "scpr_decode: frame %u truncado (esperaba %u bytes)\n", frameIndex,
                   frameSize);
      return 1;
    }

    // Frame de 0 bytes: visto contra una grabación real de Globex (frame
    // 32). Es, casi seguro, la forma más barata de decir "sin cambios
    // frente al anterior" (más barata todavía que el 1 byte con changes=0
    // de DecompressP) -- probablemente producto de cómo PSM graba períodos
    // sin actividad. El códec vendorizado no tiene un caso para esto (todos
    // sus caminos leen al menos 1 byte); se resuelve del lado de este
    // driver, repitiendo tal cual el `outBuf` del frame anterior sin llamar
    // al códec. Para el frame 0 (sin frame anterior) da un cuadro negro en
    // vez de crashear -- degradado razonable para un caso que no debería
    // ocurrir en una grabación real.
    if (frameSize == 0) {
      for (uint32_t y = 0; y < height; y++) {
        std::memcpy(packedRow.data(), outBuf.data() + static_cast<size_t>(y) * pitch, rowBytes);
        if (!writeExact(stdout, packedRow.data(), rowBytes)) {
          std::fprintf(stderr, "scpr_decode: error escribiendo el frame %u a stdout\n",
                       frameIndex);
          return 1;
        }
      }
      frameIndex++;
      continue;
    }

    int inferred = inferFrameType(inBuf[0], frameSize);
    if (inferred >= 0)
      ftype = inferred;
    else if (frameIndex > 0)
      ftype = 1;  // ambiguo: asumir P (delta), igual que el fallback original

    // El códec vendorizado (sub.h RangeCoderSub::DecodeBegin) lanza
    // std::length_error/std::invalid_argument si un frame llega mal
    // clasificado a su decodificador de entropía completo (frame_size
    // insuficiente para los 5 bytes mínimos que necesita) — sin este
    // try/catch, esa excepción quedaba sin atrapar y terminaba el proceso
    // con SIGABRT sin decir nada (ni siquiera el número de frame),
    // rompiendo el contrato de errores documentado arriba.
    int ret = 0;
    try {
      ret = codec.DecompressFrame(inBuf.data(), static_cast<int>(frameSize), outBuf.data(), pitch,
                                   ftype);
    } catch (const std::exception& e) {
      std::fprintf(stderr,
                    "scpr_decode: excepcion decodificando el frame %u (size=%u ftype=%d "
                    "firstByte=%d): %s\n",
                    frameIndex, frameSize, ftype, inBuf[0], e.what());
      return 1;
    }
    if (ret <= 0) {
      std::fprintf(stderr, "scpr_decode: fallo decodificando el frame %u\n", frameIndex);
      return 1;
    }
    for (uint32_t y = 0; y < height; y++) {
      std::memcpy(packedRow.data(), outBuf.data() + static_cast<size_t>(y) * pitch, rowBytes);
      if (!writeExact(stdout, packedRow.data(), rowBytes)) {
        std::fprintf(stderr, "scpr_decode: error escribiendo el frame %u a stdout\n", frameIndex);
        return 1;
      }
    }
    frameIndex++;
  }

  codec.Deinit();
  if (frameIndex == 0) {
    std::fprintf(stderr, "scpr_decode: no se decodifico ningun frame\n");
    return 1;
  }
  return 0;
}
