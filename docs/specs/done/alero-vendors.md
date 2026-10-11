# alero-vendors — Espacio "Alero": Vendors + Usuarios + Roles + Activities de Remote Access, sólo lectura

- **Estado:** hecho y probado de punta a punta contra `pam-primary` real
  (2026-09-21): migración `0013` aplicada, build de `api`/`web` limpio,
  credenciales del service account de Globex cargadas en el secret
  store, y la llamada real a `api.alero.io` funciona — auth Private Key
  JWT exitosa, **218 vendors** + **36 usuarios** traídos del tenant real
  (coincide con lo que muestra el portal nativo de Alero). Bugs
  encontrados y corregidos contra datos reales: (1) el campo de email de
  un vendor es `emailAddress`, no `email` (quedaba vacío en la UI); se
  sumaron también `fullName`/`companyName`. (2) `idaptiveRoles` viene
  como **string** separado por comas cuando hay varios roles (nunca como
  lista) — el frontend esperaba `string[]` y `.join()` sobre un string
  tiraba `TypeError: t.join is not a function`, dejando la pantalla en
  blanco. Se normaliza en el backend (`_as_list`), no en el frontend,
  para que el contrato `string[] | null` se cumpla siempre sin importar
  qué forma mande la API de Alero.
  **Extensión (mismo día):** Alex pidió que se vea "como el portal nativo
  de Alero, y mejor" — mostró una captura de Identities > Users del
  portal, que resultó ser un recurso **distinto** de Vendors (personas
  del equipo propio con la app móvil, no terceros). Investigado contra el
  swagger completo (`/v3/api-docs`, no el público de `/swagger-ui`):
  `GET /v2-edge/users/` (con barra final). Sumado como
  `/tenants/{id}/alero-users` + página "Usuarios", y ambas pantallas
  (Vendors y Usuarios) se rediseñaron con avatares de iniciales
  (color determinístico por nombre, paleta `line-*` de marca) y badges de
  estado con color — `frontend/src/components/AleroBadges.tsx`,
  compartido entre las dos.
  **Segunda extensión (mismo día):** Alex notó que la pantalla "Identity
  roles" del portal de Alero no era la misma vista/función que Identity >
  Roles de PAM Web Console, aunque compartieran nombres de rol, y pasó la URL del
  portal (`.../users/vendor-roles?searchInOptionField=ALL&query=`).
  Investigado contra el swagger completo: el recurso real es
  `GET /v2-edge/groups` (los parámetros `searchString`/`searchIn`
  coinciden con los query params de esa URL) — se llaman "groups" en la
  API, de dos tipos: `IdaptiveRole` (roles importados de CyberArk
  Identity, lo que muestra esa pantalla del portal) y `ProvisionedByAlero`
  (grupos nativos de Alero, que esa pantalla no muestra). Confirmado
  contra Globex real: 96 `IdaptiveRole` + 89 `ProvisionedByAlero` = 185
  total — coincide exacto con el "96 roles" del portal una vez filtrado
  por tipo. Sumado como `/tenants/{id}/alero-roles` (filtra a
  `IdaptiveRole`) + página "Roles", con badges de cantidad de
  vendors/invitaciones por rol. Clic en la cantidad de vendors de un rol
  navega a Vendors filtrado por ese rol (mismo comportamiento que el
  portal), client-side contra los datos ya cargados.
  **Tercera extensión (mismo día):** detalle al hacer clic en un
  vendor/usuario, como el portal nativo. `/alero-vendors/{id}` y
  `/alero-users/{id}` pasan a usar los endpoints reales de detalle
  (`GET /v2-edge/vendors/{id}`, `/v2-edge/users/{id}`) en vez del
  list-scan por username de antes — mucho más rico (nombre/apellido,
  teléfono, fechas de acceso, quién invitó, roles resueltos por nombre).
  "Allowed Applications" (`siteId`/`applicationId` sin nombre en la
  respuesta cruda) se resuelve contra `GET /v2-edge/sites` +
  `/v2-edge/sites/{id}/applications` — confirmado contra Globex real
  que da el mismo nombre que el portal ("globex - Privilege Cloud
  portal"). Primera versión con un panel lateral (Sheet) compartido —
  Alex pidió explícitamente una **pantalla completa** como el portal
  nativo, mostraba muy poca información en un panel angosto. Reemplazado
  por rutas propias (`/alero-vendors/:id`, `/alero-users/:id`, con
  `?tenant=` para volver a la lista), mismo patrón que
  `KnowledgeBaseArticle.tsx` (Link "< Volver" + card con secciones en
  grilla de columnas). De paso: `invitedBy` de un usuario es un ID crudo
  en `UserResponse` (a diferencia de `VendorResponse`, que ya trae el
  nombre) — se resuelve contra `list_users()`; si no aparece (usuario
  borrado/inactivo, visto contra Globex real), se omite en vez de
  mostrar un hash sin sentido.
  **Cuarta extensión (mismo día):** Alex pidió traer *toda* la
  información que muestra el portal. Comparado campo a campo contra la
  respuesta cruda real (mismo vendor de la captura, John Smith) se
  sumaron 4 campos de `VendorResponse` que faltaban: `emailConfirmedAt`
  ("Email verified at" — coincide exacto), `phoneAndEmailAuth` ("Allowed
  to join without app"), `invitedVendorsInitialStatus` y
  `maxNumOfInvitedVendors` (config de auto-invitación de sub-vendors,
  sólo relevante si `canInvite`). Investigado también "Built-in role",
  "Last offline sync", "Vendor time zone", "Allowed working days/hours"
  del portal: **no vienen** en la respuesta real de `vendors`, `groups`
  ni `applications` (se probaron los tres) — no es un descuido, la API
  pública de Alero no los expone en esta llamada. Documentado
  explícitamente en el código para que quede claro que se investigó y no
  que se pasó por alto.
  **Quinta extensión (mismo día):** espacio **Activities** (feed de
  auditoría del portal: login, altas/bajas, cambios de config).
  `GET /v2-edge/activities` (confirmado en el swagger completo) — ya
  conocido del script de PowerShell para `ApplicationVendorLogin`
  puntualmente, acá se generaliza a las ~50 variantes del enum
  `activityType`. `activityTypes` es obligatorio en la API (no hay "todos"
  implícito) — se manda la enumeración completa por default para replicar
  el feed sin filtro del portal. `_ACTIVITY_LABELS` traduce cada tipo a un
  verbo legible; `OfflineAccessUserViewedPassword` (el evento más sensible
  del feed para un tenant PAM) muestra cuenta+safe como detalle. Probado
  contra Globex real (100 eventos en 24hs): `TenantLogin` y
  `ApplicationVendorLogin` coinciden exacto con la captura del portal.
  Página `AleroActivities.tsx` con selector de ventana (1h/24h/7d/30d),
  mismo patrón `DataTable` + toolbar que `Audit.tsx` (el audit log propio
  de PAM Web Console).
  **Fast-follow — último login real (2026-09-22):** el campo `lastLogin`
  del detalle de vendor venía sin confirmar (agregado especulativamente
  desde `VendorResponse` en la primera versión del detalle, a diferencia
  del resto de campos de esa respuesta, confirmados uno por uno en la
  "Cuarta extensión"). `AleroClient.get_vendor_last_login(username)`
  calcula el login real desde `activities` filtrado a
  `ApplicationVendorLogin` — mismo algoritmo que
  `Get-AleroVendorLastLogins` de `Alero-ApiClient.ps1`: trocea el rango de
  lookback (3 años, mismo default que `AleroLookbackYears` del `.ps1`) en
  ventanas de 30 días, de la más reciente a la más vieja, y corta apenas
  una ventana da algún match (una ventana más vieja nunca puede tener un
  login más reciente). Match por la parte local del username
  (`applicationLoginUsername` trae a veces sufijo de dominio, a veces no
  — mismo criterio que el script). Se llama sólo en el detalle de UN
  vendor (`GET /alero-vendors/{id}`), no en la lista completa — es la
  parte cara que el roadmap dejaba "para más adelante sin rediseñar
  nada"; enriquecer la lista completa (218 vendors) sigue fuera de
  alcance por costo. Si no se encuentra ningún login en la ventana de
  lookback, cae al `lastLogin` crudo de `VendorResponse` por si acaso.
  5 tests nuevos (`test_alero_client.py`) sobre la lógica de troceo/match/
  corte temprano — **pendiente probar contra un vendor real de Globex**
  con logins conocidos.
  **Fast-follow — enriquecer `license-candidates` (2026-09-22):** la
  pestaña "Privileged External User" de ese reporte (`app/reports/
  license_candidates.py`) usaba el `LastLogin` de Identity para TODOS los
  usuarios con licencia, EPVUser y ExtUser por igual — para los ExtUser
  (vendors de Remote Access) ese dato no es confiable, se autentican vía
  Alero, no contra el portal de Identity directamente (mismo motivo
  documentado en `License_Candidates_Report.ps1`). Se agrega
  `AleroClient.get_all_vendor_last_logins()` — mismo troceo de ventanas
  de 30 días que `get_vendor_last_login`, pero sin cortar apenas hay un
  match: arma el mapa completo `username -> último login` de TODOS los
  vendors de una sola pasada por el lookback (puerto directo de
  `Get-AleroVendorLastLogins` del `.ps1`, que hace exactamente esto).
  `app.reports.license_candidates.enrich_ext_users_with_alero()` (pura)
  reemplaza status/login/días-sin-loguearse/candidato de las filas
  ExtUser con los datos reales de Alero (status/vigencia de acceso del
  vendor + el login calculado) y suma columnas nuevas (vigencia de
  acceso, roles, grupos, comentario de cuenta huérfana) — mismo criterio
  de "candidato" que el `.ps1`: desactivada en Alero, fuera de la ventana
  de acceso otorgada, nunca logueó, o inactiva hace más del umbral. Las
  filas EPVUser quedan intactas. **`report.run()` no puede hacer esto
  directo** — sólo recibe un `CyberArkClient`, Alero es una API separada
  con credenciales propias — así que la orquestación
  (`enrich_with_alero()`, abre un `AleroClient`) la dispara
  `app.api.reports._compute()` después de correr el reporte normal, sólo
  si `Tenant.alero_tenant_id` está configurado; si Alero falla (secret
  store sin credenciales, tenant caído), el reporte se sigue devolviendo
  con el login de Identity como respaldo — mismo criterio de
  degradación best-effort que el `.ps1` original (`try/catch` alrededor
  del enriquecimiento). `as_list()` (normaliza `idaptiveRoles`/`groups`)
  se mueve de `app.api.alero` a `app.alero.client` para compartirlo entre
  el router y el reporte. 9 tests nuevos (8 en
  `test_license_candidates_alero.py` + 1 en `test_alero_client.py`)
  — **pendiente probar contra Globex real** (vendors con y sin
  registro en Identity, cuenta huérfana real si existe alguna).
- **Roadmap:** `docs/PLAN.md` § "Pendiente — espacio de nav dedicado a
  Alero" (Fase 5 / Extras).
- **Fecha:** 2026-09-21
- **Base:** `docs/specs/done/identity-users.md` (mismo patrón de espacio
  de nav separado, "sólo lo que sale de esa API").
- **Origen del ticket:** el cliente (Globex) pidió por ticket un detalle
  de licencias en uso de Identity y Alero. El reporte de Identity
  (`license-candidates`) ya existía; Alero no tenía nada.

## Problema

CyberArk Remote Access (Alero) es la API de acceso remoto de terceros/
proveedores (zero-trust, biométrico) — producto separado de Privilege
Cloud/Identity, con su propia API (`api.alero.io`) y su propio modelo de
auth (Private Key JWT, RFC 7523, contra un Service Account por tenant).
PAM Web Console no tenía nada de esto. Ya existía una implementación de referencia
probada contra un tenant real: `Telemetry-Reporter`/
`PowerShell-Scripts` (`Shared/Alero-ApiClient.ps1` +
`License_Candidates_Report.ps1`), que resolvió el modelo de auth a
mano (incluido parsear el PEM PKCS1 a DER, porque .NET Framework/PS 5.1 no
expone `RSA.ImportRSAPrivateKey`). En Python no hace falta ese parseo:
`cryptography` (dependencia de PyJWT para RS256) importa PKCS1 directo.

## Alcance

**Entra (primer corte):**
- Sección de nav nueva **"Alero"** — sólo contenido que sale de la API de
  Alero, nada del Vault/Identity (mismo criterio que la sección Identity).
- **Vendors, sólo lectura**: lista (`GET /tenants/{id}/alero-vendors`) y
  detalle por username (`GET /tenants/{id}/alero-vendors/{username}`) —
  usuario, email, estado, vigencia de acceso, roles y grupos.

**Fuera de este corte (fast-follow, no bloquea):**
- [x] **Último login real** (`activities` / `ApplicationVendorLogin`,
  cerrado 2026-09-22, ver "Fast-follow — último login real" arriba) — el
  script de PowerShell ya lo resuelve troceando el rango en ventanas
  mensuales (la API pagina hasta offset=10000 y corta), pero es costoso
  calcularlo para *todos* los vendors de una lista; se puede sumar por
  vendor en el
  detalle más adelante sin rediseñar nada.
- [x] **Enriquecer el reporte `license-candidates`** (pestaña Privileged
  External User, cerrado 2026-09-22, ver "Fast-follow — enriquecer
  license-candidates" abajo) con estos datos, como ya hace
  `License_Candidates_Report.ps1` — evaluado y descartado para el
  primer corte (decisión explícita: primero el espacio de vendors solo).
- Multi-tenant real: hoy sólo Globex tiene Remote Access configurado.

## Diseño técnico

**Auth (Private Key JWT / RFC 7523):**
1. `GET <discoveryURI>` (viene en el `.json` del Service Account) → trae
   `token_endpoint` e `issuer`.
2. Firmar un JWT RS256 con la clave privada del Service Account:
   `iss`/`sub` = `"<AleroTenantId>.<ServiceAccountID>.ExternalServiceAccount"`
   (NO el `serviceAccountId` solo — da `401 invalid_client`, confirmado en
   el script de PS), `aud` = el **issuer** del discovery (no el
   `token_endpoint` — mismo motivo), `exp` a 60s.
3. `POST <token_endpoint>` con `grant_type=client_credentials` +
   `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`
   + `client_assertion=<jwt>` → `access_token`.
4. `GET https://api.alero.io/v2-edge/vendors?offset=&limit=` (paginado,
   `limit` capeado en 100 aunque se pida más — visto empíricamente),
   `Authorization: Bearer <access_token>`.

**Credenciales — dónde viven:**
- `Tenant.alero_tenant_id` (nueva columna, migración `0013`) — **no**
  secreto, es el identificador público del tenant de Alero (va en
  claro en el JWT). Nulo = el cliente no tiene Remote Access, gatea el
  espacio de nav. Se carga vía `seed/tenants.json` + `manage.py
  seed-tenants`, igual que `identity_tenant_url`.
- `service_account_id` / `private_key` / `discovery_uri` — sí secretos,
  van en el secret store existente (`age`), como una clave `"alero"`
  anidada dentro de la entrada del tenant (`secrets.example.json` tiene
  la plantilla). Salen tal cual del `.json` que descarga el portal de
  Alero al crear el Service Account (Identities > Service accounts, auth
  type "Private Key Signing", rol mínimo **Viewer** — necesita listar
  vendors).

**Código:**
- `app/alero/client.py` — `AleroClient`: auth + `list_vendors()` +
  `get_vendor(username)` (sobre la lista completa — no hay endpoint de
  detalle por username confirmado en el swagger, y los tenants de Remote
  Access son chicos). Errores propios (`AleroError`/`AleroNotFound`), no
  reusa la jerarquía de `app.cyberark.errors` (API completamente distinta).
- `app/core/tenants.py`: `alero_client(tenant_id, session)` — mismo
  patrón que `tenant_client`, arma el `AleroClient` desde
  `Tenant.alero_tenant_id` + el secret store.
- `app/api/alero.py`: dos rutas, auditadas (`alero_vendor.list` /
  `alero_vendor.get`) con un `_run_alero` propio (mismo patrón que
  `app.api.inventory._run`, pero para `AleroError`).
- Frontend: `pages/AleroVendors.tsx` (mismo patrón que
  `IdentityRoles.tsx`), espacio de nav "Alero" en `App.tsx`.
- Dependencia nueva: `cryptography` (`requirements.txt`) — PyJWT ya
  estaba, pero RS256 necesita `cryptography` para leer la clave PEM.

## Tareas

- [x] Migración `0013`: `Tenant.alero_tenant_id`.
- [x] `app/alero/client.py` (`AleroClient`, auth + `list_vendors` +
      `get_vendor`).
- [x] `app/core/tenants.py`: `alero_client()`.
- [x] `app/api/alero.py`: rutas + registro en `main.py`.
- [x] `secrets.example.json`: plantilla de la sub-clave `alero`.
- [x] `seed/tenants.json` + `seed.py`: `alero_tenant_id` de Globex
      (`00000000000000000000000000000001`, sacado de
      `PowerShell-Scripts/Configs/globex.json`).
- [x] Frontend: `AleroVendors.tsx`, tipos, nav, ruta.
- [x] `requirements.txt`: `cryptography`.
- [x] Probado localmente: firma JWT RS256 con una clave PKCS1 generada al
      vuelo (`cryptography` + `PyJWT`), confirma que el formato de clave
      que entrega Alero es compatible sin conversión.
- [x] Desplegado en `pam-primary`: migración `0013` aplicada, `api`/`web`
      reconstruidos limpios con `cryptography`, `seed-tenants` corrido
      (`alero_tenant_id` de Globex cargado).
- [x] Gating de `alero_client()` probado contra la DB real (tenant sin
      `alero_tenant_id` → 400; con `alero_tenant_id` sin secreto → 503).
- [x] Credenciales del service account de Globex cargadas en el secret
      store del server (fusionadas en `secrets.age` sin pasar la clave
      privada en texto plano por ningún canal salvo el archivo cifrado).
- [x] Llamada real a Alero probada: auth Private Key JWT exitosa, 218
      vendors traídos del tenant real de Globex.
- [x] Bug de `emailAddress` (no `email`) encontrado y corregido contra
      datos reales.
- [ ] Pendiente menor (no bloquea): comparar la cantidad de vendors contra
      el Excel de `License_Candidates_Report.ps1` para Globex, la
      próxima vez que se corra ese script.
- [x] Fast-follow: `AleroClient.get_vendor_last_login()` (troceo por
      ventanas de 30 días, corte temprano, match por parte local del
      username) + wireado en `GET /alero-vendors/{id}`. 5 tests nuevos
      (`test_alero_client.py`). Backend (ruff+format+mypy+pytest) verde;
      sin cambios de frontend (el tipo/campo `lastLogin` ya existía).
- [ ] Pendiente probar el fast-follow de último login contra un vendor
      real de Globex con logins conocidos, para confirmar que el valor
      calculado coincide con lo que muestra el portal nativo.
- [x] Fast-follow: `AleroClient.get_all_vendor_last_logins()` (mismo
      troceo, sin corte temprano, mapa completo) + `app.reports.
      license_candidates.enrich_ext_users_with_alero()` (pura) +
      `enrich_with_alero()` (orquestación) + wireado en
      `app.api.reports._compute()` condicionado a
      `Tenant.alero_tenant_id`. `as_list()` movido a `app.alero.client`
      para compartirlo. 9 tests nuevos. Backend
      (ruff+format+mypy+pytest) verde; sin cambios de frontend (Reports.tsx
      ya renderiza columnas de forma genérica).
- [ ] Pendiente probar el fast-follow de license-candidates contra
      Globex real: vendors con y sin registro en Identity, y confirmar
      si existe alguna cuenta huérfana real para validar ese mensaje.
