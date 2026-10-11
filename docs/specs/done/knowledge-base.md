# knowledge-base — Base de conocimiento de errores (causa/solución)

- **Estado:** aprobada — confirmado con Alex (2026-09-15): matching por
  texto simple (no regex), RBAC `operator+` en al menos un tenant.
- **Roadmap:** transversal (no es un ítem literal del roadmap original,
  surgió mirando Troubleshooting) — vive junto a Fase 4 en `docs/PLAN.md`.
- **Fecha:** 2026-09-15
- **Base:** `docs/specs/troubleshooting-errores-cpm.md` (punto de
  integración principal — el `Reason` que ya se muestra ahí),
  `docs/specs/rotation-campaigns.md` (primer precedente de tabla
  PAM Web Console-nativa, aunque esa es tenant-scoped y ésta no).

## Problema

CyberArk suele tener artículos con causa/solución para errores conocidos
del CPM (ej. `community.cyberark.com/.../CPM-winRc-5-Access-is-denied`),
pero no hay forma de conectarlos con lo que se ve en Troubleshooting — el
operador tiene que buscar a mano cada vez, y lo que se aprende resolviendo
un caso real con un cliente se pierde (no queda registrado en ningún
lado dentro de PAM Web Console).

## Alcance

**Entra:**
- Base de conocimiento **propia de PAM Web Console**, no un scraping ni una
  integración con la comunidad de CyberArk (no existe API pública para
  eso) — Alex (y el equipo a futuro) la carga a mano, arrancando con
  artículos de CyberArk adaptados + casos reales con clientes.
- **Global, no tenant-scoped** — a diferencia de todo lo demás en PAM Web Console
  (Campañas incluido), el conocimiento no es de un cliente puntual.
  Primera tabla verdaderamente global de PAM Web Console.
- CRUD de artículos: categoría (rotación/conexión/verificación/otro),
  patrón de error (texto simple), causa posible, solución, link externo
  opcional.
- **Matching automático en Troubleshooting:** en
  `TroubleshootingAccountPanel`, si el `Reason` de la última falla
  contiene el patrón de algún artículo (comparación simple, sin
  mayús/minús — no regex), se muestra una tarjeta "Solución conocida"
  con causa + solución + link.
- **"Agregar a la base de conocimiento" desde el mismo panel** cuando no
  hay match — abre el alta con el patrón pre-cargado con el `Reason` real
  (recortado), para que cada caso resuelto en producción se convierta en
  un artículo sin trabajo extra.
- Página nueva `/knowledge-base` (espacio Operación, sin selector de
  tenant).

**Fuera (de este corte):**
- Expresiones regulares — texto simple alcanza para v1; si en el uso real
  hace falta más precisión (rangos de códigos, variantes), se suma
  después sin romper lo existente (el campo sigue siendo texto).
- Versionado/historial de ediciones de un artículo.
- Adjuntos/imágenes en la solución — texto plano.
- Matching también en `LastCpmErrorCell` (la columna de la lista) — se
  puede sumar rápido después, hoy sólo vive en el detalle de
  Troubleshooting para no pagar una fetch extra por cada fila de la
  lista.

## Requisitos

1. **Crear un artículo.** Categoría + patrón + causa + solución
   (obligatorios) + link (opcional) → queda visible para cualquier
   operador con acceso a algún tenant.
2. **Match automático.** Con un artículo `error_pattern: "Access is
   denied"`, una cuenta cuyo `Reason` sea `"CPM-winRc-5 Access is denied
   (winRc=5)"` muestra la tarjeta de solución en
   `TroubleshootingAccountPanel`.
3. **Sin match.** Si ningún artículo matchea, se ve un botón "Agregar a
   la base de conocimiento" con el `Reason` real pre-cargado como patrón.
4. **Editar/borrar.** Un `operator+` (en al menos un tenant) puede editar
   o borrar cualquier artículo — no hay noción de "dueño" del artículo en
   v1 (es conocimiento compartido).
5. **RBAC de lectura.** Cualquier operador autenticado (con acceso a al
   menos un tenant) puede ver la base — no hace falta ser `operator+`
   para leer, sólo para escribir.

## Diseño técnico

### RBAC nuevo — recurso global, no tenant-scoped

`require_tenant_access(minimum)` no aplica acá (no hay `tenant_id` en la
ruta). Se agrega:

- `app/core/rbac.py`: `has_role_on_any_tenant(op, minimum, session) ->
  bool` — True si el operador tiene rol efectivo ≥ `minimum` en al menos
  un tenant (o es `platform_admin`). Mismo criterio que
  `useAnyTenantAdmin()` del frontend, pero server-side.
- `app/core/deps.py`: `require_any_tenant_role(minimum)` — dependencia
  nueva para endpoints sin `tenant_id` en el path.

### Modelo (Postgres, nuevo — migración `0008`)

- `app/models/knowledge_base.py`: `KbArticle` (id, category, title,
  error_pattern, cause, solution, source_url nullable, created_by,
  updated_by, created_at, updated_at vía `TimestampMixin`). **Sin
  `tenant_id`** — la diferencia clave con `Campaign`.

### Backend

- `app/domain/knowledge_base.py`: `match_article(text, articles) ->
  dict | None` — puro, primer artículo cuyo `error_pattern` aparece
  (case-insensitive, substring simple) en `text`. Sin regex.
- `app/api/knowledge_base.py` (router nuevo, prefix `/knowledge-base`,
  **sin** `{tenant_id}`):
  - `GET /knowledge-base` — lista completa. `require_any_tenant_role(viewer)`.
  - `POST /knowledge-base` — `require_any_tenant_role(operator)`,
    auditado `kb.create` (con `tenant_id=None` en el audit — primer caso
    de una auditoría sin tenant).
  - `GET /knowledge-base/{id}`.
  - `PUT /knowledge-base/{id}` — `require_any_tenant_role(operator)`,
    auditado `kb.update`.
  - `DELETE /knowledge-base/{id}` — `require_any_tenant_role(operator)`,
    auditado `kb.delete`.

### Frontend

- `pages/KnowledgeBase.tsx` (ruta `/knowledge-base`, **sin** `TenantScope`
  — es lo primero en PAM Web Console sin selector de tenant): lista + alta en diálogo
  simple (`KbArticleFormDialog.tsx`, solo creación). **Actualizado
  (2026-09-15):** vive en un espacio de nav propio "Base de conocimiento"
  (ícono `BookOpen`), separado de Operación — mismo criterio que Identity,
  es un recurso global, no de un tenant puntual.
- **Segunda vuelta de diseño el mismo día** — Alex pidió que cada artículo
  se abra en una **sección propia**, no compartiendo pantalla con la lista
  (se probó primero un layout de dos paneles calcado de Safes, descartado
  a pedido de Alex), y que sea **editable en línea** en esa misma página
  (no un diálogo aparte) — confirmado con Alex: ruta propia por artículo
  (no un overlay de pantalla completa) y edición de **texto plano en
  línea** (no un editor de texto enriquecido tipo Word/foro — eso
  hubiera exigido guardar el contenido con formato HTML/Markdown +
  sanitizar al mostrarlo, mucho más trabajo para el beneficio real).
  `pages/KnowledgeBaseArticle.tsx` (ruta `/knowledge-base/:id`): un
  artículo completo — categoría/patrón/título arriba, Editar/Borrar como
  acciones, Causa/Solución en prosa legible (`whitespace-pre-wrap`), link
  al artículo original si existe. "Editar" vuelve los mismos campos
  editables ahí mismo (inputs/textareas en línea, sin diálogo) con
  Guardar/Cancelar — reemplaza el modo edición que tenía
  `KbArticleFormDialog.tsx` (ahora es solo de creación, sin prop
  `article`). `components/KbArticleDetailPanel.tsx` (el panel de dos
  columnas de la primera vuelta) se eliminó por completo, sin reemplazo
  parcial — quedó sin uso.
- `components/TroubleshootingAccountPanel.tsx`: después del bloque
  "Último error", si hay match contra la base de conocimiento (fetch de
  `GET /knowledge-base` una sola vez, matching en el cliente con la misma
  lógica que el backend — no hace falta un endpoint de "buscar por
  texto"), tarjeta con causa/solución/link; si no hay match, botón
  "Agregar a la base de conocimiento" → `KbArticleCreateDialog.tsx` con
  `error_pattern` pre-cargado.

## Tareas

- [x] `app/core/rbac.py`: `has_role_on_any_tenant` + tests.
- [x] `app/core/deps.py`: `require_any_tenant_role`.
- [x] `app/models/knowledge_base.py` + migración `0008_knowledge_base.py`.
- [x] `app/domain/knowledge_base.py`: `match_article` + tests unit.
- [x] `app/api/knowledge_base.py`: 5 endpoints + tests (CRUD, RBAC,
      auditoría sin tenant).
- [x] `pages/KnowledgeBase.tsx` + ruta/ítem de nav en Operación.
- [x] `TroubleshootingAccountPanel.tsx`: tarjeta de solución conocida +
      botón "Agregar" con patrón pre-cargado.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md`.
- [x] Migración `0008` corrida en el server.
- [x] Probado contra Globex (2026-09-18): artículos reales cargados,
      match confirmado en una cuenta con ese error. Spec cerrada.

## Extra — pegar artículo y autocompletar campos (2026-09-15)

Pedido de Alex tras cerrar el corte v1: cargar un artículo campo por campo
es lento, sobre todo con artículos largos de CyberArk (secciones Cause/
Resolution extensas). Evaluadas 3 opciones con Alex:

1. **Heurística de texto (elegida).** Sin dependencias nuevas, sin datos
   saliendo de la red. `lib/kbArticleParser.ts` reconoce los encabezados
   fijos que usa el formato de copiado de CyberArk (`Title`, `Cause`,
   `Resolution`, ignorando `Issue / Details`/`Product`/`Environment`/
   `Resources`) y agrupa el texto entre ellos — el operador pega el
   artículo completo tal cual, un botón "Detectar campos" completa
   título/causa/solución (y el patrón de error, extraído de lo que esté
   entre comillas simples en el título — ej. `'Access is denied.'` →
   `Access is denied`, que coincide con el `Reason` real que llega desde
   CyberArk). **Solo completa campos vacíos** — si `pattern` ya viene
   precargado desde "Agregar a la base de conocimiento" (el `Reason` real
   de una cuenta en Troubleshooting), el parser no lo pisa, así se
   mantiene el texto exacto observado en producción aunque se pegue
   después un artículo de referencia.
2. IA vía API externa (Gemini tiene tier gratuito real): descartada por
   ahora — primera vez que algo de PAM Web Console saldría de la red on-prem de
   el MSP, y un caso propio con un cliente podría incluir nombres de
   servidor/cuenta reales en el texto pegado.
3. IA local (Ollama, modelo chico): descartada — el server (`pam-primary`,
   8 vCPU/12 GB, sin GPU) no tiene margen cómodo para correr un modelo
   permanentemente sin competir con Postgres/Redis/la app.

Sin backend nuevo — es parsing puro en el cliente, ningún dato pegado
sale del navegador del operador hasta que se guarda el formulario (mismo
POST/PUT de siempre). Probado manualmente contra el artículo real
`CPM-winRc-5` (`test-kb-parser.js` descartable, no forma parte del repo).

## Decisiones abiertas

1. **¿La auditoría de `kb.*` necesita `tenant_id`?** El modelo de
   `audit.record()` acepta `tenant_id=None` — a confirmar que el resto
   del sistema de audit (filtros por tenant, `audit_tenant_ids`) no
   asuma que siempre hay uno.
2. **Nombre de la categoría "otro":** por ahora sólo rotación/conexión/
   verificación/otro — se puede ampliar la lista sin migración si el uso
   real pide más categorías (es un `String`, no un enum de Postgres).
