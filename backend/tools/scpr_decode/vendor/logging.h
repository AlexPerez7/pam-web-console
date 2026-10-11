#ifndef LOGGING_H
#define LOGGING_H
#include <stdio.h>

//#define DO_LOG

extern FILE *logF;
#ifdef DO_LOG
 #define lprintf log_printf
 void log_printf(FILE *f, char * format, ...);
#else
 // El original define esto como macro de 0 argumentos (`lprintf()`) pero el
 // código lo invoca con argumentos (`lprintf(logF, "...")`) — el
 // preprocesador legacy de MSVC lo toleraba, GCC no: exige que la cantidad
 // de argumentos coincida. Variádica y sin efecto, igual que el original
 // pretendía (no-op cuando DO_LOG no está definido).
 #define lprintf(...) ((void)0)
#endif


#endif