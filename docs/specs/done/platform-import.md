# platform-import — importar una plataforma desde un paquete `.zip`

- **Estado:** hecha, **probada contra Globex (2026-09-24)** — exportó
  una plataforma existente y reimportó el `.zip` con otro nombre, funcionó
  bien. Confirma de paso el formato base64 (la ambigüedad que quedaba
  abierta en "Diseño técnico" más abajo).
- **Roadmap:** `docs/PLAN.md §7` Fase 3, ítem "Activar/desactivar/duplicar/
  importar plataformas" (la parte que `platform-lifecycle.md` había
  dejado explícitamente afuera)
- **Fecha:** 2026-09-14

## Problema

`platform-lifecycle.md` implementó activar/desactivar/duplicar/borrar
plataformas pero dejó importar afuera a propósito (más superficie de UI,
sin pedido concreto todavía). Ahora hay pedido: complementar
`PlatformExportButton` (descarga el paquete de una plataforma) con la
vuelta — subir ese mismo tipo de paquete para crear una plataforma nueva.

## Alcance

**Entra (v1, el modo más simple y de menor riesgo de los tres que expone
la API):**
- `POST /tenants/{id}/platforms/import` — sube un `.zip` y crea una
  plataforma **nueva** a partir de él (`API/Platforms/Import`, CyberArk
  10.2+). Riesgo bajo/medio: sólo crea un objeto nuevo, no toca ninguna
  plataforma existente — mismo criterio que `platform.duplicate`
  (`operator+`, sin confirmación reforzada).
- Botón "Importar plataforma" en el toolbar del tab Plataformas (junto a
  donde iría, si existiera, "Crear plataforma" — CyberArk no tiene un
  "Add Platform" manual, sólo import/duplicate).

**Fuera de v1 (dos modos adicionales que expone la misma
`Import-PASPlatform` de `psPAS`, con más riesgo o menos claros):**
- **"Update" (`POST /API/Platforms/{id}/Update`, CyberArk 14.2+, flag
  `Force`)** — re-importa un `.zip` **sobre una plataforma existente**,
  sobrescribiendo su configuración. Mucho más riesgo que crear una nueva
  (afecta a una plataforma en uso, sin diff posible de "qué cambia" porque
  el contenido real del `.zip` es opaco) — se evalúa después si hay un
  caso de uso concreto (ej. reaplicar un parche de plataforma que
  CyberArk publica).
- **"SideBySide" (`PATCH /API/Platforms/Import`, CyberArk 14.6+)** — modo
  menos documentado en `psPAS` (recibe `PlatformId`/`PlatformName`/
  `Description`, sin archivo); semántica no del todo clara sin probarlo,
  no vale la pena implementarlo a ciegas.
- Import en lote.

## Investigación: endpoint y una ambigüedad real sin resolver

`docs.cyberark.com` bloqueó el fetch automatizado también para esta
página. Verificado contra `psPAS`
(`psPAS/Functions/Platforms/Import-PASPlatform.ps1`):

```
POST /API/Platforms/Import
Body: {"ImportFile": <contenido del .zip>}
```

**Ambigüedad sin resolver, importante:** el helper que arma el body
(`Get-ByteArray`) lee el archivo como `[System.IO.File]::ReadAllBytes()`
— un **array de bytes crudo**, no base64 — y `psPAS` lo manda tal cual
dentro del JSON (`ConvertTo-Json` de un `byte[]` en PowerShell serializa
como un **array de enteros**, ej. `[80, 75, 3, 4, ...]`, no un string).
Es un formato inusual para transportar un `.zip` en JSON (ineficiente y
poco común en APIs REST reales) — es **posible que sea así de verdad**
(CyberArk expone algunas APIs viejas con contratos poco convencionales,
ver el caso ya documentado de `PlatformID` vs ID numérico) o que la
implementación real de CyberArk en realidad espere el contenido en
**base64** y `psPAS` tenga un comportamiento distinto/un bug en este
punto puntual. **No se puede confirmar sin probarlo contra un tenant
real** — a diferencia del resto de specs de esta sesión, acá `psPAS` no
alcanza como fuente única de verdad.

**Diseño defensivo mientras tanto:** `client.import_platform(zip_bytes)`
intenta primero con el archivo codificado en **base64** (la convención
más común en APIs REST reales para blobs binarios en JSON); si CyberArk
devuelve 400/422, se documenta el error tal cual llega (no se reintenta
en silencio con el otro formato — mejor que la primera prueba contra
Globex falle con un mensaje claro a que quede una ambigüedad
enmascarada). Se ajusta con el resultado de esa prueba, antes de cerrar
la spec.

## Requisitos

1. **Importar un `.zip` válido.** Sube el archivo elegido, con `dry_run`
   no aplicable de la forma habitual (no hay forma de previsualizar el
   contenido de un `.zip` sin parsearlo — CyberArk no expone eso). El
   "dry-run" de esta operación es literal: **no se implementa un
   preview real**, se avisa explícitamente en la UI que importar ejecuta
   directo (ver Diseño técnico).
2. **Archivo inválido / rechazado por CyberArk.** El error de CyberArk se
   muestra tal cual (no se interpreta ni se traduce) — la superficie de
   fallas de un `.zip` mal armado es grande y no vale la pena tratar de
   adivinar cada caso.
3. **Plataforma nueva visible.** Tras importar con éxito, la plataforma
   aparece en el tab Plataformas sin esperar el TTL del caché
   (`platforms_rows_cached` se invalida).

## Diseño técnico

- **Sin `dry-run` real** (a diferencia de todo el resto de Fase 2/3):
  `writeop.run_write` exige un `build_plan` que compute un diff/efecto
  ANTES de tocar CyberArk, pero acá no hay nada que previsualizar del
  contenido de un `.zip` sin abrirlo. Dos opciones evaluadas:
  1. No pasar por `run_write` — endpoint propio con su propio gating
     manual de feature-flag + `reason` + audit (mismo patrón "acción sin
     dry-run real" que `account.reveal`).
  2. Pasar por `run_write` con un "dry-run" que sólo dice "se va a
     importar un archivo de N KB, no se puede previsualizar el
     contenido" (sin diff real) y ejecuta en el segundo paso, igual UX
     que el resto.
  **Se elige la opción 2** — mantiene la UI consistente
  (`<DryRunDialog>` en dos pasos, con `reason` obligatoria) aunque el
  "preview" sea sólo el nombre/tamaño del archivo, no un diff real.
- `backend/app/cyberark/client.py`: `import_platform(file_bytes: bytes) ->
  dict` — `POST /API/Platforms/Import` con
  `{"ImportFile": base64.b64encode(file_bytes).decode()}`,
  `retry_safe=False` (no idempotente).
- `backend/app/domain/platforms.py`: `plan_platform_import(_current,
  changes)` — `changes = {"filename": str, "size_bytes": int}` (nunca el
  contenido real, evita mandar el archivo dos veces y que quede en el
  audit); `effect` = `f"Se importa la plataforma desde **{filename}**
  ({size_kb} KB) — CyberArk decide el resultado, no hay preview real del
  contenido."`; sin `confirm_phrase` (riesgo bajo, mismo criterio que
  `platform.duplicate`).
- `backend/app/api/writes.py`: `POST /tenants/{id}/platforms/import` —
  `multipart/form-data` (FastAPI `UploadFile`), no JSON — es el único
  endpoint de escritura de PAM Web Console que sube un archivo. `fetch` no hace
  nada (`return {}`, no hay estado "actual" que traer); `apply` lee los
  bytes del `UploadFile` y llama a `client.import_platform`. Valida
  `.zip` por extensión antes de llegar a CyberArk (422 si no, sin
  auditar — mismo criterio que otras validaciones de forma).
- **`extraBody`/`changes` con un archivo:** `DryRunDialog` hoy manda
  `changes` como JSON — subir un archivo real necesita un flujo de
  request distinto (no cabe en el patrón `{changes, dry_run, reason}`
  actual). Frontend: `PlatformImportDialog.tsx` hace el preview
  "manual" (arma el mismo `{filename, size_bytes}` que espera
  `plan_platform_import` y lo manda como querystring/JSON en el paso de
  preview) y en el paso de confirmar recién ahí manda el archivo real
  por `multipart/form-data` con `fetch`/`FormData` nativo (no
  `DryRunDialog` genérico — necesita su propio componente, más chico,
  inspirado en el mismo flujo de 2 pasos pero sin reusar el componente
  tal cual).
- `backend/app/core/features.py` + `frontend/src/pages/Tenants.tsx`:
  `platform.import` en ambas listas.
- Invalidar `pam:platforms:{tenant_id}` al importar (igual que
  `platform.duplicate`).

## Tareas

- [x] `client.py`: `import_platform` (manda el archivo en base64).
- [x] `domain/platforms.py`: `plan_platform_import`.
- [x] `writes.py`: endpoint `multipart/form-data` (`UploadFile`/`File`/
      `Form`; nuevo `python-multipart==0.0.32` en `requirements.txt`).
- [x] `features.py` + `Tenants.tsx`: `platform.import` en ambas listas.
- [x] `PlatformImportDialog.tsx` (componente propio, no
      `<DryRunDialog>` genérico — nuevo helper `apiForm()` en `lib/api.ts`
      para `multipart/form-data`) + botón en el toolbar de Plataformas.
- [x] Tests unit: import ok (confirma el body real en base64), extensión
      inválida 422, archivo demasiado grande 422, RBAC, feature-flag.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [ ] **Probar contra Globex antes de cerrar — en particular resolver
      la ambigüedad de codificación (base64 vs array de bytes) del
      campo `ImportFile`, con un `.zip` de prueba real (se puede generar
      exportando una plataforma existente con `PlatformExportButton` y
      reimportándola con un nombre distinto).**
