# sia-read-only

- **Estado:** hecha (implementada el 2026-10-06; probada contra Globex y cerrada el 2026-10-07)
- **Roadmap:** Fase 5, ítem "SIA" (`docs/PLAN.md` §7, `docs/plan/fase-5.md`).
  Entra en la fase por decisión de Alex (2026-10-06).
- **Fecha:** 2026-10-06

## Problema

SIA (Secure Infrastructure Access) es el servicio de acceso just-in-time a
VMs, bases de datos y Kubernetes de Privilege Cloud. Hoy PAM Web Console solo muestra
sus **conectores** (Connector Management). Para ver quién puede entrar a qué
máquina o base, qué sesiones hay abiertas, qué targets y cuentas fuertes
tiene registradas o cómo está configurado el servicio, el equipo PAM tiene que
entrar al portal de cada cliente, uno por uno.

## Alcance

Entra (corte 1, **solo lectura**, decisión de Alex):
- **Políticas de acceso** de VM y de bases de datos: lista y detalle
  (quién, a qué targets, con qué condiciones y en qué horario).
- **Sesiones** de SIA: activas (y recientes) por tenant, con su detalle.
- **Targets**: bases de datos registradas en SIA y *target sets* de VMs.
- **Secretos** (cuentas fuertes de DB y de VM): **solo metadatos** (nombre,
  tipo, usuario, estado). PAM Web Console nunca pide ni muestra el valor del secreto.
- **Configuración del servicio** (settings de SIA del tenant): lectura.
- Espacio de navegación propio **SIA**, igual que Alero.

Queda afuera:
- **Escritura** (corte 2, spec aparte): crear/editar/activar políticas,
  terminar sesiones, alta de targets y secretos, cambiar settings. Va con
  `approver+`, dry-run y confirmación reforzada, como el resto de la Fase 3.
- **Kubernetes** (políticas y workspaces de K8s): sin cliente que lo use hoy.
- **SSO/tokens de corta duración, SSH CA y certificados** de SIA: es
  autoservicio del usuario final, no operación del MSP.
- **Reproducción de grabaciones de SIA**: las de PSM ya están; las de SIA se
  evalúan en el corte 2 si Session Monitoring las expone.
- **SCA** (Secure Cloud Access): otro producto.

## Fuentes de la API

No hay capturas de DevTools de SIA en las specs (`docs/specs/done/` solo cubre
`/api/connectors`). Las rutas salen del **SDK oficial de CyberArk**,
`cyberark/ark-sdk-python` (rama `main`, revisado el 2026-10-06):

| Qué | Servicio (host) | Ruta |
|---|---|---|
| Políticas VM (legado) | `{sub}.dpa.cyberark.cloud` | `GET api/access-policies`, `GET api/access-policies/{id}` |
| Políticas DB (legado) | `{sub}.dpa.cyberark.cloud` | `GET api/adb/access-policies`, `GET api/adb/access-policies/{id}` |
| Políticas unificadas (UAP) | `{sub}.uap.cyberark.cloud` | `GET api/policies`, `GET api/policies/{id}` |
| Sesiones | `{sub}.sessionmonitoring.cyberark.cloud` | `GET api/sessions` (`search`, `offset`), `GET api/sessions/{id}`, `GET api/sessions/{id}/activities` |
| Targets de DB | `{sub}.dpa.cyberark.cloud` | `GET api/adb/resources`, `GET api/adb/resources/{id}` |
| Target sets de VM | `{sub}.dpa.cyberark.cloud` | `GET api/targetsets` |
| Secretos de DB | `{sub}.dpa.cyberark.cloud` | `GET api/adb/secretsmgmt/secrets` |
| Secretos de VM | `{sub}.dpa.cyberark.cloud` | `GET api/secrets` |
| Settings | `{sub}.dpa.cyberark.cloud` | `GET api/settings` |

`{sub}` es el subdominio del tenant, el mismo que ya está en
`sia_connector_url` (`https://globex.dpa.cyberark.cloud`). El SDK arma la
URL de cada servicio como `https://{sub}.{servicio}.cyberark.cloud`.

## Requisitos

1. **Políticas de acceso.** `GET /tenants/{id}/sia/policies` devuelve las
   políticas de VM y de DB en una sola lista, con tipo (VM/DB), nombre,
   estado (activa/suspendida/vencida), vigencia, cantidad de reglas y de
   usuarios/roles, y fecha de modificación. `GET .../sia/policies/{policy_id}`
   devuelve el detalle: reglas, principales (usuarios, roles, grupos),
   targets o filtros de targets, condiciones (días/horarios, duración máxima
   de sesión, MFA) y conexiones permitidas (SSH/RDP, usuario efímero, etc.).
   - Dado Globex con 3 políticas de VM, cuando un `viewer` abre
     SIA → Políticas, entonces ve 3 filas y al abrir una ve sus reglas.
   - Si el tenant ya migró a políticas unificadas (UAP), se leen de ahí; si
     UAP devuelve `404`/sin datos, se usan las rutas legado de `dpa`. El
     origen se muestra en el detalle (dato útil para el equipo).
2. **Sesiones.** `GET /tenants/{id}/sia/sessions?status=active|recent`
   devuelve las sesiones de SIA: usuario, target, protocolo, cuenta usada,
   inicio, duración y estado. `recent` = últimas 24 h. El detalle
   (`.../sia/sessions/{session_id}`) incluye las actividades (comandos/
   eventos) si Session Monitoring las devuelve.
   - Dado un usuario conectado por SSH a una VM vía SIA, cuando el operador
     abre SIA → Sesiones, entonces la ve como activa con su duración.
   - Session Monitoring es compartido entre servicios; se filtra solo lo que
     es de SIA (campo/`search` exacto a confirmar, ver "Pendiente").
> **Cambio (2026-10-06, a pedido de Alex al comparar con el portal):** los
> requisitos 3 y 4 se fusionaron en una sola página **Strong accounts**
> (`GET /tenants/{id}/sia/strong-accounts`, acción `sia.strong_accounts.list`),
> como Setup > Deployment > Strong accounts del portal: pestañas Máquinas
> virtuales (cada cuenta con sus target sets) y Bases de datos (cada cuenta
> con sus bases). Los campos de las strong accounts de VM (dominio, usuarios
> efímeros de dominio, controlador de dominio, WinRM) salen de
> `cyberark/idsec-sdk-golang` (`IdsecSIAVMSecret`). El detalle verifica en vivo
> que el rol `DPA Privilege Cloud Secret Access` sea miembro del safe.

3. **Targets.** `GET /tenants/{id}/sia/targets` devuelve dos listas: bases de
   datos (nombre, motor, host/puerto, red, secreto asociado) y target sets de
   VM (nombre, tipo — dominio/sufijo/IP, secreto asociado).
4. **Secretos, solo metadatos.** `GET /tenants/{id}/sia/secrets` devuelve las
   cuentas fuertes de DB y de VM: nombre, tipo, usuario, dónde se guarda
   (bóveda de PCloud o SIA), habilitado/deshabilitado, fecha. **El backend
   descarta cualquier campo que traiga el secreto** antes de responder y
   antes de auditar (lista blanca de campos, no lista negra).
   - Dado un secreto de DB con `secret_data` en la respuesta de CyberArk,
     cuando PAM Web Console la procesa, entonces ese campo no llega al frontend, ni al
     audit log, ni a la caché.
5. **Configuración del servicio.** `GET /tenants/{id}/sia/settings` devuelve
   los settings del tenant agrupados por función (MFA caching, grabación,
   SSH, RDP, certificados, timeouts, etc.), con etiquetas legibles.
6. **Errores claros.** Tenant sin `sia_connector_url` → `409` "SIA no está
   configurado para este tenant". `403` de CyberArk → "SIA no está licenciado
   o el service account no tiene el rol SIA Admin" (mismo texto que hoy en
   `list_sia_connectors`). Un servicio caído (p. ej. Session Monitoring) no
   rompe las otras pestañas.
7. **RBAC y auditoría.** `viewer+` en el tenant para todo. Cada lectura se
   audita (`sia.policies.list`, `sia.policy.get`, `sia.sessions.list`,
   `sia.session.get`, `sia.targets.list`, `sia.secrets.list`,
   `sia.settings.get`) con el conteo devuelto, y las acciones se registran en
   `audit_catalog.py` con su etiqueta en español.
8. **Navegación.** Espacio **SIA** en `AppShell` con las páginas Políticas,
   Sesiones, Targets, Secretos y Configuración, visible para `viewer+`.
   Rutas `/sia/policies`, `/sia/sessions`, `/sia/targets`, `/sia/secrets`,
   `/sia/settings`, con `TenantScope` y `DataTable`.

## Diseño técnico

- **URLs por servicio.** Sin columnas nuevas en `tenants`: un helper
  `isp_service_url(tenant, "uap" | "sessionmonitoring" | "dpa")` deriva el host
  a partir del subdominio de `sia_connector_url`. Si algún tenant resulta
  tener un host distinto, se agrega un override en el seed (no hace falta
  hoy).
- **Cliente** (`app/cyberark/client.py`): métodos de lectura nuevos
  (`list_sia_vm_policies`, `list_sia_db_policies`, `list_uap_policies`,
  `get_sia_policy`, `list_sm_sessions`, `get_sm_session`,
  `list_sm_session_activities`, `list_sia_db_resources`,
  `list_sia_target_sets`, `list_sia_db_secrets`, `list_sia_vm_secrets`,
  `get_sia_settings`), con la misma traducción de errores
  (`app.cyberark.errors`) y paginación por `offset`/`nextToken` donde aplique.
  Mismo token ISPSS del tenant: no hace falta otro login.
- **Dominio** (`app/domain/sia.py`, nuevo): funciones puras que normalizan
  las respuestas (VM y DB, legado y UAP) a una forma común para la grilla y el
  detalle, la **lista blanca** de campos de secretos y las etiquetas de los
  settings.
- **Router** (`app/api/sia.py`, nuevo, prefijo `/tenants/{tenant_id}/sia`):
  solo `GET`, `_viewer`, `tenant_client` + `audit.record`, mismo patrón que
  `app/api/setup.py`. Caché corta de 30 s (sesiones activas: 15 s), nunca con
  secretos.
- **Frontend:** `pages/SiaPolicies.tsx` (+ panel de detalle),
  `SiaSessions.tsx`, `SiaTargets.tsx` (pestañas DB / VM), `SiaSecrets.tsx`,
  `SiaSettings.tsx`; tipos en `src/lib/`. Convenciones de accesibilidad del
  repo (`aria-label` en botones de ícono, tokens `*-ink` para el estado).
- **Permisos del service account:** necesita el rol **SIA Admin** (o el
  equivalente de solo lectura si existe) en Identity. Se documenta en el
  runbook del service account; sin él, el requisito 6 cubre el `403`.
- **Pruebas:** primero contra el tenant de lab (`lab`), después contra
  Globex (tiene conectores SIA en `Globex_GCP`).

## Pendiente de confirmar (contra el lab, antes de cerrar el diseño)

- Si los tenants usan **políticas UAP o legado** (o ambas), y la forma real de
  cada respuesta.
- El **filtro de Session Monitoring** para quedarse solo con SIA y separar
  activas de terminadas (sintaxis de `search`).
- Si el service account actual ya tiene acceso (rol SIA Admin) en cada tenant.
- Que el host de Session Monitoring sea `{sub}.sessionmonitoring.cyberark.cloud`.

Una captura de DevTools del portal de SIA del lab (Políticas, Sesiones,
Settings) cierra casi todo esto de una vez.

## Notas de la implementación (2026-10-06)

- **Sesiones:** sin filtro por `applicationCode` en el `search` (sintaxis sin
  confirmar); se piden activas (`sessionStatus IN Active`) o de las últimas
  24 h (`startTime GE ...`) y el backend se queda con las de código `DPA`/
  `SIA` (una sesión sin código se muestra). Tope de 500 por consulta.
- **Políticas:** UAP se pide con `filter=(targetCategory eq 'VM'|'DB')`; si
  UAP responde error o vacío, se usan las rutas legado. El detalle incluye la
  respuesta original de CyberArk (sin claves sensibles) para confirmar la
  forma real en la primera prueba.
- **Strong accounts (antes Targets + Secretos):** sin caché en Redis (ni
  siquiera los metadatos). De `secret.secret_data` de una strong account
  `PCloudAccount` se leen sólo `safe`, `account_name` y `username`.
- **RBAC:** el espacio SIA es visible para todos los operadores; los
  endpoints exigen `viewer+` en el tenant.

## Tareas

- [x] Aprobar la spec (Alex).
- [x] Confirmar los puntos pendientes contra el lab (captura de DevTools o
      `curl` desde el server). Confirmado contra Globex, ver "Resultado de
      la prueba" abajo.
- [x] Cliente + helper de URLs por servicio + tests.
- [x] `app/domain/sia.py` (normalización, lista blanca de secretos, etiquetas
      de settings) + tests, incluido el test de que un secreto nunca sale.
- [x] `app/api/sia.py` + acciones en `audit_catalog.py` + tests (RBAC, `409`,
      `403`, auditoría, caché sin secretos).
- [x] Espacio "SIA" en `AppShell`, rutas en `App.tsx`, las 5 páginas y tipos.
- [x] ruff, ruff format, mypy, pytest; lint, tsc, build.
- [x] Probar contra el lab y contra Globex. Globex, 2026-10-07.
- [x] Docs: `ARCHITECTURE.md`, `PLAN.md` §7, `docs/plan/fase-5.md`, runbook del
      service account (rol SIA Admin). Mover la spec a `done/`.

## Resultado de la prueba (Globex, 2026-10-07)

- **Hosts:** `{sub}.dpa`, `{sub}.uap` y `{sub}.sessionmonitoring` responden
  con el token del service account (ya tiene SIA Admin).
- **Políticas:** Globex usa UAP. Si UAP responde, un 403/404 de la ruta
  legado significa "sin políticas de ese tipo" (antes se mostraba como "SIA
  no licenciado" para DB).
- **Sesiones:** el filtro `DPA`/`SIA` deja las mismas que Session
  Monitoring del portal (incluidas las RDP por JIT). `startTime`/`endTime`
  vienen en UTC **sin `Z`**: ahora se marcan como UTC (salían 3 h
  corridas).
- **Strong accounts:** la lista de `api/secrets` no trae a qué cuenta de
  Privilege Cloud apunta una `PCloudAccount`; se resuelve con
  `GET api/secrets/actions/retrieve_pcloud_account_by_admin?secret_id=…`
  (`secrets_results[0].pcloud_account = {safe, account_name, account_id,
  username}`), la misma llamada del portal. Rotable/Creada/Modificada no
  vienen: se ocultan si están vacíos. El rol del safe se llama "DPA RDP
  Privilege Cloud Secrets Access" (variante por protocolo); se acepta
  cualquier "DPA … Privilege Cloud Secret(s) Access".
- **Configuración:** seis grupos no estaban en el SDK y quedaron con
  etiqueta en español.
