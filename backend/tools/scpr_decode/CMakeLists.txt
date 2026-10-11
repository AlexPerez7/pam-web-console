cmake_minimum_required(VERSION 3.16)
project(scpr_decode CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# NOPROTECT ya está definido dentro de vendor/screencap.h (deshabilita el
# bloque anti-piratería del original, no aplica a este build). TIMING
# queda sin definir (default upstream), lo que también deja afuera el
# código de profiling atado a QueryPerformanceCounter. Ver
# THIRD_PARTY_NOTICE.md.

add_executable(scpr_decode
  main.cpp
  vendor/screencap.cpp
  vendor/ans_contexts.cpp
  vendor/sub.cpp
  vendor/squad.cpp
  vendor/logging.cpp
)

target_include_directories(scpr_decode PRIVATE vendor)

if(NOT MSVC)
  target_compile_options(scpr_decode PRIVATE -O2 -Wno-unused-parameter)
endif()
