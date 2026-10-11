# cross-tenant-search

- **Estado:** hecha (probada en el server 2026-09-09)
- **Roadmap:** Fase 1, ítem "Búsqueda y filtros potentes; búsqueda **cross-tenant** ('¿dónde está esta cuenta?')"
- **Fecha:** 2026-09-09

## Problema

Hoy para buscar una cuenta hay que elegir un tenant y filtrar dentro de él.
El equipo PAM opera 4 clientes y una pregunta habitual es "¿en qué cliente /
safe está la cuenta `svc-sql-prod`?" o "¿este servidor `10.20.30.40` está
onboardeado en algún lado?". Con el PVWA eso son 4 logins y 4 búsquedas. PAM Web Console
puede resolverlo en una.

## Alcance

**Entra:**
- Búsqueda de **cuentas** por término libre en los tenants habilitados
  (Privilege Cloud), en paralelo, con resultados agrupados por tenant.
- Manejo por-tenant de errores (auth, permiso, timeout): un tenant que falla
  no rompe la búsqueda; se reporta aparte.
- Página nueva en el frontend con la barra de búsqueda y los resultados.

**Fuera (otra spec / fase):**
- Buscar safes, plataformas o usuarios cross-tenant (se agrega después con el
  mismo patrón).
- Scope por RBAC (que un operador solo busque en sus tenants) — depende de
  `role_assignments`, ítem aparte de Fase 1. Por ahora: rol `viewer` ve
  todos los tenants, igual que el resto del inventario.
- Caché de resultados / índice. Cada búsqueda pega en vivo a CyberArk.

## Requisitos

### R1 — Endpoint de búsqueda de cuentas cross-tenant

`GET /search/accounts?q=<término>`

- Requiere rol `viewer`.
- `q` obligatorio, mínimo 2 caracteres (400 si no).
- Consulta **en paralelo** (`asyncio.gather`) todos los tenants con
  `enabled = true` y `deployment = privilege_cloud`, llamando
  `client.list_accounts(search=q)`.
- Cada tenant abre su propio `tenant_client` (aislamiento: un cliente nuevo
  por tenant por request, como el resto del inventario).
- Cap de 100 cuentas por tenant en la respuesta (el `search` de CyberArk ya
  acota; el cap evita payloads gigantes con términos muy amplios).

**Escenario feliz:**
Dado que existe una cuenta `svc-sql` en el safe `SQL-PROD` de Globex y otra
`svc-sql-dr` en Acme,
Cuando el operador hace `GET /search/accounts?q=svc-sql`,
Entonces la respuesta es `200` con:
```json
{
  "query": "svc-sql",
  "results": [
    {"tenant": "globex", "tenant_name": "Globex", "count": 1,
     "accounts": [{"id": "...", "name": "svc-sql", "safeName": "SQL-PROD", ...}]},
    {"tenant": "acme", "tenant_name": "Acme", "count": 1, "accounts": [...]},
    {"tenant": "umbrella", "tenant_name": "Umbrella", "count": 0, "accounts": []},
    {"tenant": "lab", "tenant_name": "Laboratorio (lab)", "count": 0, "accounts": []}
  ],
  "errors": []
}
```

### R2 — Un tenant que falla no rompe la búsqueda

`asyncio.gather(..., return_exceptions=True)`. Si un tenant tira `AuthError`,
`Forbidden`, timeout o `CyberArkError`:
- No aparece en `results`.
- Aparece en `errors`: `{"tenant": "acme", "error": "<mensaje>"}`.
- El resto de los tenants responde normal.

**Escenario:**
Dado que el Service User de Acme perdió el permiso de listar cuentas,
Cuando el operador busca `q=admin`,
Entonces `results` trae Globex/Umbrella/el MSP y `errors` trae
`{"tenant": "acme", "error": "... 403 ..."}`, con status `200`.

### R3 — Todo se audita, por tenant

Por cada tenant consultado se llama `audit.record` con:
- `action = "account.search"`, `tenant_id = <tenant>`,
- `result = "ok"` con `detail = {"q": q, "count": N}`, o
- `result = "error"` con `detail = {"q": q, "error": "..."}`.

Atribuido al operador real (`op.username` / `op.role`).

### R4 — Página de búsqueda en el frontend

- Ruta `/search`, ítem "Buscar" en el menú del header.
- Input de texto + botón (o Enter). Debounce no necesario (búsqueda on
  submit, no on type — cada búsqueda es cara).
- Mientras carga: loader con texto "Buscando en N tenants…".
- Resultados: una sección por tenant **con al menos 1 resultado**, colapsable,
  mostrando `tenant_name` + count y una tabla compacta (nombre, usuario,
  address, safe, plataforma). Tenants con 0 resultados se listan al pie en
  gris ("Sin coincidencias: Umbrella, el MSP").
- `errors`: un `Alert` amarillo por tenant fallado.
- Cada fila permite saltar al inventario de ese tenant (link a
  `/` con el tenant preseleccionado — o al menos mostrar el tenant claramente).

**Escenario:**
Dado el resultado de R1,
Cuando el operador abre `/search` y busca `svc-sql`,
Entonces ve dos secciones (Globex 1, Acme 1) y al pie
"Sin coincidencias: Umbrella, Laboratorio (lab)".

## Diseño técnico

**Backend**
- Nuevo router `app/api/search.py`, prefijo `/search`, dep `require_role(viewer)`.
- `GET /search/accounts`:
  - Query de tenants elegibles: `select(Tenant).where(Tenant.enabled, Tenant.deployment == Deployment.privilege_cloud)`.
  - `async def _one(tenant)`: abre `tenant_client(tenant.id, session)`, llama
    `list_accounts(search=q)`, audita ok, devuelve `(tenant, rows[:100])`.
    Captura excepciones y las devuelve como error estructurado + audita error.
  - `results = await asyncio.gather(*[_one(t) for t in tenants], return_exceptions=False)`
    (las excepciones se capturan dentro de `_one`, no se propagan).
  - **Ojo sesión SQLAlchemy:** `AsyncSession` no es segura para uso
    concurrente. Cada `_one` hace `audit.record` sobre la misma `session`.
    Opciones: (a) serializar los `audit.record` después del gather con los
    resultados juntos, o (b) que cada `_one` use su propia `SessionLocal()`.
    → Elegir (a): el gather solo hace las llamadas HTTP a CyberArk en
    paralelo; los `audit.record` se hacen en un loop secuencial después, y un
    solo `session.commit()` al final. Mantiene la cadena de hash consistente.
  - `tenant_client` necesita la `session` para cargar el `Tenant` y no hace
    escrituras — usarla concurrentemente solo para `session.get(Tenant, id)`
    es lecturas; aun así, para evitar cualquier problema, precargar los
    `Tenant` antes del gather y pasar un helper que no toque `session`.
    → Refinar en implementación: quizás `tenant_client` acepte un `Tenant` ya
    cargado, o crear un `tenant_client_for(tenant, secret)` sin `session`.
- Registrar el router en `app/main.py`.

**Frontend**
- `src/pages/Search.tsx`.
- Tipos en `src/lib/types.ts`: `AccountSearchResponse`, `TenantAccountsGroup`,
  `SearchError`.
- Reutiliza `Account` y, para las tablas, el `DataTable` (o una tabla simple
  si el `DataTable` es demasiado para grupos chicos).
- Ruta + ítem de menú en `src/App.tsx`.

**Principios**
- Read-only: es solo `GET` + lecturas contra CyberArk. Nada de escritura.
- Aislamiento por tenant: un `CyberArkClient` nuevo por tenant, cerrado al
  terminar. No se comparten tokens.
- Auditoría: R3.

## Tareas

- [x] Backend: `_search_accounts_one` replica `tenant_client` sin `session`
      (get_secret + build_client + authenticate + close dentro de la tarea).
- [x] Backend: `app/api/search.py` con `GET /search/accounts` (R1, R2).
- [x] Backend: auditoría por tenant, commit único al final (R3).
- [x] Backend: registrar router en `main.py`.
- [x] Backend: `py_compile`.
- [x] Frontend: tipos en `types.ts`.
- [x] Frontend: `pages/Search.tsx` (R4) — accordion por tenant, "Ver en
      inventario" via `/?tenant=<id>` (Inventory ahora lee el query param).
- [x] Frontend: ruta `/search` + ítem de menú.
- [x] `CLAUDE.md` + `docs/PLAN.md` actualizados.
- [ ] Probar en el server contra los 4 tenants, mover esta spec a
      `docs/specs/done/`.

## Notas de implementación

- No se tocó `tenant_client`: `search.py` arma su propio cliente por tenant.
- El `gather` solo paraleliza las llamadas HTTP; los `audit.record` van
  después en loop secuencial sobre la única `AsyncSession`, con un commit.
- `except Exception` de red en `_search_accounts_one` como red de seguridad
  para cumplir R2 (un tenant caído no rompe la búsqueda).
- Bonus: `Inventory.tsx` ahora sincroniza el tenant con `?tenant=` en la URL.
