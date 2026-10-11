# PAM Web Console — Plan de proyecto

> Consola de operación PAM multi-tenant sobre CyberArk Privilege Cloud,
> pensada para el equipo PAM de un MSP (proveedor de servicios gestionados)
> que administra los tenants de varios clientes. Este documento conserva
> el razonamiento de diseño original: alcance, riesgos, arquitectura,
> modelo de identidad y auditoría, y el roadmap por fases.

> **Alcance v1: solo Privilege Cloud / ISPSS.** El cliente de API soporta
> también PAM Self-Hosted (la API Gen2 de PVWA es la misma; cambia la
> autenticación), pero el registro de tenants solo admite
> `privilege_cloud`.

---

## 1. Resumen ejecutivo

Construir **PAM Web Console (PAM Web Console)**, una webapp de
operación PAM para un MSP que permita, desde una sola
interfaz, **seleccionar un tenant de CyberArk Privilege Cloud / ISPSS** y
**ver, crear, modificar y eliminar** cuentas, safes, plataformas, usuarios,
permisos y sesiones a través de la API REST — un "panel único" para todos
los clientes, con los flujos propios de un MSP (onboarding masivo,
operaciones cross-cliente, reporting) que la consola nativa de CyberArk
(PVWA, una por tenant) no cubre. (Self-Hosted: ver banner de alcance.)

**El punto crítico del proyecto no es técnico, es de riesgo.** ITR (el
proyecto actual) es de solo lectura: lo peor que puede pasar es que un
reporte salga incompleto. Esta herramienta puede **borrar la cuenta
privilegiada de un cliente en producción**. Todo el diseño gira alrededor
de contener ese radio de impacto: mínimo privilegio, auditoría propia
inmutable, aprobaciones para lo destructivo, `dry-run` por defecto y un
**tenant de laboratorio de Privilege Cloud** (non-prod / partner) obligatorio
antes de tocar producción.

**Qué es, en una frase:** el **PVWA del equipo PAM, pero mejor** —
mismo alcance de gestión (cuentas, safes, plataformas, usuarios, sesiones),
con la telemetría que hoy da ITR integrada como dashboards vivos, mejor
UI/UX para las tareas repetitivas (filtrar, editar en lote, buscar), y
multi-tenant (un panel para los 4 clientes, no un login por cada uno).

**Es una webapp, no un generador de reportes.** ITR produce un archivo HTML;
PAM Web Console es una aplicación donde se navega y se opera. La lógica de análisis de
ITR (`analysis.py`) se reutiliza para *calcular* métricas, pero se renderizan
como tablas y gráficos interactivos en la app, no como un HTML estático.
(Sigue existiendo un "exportar" para entregarle un documento al cliente —
ver §8 — pero es una función, no el producto.)

**Dónde poner el esfuerzo:** lo que hace ganar a PAM Web Console sobre PVWA no es
clonar pantalla por pantalla, es la **UX de los flujos que importan** (traer
las cuentas de una plataforma y editarlas en lote, búsqueda cross-tenant,
onboarding masivo) y el **plano multi-tenant + auditoría propia**. Los flujos
que PVWA/PSM ya hacen bien y son sensibles —recuperar un secreto, abrir una
sesión— se dejan en PVWA, no se reimplementan.

**Primera entrega (Fase 1):** la consola de solo lectura — navegar, filtrar
y buscar todo el inventario de los 4 tenants en vivo, con los dashboards de
ITR embebidos. La edición ("editarlas ahí") llega en la Fase 2, detrás de
controles.

---

## 2. Visión y alcance

### 2.1 Qué es

- **Un panel único multi-tenant.** Selector de cliente/entorno; el resto de
  la app opera contra ese tenant. **v1: solo tenants de Privilege Cloud /
  ISPSS.** El cliente de API y el modelo de datos contemplan Self-Hosted (la
  API Gen2 de PVWA es la misma; cambia la autenticación), pero el registro
  de tenants no lo habilita hasta que haya lab y conectividad.
- **Inventario vivo, navegable:** Accounts, Safes, Platforms, Users/Groups,
  Sesiones, Componentes, Aplicaciones (AAM), Requests/aprobaciones. Con
  filtros potentes, orden, búsqueda y selección múltiple.
- **Flujo canónico** (el ejemplo que dio Alex): filtrar las cuentas de una
  plataforma → verlas en una grilla cómoda → editarlas de a una o **en
  lote**, con mejor UX que PVWA. (La edición es Fase 2.)
- **Operación:** CRUD sobre esos recursos + acciones (change/verify/reconcile
  de contraseñas, activar plataformas, gestionar miembros de safe, terminar
  sesiones, etc.).
- **Workflows MSP:**
  - Onboarding masivo de cuentas por CSV / descubrimiento.
  - Plantillas de configuración por tipo de cliente (safes estándar,
    naming, políticas).
  - Operaciones cross-cliente (ej. "buscar esta cuenta de servicio en los
    4 tenants").
  - Campañas de rotación / remediación con seguimiento.
- **Dashboards de telemetría (lo de ITR, embebido):** salud de componentes,
  compliance, cuentas sin gestión, sesiones concurrentes, etc. — como
  gráficos y tablas interactivas, no como HTML.
- **Auditoría y gobernanza:** log inmutable propio de toda acción, atribuido
  al operador real; workflow de aprobación para operaciones destructivas.

### 2.2 Fuera de alcance (v1, y probablemente siempre)

- Reemplazar PVWA para el **usuario final del cliente** que recupera
  secretos / abre sesiones PSM. Eso sigue en PVWA/PSM. PAM Web Console es la consola de
  **administración** del equipo PAM, no el portal de acceso del
  cliente.
- Administración del propio Vault a bajo nivel (DBParm, PARagent, ENE,
  replicación, DR) — no expuesto por API.
- Instalar/actualizar componentes (CPM/PSM) más allá de lo que exponen
  Components Monitoring / Connector Management.
- Ser el sistema de identidad. PAM Web Console tiene su propio store de operadores
  (local + TOTP); no hay SSO externo.

### 2.3 Por qué no alcanza con PVWA + la API a mano

| Necesidad | PVWA nativo | Script suelto | PAM Web Console |
|---|---|---|---|
| Ver los 4 tenants a la vez | ❌ (uno por login) | parcial | ✅ |
| Filtrar cuentas de una plataforma y **editarlas en lote** | ❌ (una por una) | frágil | ✅ |
| Buscar una cuenta en los 4 clientes | ❌ | script | ✅ |
| Onboarding masivo con plantilla de cliente | manual | frágil | ✅ |
| Historial de "qué cambió el MSP y quién" | ❌ (mezclado con el del cliente) | ❌ | ✅ |
| Telemetría / HealthCheck / compliance en vivo | ❌ | ITR (HTML) | ✅ (dashboards) |

---

## 3. Riesgo y principios de diseño

### 3.1 Radio de impacto

La herramienta, con credenciales de escritura, puede:

- Eliminar cuentas y safes (pérdida de secretos, corte de accesos).
- Cambiar/rotar contraseñas (invalidar credenciales en uso → incidentes).
- Modificar plataformas y políticas (afecta a todas las cuentas asociadas).
- Cambiar miembros de safe (dar o quitar acceso privilegiado).
- Terminar sesiones activas.

Un bug, un CSV mal armado o una cuenta comprometida de la webapp puede
causar un incidente **en varios clientes a la vez**.

### 3.2 Principios (no negociables)

1. **Read-only por defecto.** Cada rol, cada pantalla, cada endpoint
   arranca sin permiso de escritura. La escritura se habilita explícito.
2. **Todo se audita, en un log propio e inmutable.** Como las acciones se
   ejecutan con el service account del tenant, CyberArk solo ve al service
   account — PAM Web Console debe registrar el operador real, el tenant, el before/after
   y el resultado, en almacenamiento append-only. Sin esto, el proyecto no
   se despliega.
3. **Aprobación para lo destructivo.** Delete, bulk, cambios de plataforma,
   quitar miembros de safe → requieren un segundo operador (rol Approver).
   Configurable por tenant y por tipo de operación.
4. **`dry-run` primero.** Toda operación de escritura (sobre todo las
   masivas) muestra el plan de cambios y pide confirmación antes de
   ejecutar. Las masivas corren como job con checkpoint y posibilidad de
   abortar.
5. **Tenant de laboratorio obligatorio.** Ninguna feature de escritura se
   marca "lista" sin haberse probado contra un **tenant de Privilege Cloud
   non-prod / partner**. (el MSP no tiene lab Self-Hosted hoy; por eso
   Self-Hosted está parkeado.)
6. **Mínimo privilegio del service account.** El service account de cada
   tenant tiene solo los permisos de las fases habilitadas para ese
   cliente. Un cliente puede estar en "solo lectura" aunque PAM Web Console ya soporte
   escritura.
7. **Aislamiento entre tenants.** Credenciales, tokens en memoria, caché de
   inventario y RBAC están segmentados por tenant. Un operador con acceso al
   tenant A no ve ni toca el B.
8. **Reversibilidad y respaldo.** Antes de un cambio destructivo, PAM Web Console
   snapshotea el objeto (export de plataforma, dump de miembros de safe,
   metadata de cuenta) para poder reconstruir.

### 3.3 Modelo de amenaza (resumen)

| Amenaza | Mitigación |
|---|---|
| Cuenta de operador comprometida | SSO + MFA obligatorio, sesiones cortas, IP allowlist, RBAC estricto, aprobación para destructivo, alertas de acción anómala |
| Compromiso del backend / del host | Service accounts en CCP/Vault (nunca en disco ni en el repo), auth del CCP por cert de cliente + allowlist, secretos en memoria con TTL, red segmentada, host aislado y hardenizado, EDR |
| Bug en PAM Web Console que hace un cambio masivo indebido | `dry-run`, límites de tasa por tenant, "circuit breaker" (si una operación toca > N objetos, frena y pide confirmación reforzada), jobs abortables |
| CSV / input malicioso o erróneo | Validación estricta, `dry-run` con diff, límite de filas, esquema versionado |
| Repudio ("yo no fui") | Log inmutable con firma, correlación con el audit de CyberArk por timestamp |
| API de CyberArk cambia y rompe una operación | Cliente unificado con tests de contrato contra el lab, pinneo de versión, canary por tenant |

---

## 4. Arquitectura

### 4.1 Componentes

```
                    ┌───────────────────────────────────────────┐
                    │  Navegador (operador el MSP)            │
                    │  SPA React + TypeScript                    │
                    └───────────────┬───────────────────────────┘
                                    │ HTTPS (JSON) + login (local / LDAP-AD)
                    ┌───────────────▼───────────────────────────┐
                    │  API PAM Web Console  (FastAPI / Python)              │
                    │  ┌─────────────────────────────────────┐  │
                    │  │ Auth & RBAC   (local+TOTP, roles×tn) │  │
                    │  │ Dominio       (Accounts, Safes, ...) │  │
                    │  │ Cliente CyberArk unificado           │  │───► Privilege Cloud
                    │  │   (v1: Privilege Cloud / OAuth2;     │  │     de cada tenant
                    │  │    retry, paginación, rate-limit,    │  │     (por internet)
                    │  │    contrato de API)                  │  │
                    │  │ Auditoría     (append-only)          │  │
                    │  │ Jobs          (encolar / seguir)     │  │
                    │  └─────────────────────────────────────┘  │
                    └───┬─────────────┬──────────────┬──────────┘
                        │             │              │
              ┌─────────▼───┐  ┌──────▼──────┐  ┌────▼─────────────┐
              │ PostgreSQL  │  │ Redis       │  │ CyberArk CCP     │
              │ tenants,    │  │ cola de     │  │ (Credential      │
              │ RBAC, audit,│  │ jobs, cache │  │  Provider) —     │
              │ inventario, │  │ de tokens   │  │ service accounts │
              │ jobs        │  │ efímero     │  │ por tenant       │
              └─────────────┘  └─────────────┘  └──────────────────┘
                        (todo on-prem en el MSP; sin nube)
                        │
              ┌─────────▼──────────┐
              │ Worker(s) de jobs  │  (mismo código que la API, rol worker)
              │ bulk, sync, rotac. │
              └────────────────────┘
```

### 4.2 Stack recomendado

**Es green-field.** La recomendación busca "completo y profesional" con el
menor riesgo de ejecución para el equipo actual (Python-fluent por ITR) y
el menor costo de mantenimiento a 3 años.

| Capa | Elección | Por qué |
|---|---|---|
| **Backend** | **Python 3.12 + FastAPI** | async (ideal para orquestar cientos de llamadas a la API de CyberArk), Pydantic v2 para validación de entrada/salida, OpenAPI automático, ecosistema enorme. El equipo ya domina Python y la lógica de CyberArk (auth, retry, paginación) está probada en ITR y se porta directo. |
| **Cliente CyberArk** | Módulo propio, evolución de `cyberark_api.py` de ITR | No hay SDK oficial de Python usable. Ya tenemos las estrategias de auth (ISPSS / Self-Hosted), retry 429/5xx, paginación y manejo de errores. Se le agrega la mitad de escritura + tests de contrato. |
| **Frontend** | **React 18 + TypeScript + Vite** | estándar de industria, tipado fuerte (clave para no romper llamadas), gran pool de talento. |
| **UI kit** | **Tailwind v4 + shadcn/ui** (primitivas Radix) | control total del diseño, componentes que uno posee, alineado con el design system del MSP. (Se arrancó con Mantine y se migró en Fase 1, ver `docs/specs/done/migracion-tailwind.md`.) |
| **Estado servidor** | **TanStack Query** | caché, revalidación, optimistic updates controlados, reintentos. |
| **Tablas** | **TanStack Table** | los grids de Accounts/Safes/Users son grandes: virtualización, filtros, orden, selección múltiple para bulk. |
| **DB** | **PostgreSQL 16** | registro de tenants, RBAC, **audit log**, historial de jobs, caché de inventario, snapshots de reversión. |
| **ORM / migraciones** | **SQLAlchemy 2.0 + Alembic** | maduro, migraciones versionadas (necesario para un sistema que va a evolucionar años). |
| **Jobs async** | **ARQ** (o Celery si se necesita más) + **Redis** | onboarding masivo, sync de inventario, campañas de rotación. Redis además cachea tokens efímeros. |
| **Secretos** | **CyberArk Central Credential Provider (CCP)** — decidido: **sin nube** | Las credenciales de service account de cada tenant viven en un Vault de CyberArk (el lab Self-Hosted del MSP, o un Safe dedicado). PAM Web Console las pide en runtime vía la API REST de CCP; el CCP autentica a PAM Web Console por app-id + certificado de cliente + allowlist de IP/host, **sin ningún secreto en disco**. Dogfooding: el MSP usa su propio producto para custodiar las credenciales de su herramienta. Alternativa si no se quiere depender de CyberArk para esto: **OpenBao / HashiCorp Vault** self-hosted. Para Fase 0–1 (solo lectura) alcanza con `sops`+`age` (archivo cifrado) hasta montar el CCP. |
| **Auth de operadores** | **Fase 0–1:** store local en PAM Web Console (contraseñas Argon2 + **TOTP obligatorio**). **Después:** bind LDAP contra el **AD on-prem del MSP**, o un IdP self-hosted (Keycloak / Authelia) si se quiere OIDC de verdad. **Sin Entra ID / sin nube.** | Pocos operadores; cero dependencias externas para arrancar. El AD ya existe (los 3 Windows Server). |
| **Observabilidad** | OpenTelemetry → **Grafana + Loki + Tempo + Prometheus**, self-hosted (mismo Compose) | trazas de cada operación, correlación con el audit. |
| **Contenedores** | **Docker + Docker Compose**, on-prem (Ubuntu). Kubernetes solo si algún día crece mucho. | reproducible, simple de operar para una persona. |
| **CI/CD** | GitHub Actions | lint (ruff), type-check (mypy / pyright + tsc), tests, build de imágenes, deploy. |
| **Tests** | pytest + httpx (backend), Vitest + Testing Library (front), Playwright (E2E), **tests de contrato contra el tenant de lab** | |

**Alternativa considerada y descartada:** un stack full-TypeScript
(NestJS + React) unificaría el lenguaje, pero obligaría a reescribir y
mantener el cliente de CyberArk en TS y desaprovecha ITR. **Django + DRF**
es válido y más "batteries included", pero su fuerte (admin, ORM, formularios
server-rendered) pesa menos en una app que es sobre todo un proxy
inteligente hacia una API externa con una SPA rica adelante.

### 4.3 Multi-tenancy

- Tabla `tenants`: id, nombre, cliente, `deployment` (`privilege_cloud` /
  `self_hosted`), URLs (PVWA, y en PC: Identity / Connector Mgmt / SIA),
  referencia al secreto en el CCP/Vault (query name), estado, ventana de
  mantenimiento, notas. Sin feature-flags de escritura por tenant desde el
  2026-09-22 — el control de qué puede hacer un cliente/operador es sólo
  RBAC (ver CLAUDE.md "Principios no negociables").
- Todo request lleva un `tenant_id`; el middleware resuelve credenciales,
  cliente y permisos para ese tenant y **rechaza** si el operador no tiene
  rol en él.
- Caché de inventario **particionada por tenant** con TTL corto; se puede
  forzar refresh. Nunca se mezcla data de dos tenants en memoria ni en
  respuestas.

### 4.4 El cliente CyberArk unificado

Capa que aísla al resto de la app de las diferencias PC / Self-Hosted y de
las asperezas de la API:

- **Auth pluggable:** `ISPSSAuth` (OAuth2 client_credentials) / `SelfHostedAuth`
  (logon, token crudo, logoff). Ya existen en ITR.
- **Resiliencia:** retry con backoff ante 429 y 5xx transitorios, re-auth
  ante 401, timeouts, límite de concurrencia por tenant (un PVWA on-prem
  aguanta menos que el SaaS).
- **Paginación** transparente (offset/limit y cursor).
- **Normalización de errores** a excepciones tipadas (`NotFound`,
  `Forbidden`, `Conflict`, `RateLimited`, `UpstreamError`…).
- **Modo `dry_run`:** valida y arma el diff sin ejecutar.
- **Tests de contrato:** un set de tests que corre contra el tenant de lab
  y verifica que cada operación sigue funcionando como se espera tras cada
  release de CyberArk.

---

## 5. Identidad, credenciales y auditoría

### 5.1 Operadores de la webapp

**Sin Entra ID / sin nube.**

- **Fase 0–1:** store local en PAM Web Console — usuarios con contraseña Argon2 +
  **TOTP obligatorio** (app authenticator). Alta de usuarios por el
  Platform Admin.
- **Después:** bind LDAP contra el **AD on-prem del MSP** (el operador
  usa sus credenciales de dominio) o un IdP self-hosted (Keycloak) si se
  quiere OIDC. El TOTP se mantiene salvo que el AD ya aporte MFA.
- **Sesión:** cookie de sesión server-side (o JWT corto + refresh).
  Inactividad → logout. Re-auth para entrar a zonas sensibles (escritura,
  audit, gestión de tenants).
- Acceso a PAM Web Console solo desde la red corporativa del MSP / su VPN
  (allowlist de IP). Nunca publicado a internet.

### 5.2 RBAC interno

Roles (combinables con **scope por tenant**: un operador puede ser Operator
en Hooli y solo Viewer en Acme):

| Rol | Puede |
|---|---|
| **Viewer** | Ver inventario, dashboards, reportes. Nada de escritura. |
| **Operator** | + operaciones de escritura de bajo riesgo habilitadas (crear cuenta, editar metadata, change/verify on-demand, agregar miembro de safe). |
| **Approver** | + aprobar/rechazar operaciones destructivas de otros. No puede aprobar las propias. |
| **Tenant Admin** | + gestionar qué features están activas para sus tenants, ver el audit de sus tenants. |
| **Platform Admin** | Gestiona el registro de tenants, los secretos (alta/rotación), RBAC global, configuración de PAM Web Console. No opera CyberArk directamente. |

### 5.3 Service accounts por tenant

- Uno por tenant. Para PC: `client_id` / `client_secret` (Service User de
  Identity). Para Self-Hosted: usuario **local de CyberArk** (auth Internal,
  sin MFA) — mismo criterio que ya se usó para Hooli en ITR.
- **Custodia (sin Key Vault / sin nube):** las credenciales viven en un
  Vault de CyberArk — el lab Self-Hosted del MSP o un Safe dedicado —
  y PAM Web Console las pide en runtime vía **CyberArk CCP** (REST sobre HTTPS, el
  CCP autentica a PAM Web Console por app-id + cert de cliente + allowlist de host/IP,
  sin secreto en disco). Alternativa: **OpenBao / HashiCorp Vault**
  self-hosted. Puente para Fase 0–1: archivo `sops`+`age` fuera del repo.
- **Permisos en CyberArk escalonados por fase**: en Fase 1 el service
  account es de solo lectura (Auditors + PVWAMonitor). Recién cuando un
  cliente aprueba usar escritura se le amplían los permisos, y solo los de
  las operaciones habilitadas.
- Token de sesión: se obtiene bajo demanda, se cachea **en memoria / Redis
  con TTL**, nunca se persiste. Logoff al final en Self-Hosted.
- Rotación de estos secretos: procedimiento documentado + recordatorio
  automático.

### 5.4 Atribución y auditoría (el control central)

Como CyberArk registra las acciones a nombre del **service account**, PAM Web Console
mantiene su **propio log de auditoría append-only** (tabla Postgres con
hash encadenado, y/o export continuo a almacenamiento WORM):

Cada entrada: timestamp, operador (identidad real, del store local o del
AD), rol usado, tenant, operación, objeto(s) afectado(s), payload/diff
(before→after),
`dry_run` sí/no, aprobado por (si aplica), resultado, request-id,
IP/origen, y el/los id(s) de actividad que devolvió CyberArk para poder
cruzar.

- El operador **no puede** borrar ni editar el log.
- Retención larga (definir con Legal/cliente; sugerido ≥ 2 años).
- Reporte "qué cambió el MSP en el tenant X entre fechas" — entregable a
  clientes.

---

## 6. Cobertura de la API de CyberArk

Qué puede llegar a hacer PAM Web Console, con la operación, disponibilidad por
deployment, permiso que necesita el service account y fase del roadmap.
(`PC` = Privilege Cloud/ISPSS, `SH` = Self-Hosted.)

> La columna `SH` se mantiene como referencia (el cliente de API la soporta),
> pero **v1 opera solo tenants `PC`**. Ver banner de alcance.

### Accounts
| Operación | PC | SH | Permiso | Fase |
|---|---|---|---|---|
| Listar / buscar / ver detalle | ✅ | ✅ | List/Retrieve en el safe | 1 |
| Ver actividad (`/Activities`) | ✅ | ✅ | View Audit | 1 |
| Crear cuenta | ✅ | ✅ | Add Accounts | 2 |
| Editar propiedades / metadata | ✅ | ✅ | Update Account Properties | 2 |
| Change / Verify / Reconcile on-demand | ✅ | ✅ | Initiate CPM account management | 2 |
| Cambiar contraseña sólo en la Vault | ✅ | ✅ | Update password value | 3 (RBAC tenant_admin+) |
| Especificar próxima contraseña (CPM) | ✅ | ✅ | Initiate CPM + Specify next password value | 3 (RBAC tenant_admin+) |
| Vincular Logon/Recon/JIT account | ✅ | ✅ | Update | 2 |
| Borrar cuenta | ✅ | ✅ | Delete Accounts | 3 (aprobación) |
| Alta masiva (CSV / feed de discovery) | ✅ | ✅ | Add Accounts | 4 |
| Recuperar secreto (cuenta activa) | ✅ | ✅ | Retrieve | 3 (RBAC tenant_admin+) |

### Safes
| Operación | PC | SH | Permiso | Fase |
|---|---|---|---|---|
| Listar / ver detalle / miembros | ✅ | ✅ | Miembro del safe | 1 |
| Crear safe | ✅ | ✅ | Add Safes | 2 |
| Editar (retención, OLAC, CPM, descripción) | ✅ | ✅ | Manage Safe | 3 |
| Agregar miembro | ✅ | ✅ | Manage Safe Members | 2 |
| Quitar / editar permisos de miembro | ✅ | ✅ | Manage Safe Members | 3 (aprobación) |
| Borrar safe | ✅ | ✅ | Manage Safe | 3 (aprobación reforzada) |

### Platforms
| Operación | PC | SH | Permiso | Fase |
|---|---|---|---|---|
| Listar / ver detalle (props INI en SH) | ✅ | ✅ | Audit / Manage Platforms | 1 |
| Activar / desactivar | ✅ | ✅ | Manage Platforms | 3 |
| Duplicar | ✅ | ✅ | Manage Platforms | 3 |
| Importar / exportar (ZIP) | ✅ | ✅ | Manage Platforms | 3 |
| Editar parámetros | parcial | ✅ | Manage Platforms | 4 (aprobación) |

### Users & Groups
| Operación | PC | SH | Permiso | Fase |
|---|---|---|---|---|
| Listar usuarios del Vault / detalle / last login | ✅ | ✅ | Audit Users | 1 |
| Usuarios de Identity (unión) | ✅ | ❌ | rol en Identity | 1 |
| Crear / editar / habilitar-deshabilitar usuario del Vault | ✅ | ✅ | Add/Update Users | 3 |
| Activar usuario suspendido | ✅ | ✅ | Activate Users | 2 |
| Grupos del Vault (CRUD, miembros) | ✅ | ✅ | Add/Update Users | 3 |

### Sesiones (PSM)
| Operación | PC | SH | Permiso | Fase |
|---|---|---|---|---|
| Listar sesiones activas / históricas | ✅ | ✅ | Monitor sessions | 1 |
| Ver metadata / eventos / SIEM | ✅ | ✅ | Monitor sessions | 1 |
| Terminar sesión activa | ✅ | ✅ | Terminate sessions | 3 (aprobación) |
| Acceso a grabaciones | ✅ | ✅ | View recordings | 4 |

### Otros
| Área | PC | SH | Fase |
|---|---|---|---|
| Componentes (Monitoring / System Health) | ✅ | ✅ | 1 |
| Requests / dual control (aprobar accesos) | ✅ | ✅ | 4 |
| Applications (AAM/AIM) — listar y CRUD | ✅ | ✅ | 4 |
| Discovery / Accounts Feed / Onboarding rules | ✅ | ✅ | 4 |
| Connector Management (agentes CPM/PSM) | ✅ | ❌ | 5 |
| SIA (Secure Infrastructure Access) | ✅ | ❌ | 5 |
| Secrets Hub | ✅ | ❌ | 5 |
| Licencias | ✅ | ❌ (no hay API) | 1 |

---

## 7. Roadmap por fases

Cada fase termina con: probado contra un tenant de laboratorio,
documentado (spec en `docs/specs/`), con su parte de RBAC y auditoría.

### Fase 0 — Fundaciones ✅

Repo + `docker-compose` (Postgres 16 + Redis 7 + API FastAPI async),
modelos/migraciones (`tenants`/`operators`/`audit_log`), cliente CyberArk
async, audit log append-only con hash encadenado, secret store puente
(`age`), registro de tenants + seed, endpoints de inventario en vivo, auth
de operadores (Argon2 + TOTP + JWT + RBAC jerárquico), CI (ruff + mypy +
pytest, eslint + tsc + build) + logging estructurado + pre-commit/gitleaks.

### Fase 1 — Read-only live multi-tenant ✅

Frontend React + Tailwind v4 + shadcn/ui, login en 2 pasos (password +
TOTP), inventario con pestañas (Cuentas/Safes/Plataformas/Usuarios/
Sesiones/Componentes) con paginación, orden y búsqueda server-side, vista
de detalle con actividad + audit trail, búsqueda cross-tenant, dashboard
de compliance (umbral ajustable por tenant + cruce con la política de
rotación de la plataforma, errores de CPM, salud de componentes, caché
Redis), export CSV/PDF, grillas virtualizadas (TanStack Table + Virtual),
sync de inventario a Postgres, reporte ITR mensual desde la UI, RBAC con
scope por tenant + refresh token rotativo, página de auditoría, System
Health cross-tenant y troubleshooting de errores de CPM.

### Fase 2 — Escritura de bajo riesgo ✅

Maquinaria común (`writeop.run_write`: dry-run + diff + rate-limit +
circuit breaker + audit, `<DryRunDialog>` en el frontend). Operaciones:
editar cuenta, change/verify/reconcile, agregar miembro de safe, activar
usuario, vincular cuentas (logon/reconcile), crear cuenta, crear safe,
edición en lote y cambio de plataforma.

### Fase 3 — Escritura de alto riesgo ✅

Confirmación reforzada (`confirm_phrase`) + RBAC `approver+` +
**aprobación de un segundo operador** (`docs/specs/dual-approval.md`).
Operaciones: editar/quitar miembros de safe, editar safes, reasignar PSM
de plataforma (con rollback automático), historial de versiones de
contraseña, borrar cuenta/safe (con backup cifrado previo), revelar
contraseña, backup cifrado de credenciales de todo el tenant, fijar
contraseña manual, ciclo de vida de plataformas (activar, duplicar,
borrar, importar `.zip`), terminar sesiones y excepciones de compliance.

### Fase 4 — Operaciones MSP avanzadas ✅

Onboarding masivo por CSV, campañas de rotación/remediación, base de
conocimiento de errores del CPM, grabaciones de sesión con reproducción
embebida (decodificador propio del códec SCPR de PSM → ffmpeg →
`<video>`), integración con Discovery.

### Fase 5 — Extras de Privilege Cloud + automatización

Jobs programados (ARQ + Redis: sync nocturno de inventario, reportes
pesados), reglas de alerta con severidad y debounce enviadas a Microsoft
Teams, espacio Remote Access (Alero), Connector Management y SIA (solo
lectura). En backlog: Secrets Hub, API pública y credencial del service
user leída desde la propia bóveda.

### Identity (administración de SSO/MFA)

Usuarios de Identity (estado, bloquear/desbloquear), roles (lista, alta
de roles estáticos, ver y agregar miembros).

### Reportes (transversal, read-only)

Catálogo de reportes con export CSV y Excel propio por reporte: safes
duplicados, permisos de safe, inventario de cuentas, balance de PSM,
candidatos de licencia, inactividad de targets, actividad de cuentas,
cuentas que no rotan, fallas de rotación por tipo de sistema y accesos
de un usuario.

---

## 8. Relación con ITR

ITR (*Telemetry Reporter*) es una herramienta previa de solo
lectura que genera un reporte HTML de telemetría por tenant. De ahí se
reutilizó el cliente de API (base de `app/cyberark/client.py`) y las
funciones `analyze_*` que calculan las métricas, que aquí se renderizan
como dashboards interactivos. El reporte mensual en sí también se genera
desde la página Reportes (`backend/app/itr/`, self-contained).

---

## 9. Deployment

- Contenedores Docker Compose (API + worker + Postgres + Redis + nginx con
  la SPA) en una VM Linux on-prem, **nunca publicada a internet**: acceso
  solo desde la red interna o por túnel SSH. Requiere salida a internet
  para llegar a los tenants de Privilege Cloud.
- `infra/pam-web-console.service` (systemd) mantiene el stack arriba;
  `infra/deploy.sh` actualiza con verificaciones.
- HTTPS con certificado propio (autofirmado por defecto, o uno de una CA
  interna / mkcert, ver `tls/README.md`).
- El diseño original incluyó un esquema activo/standby con backup nocturno
  al segundo servidor y re-encadenado del audit log en el fail-back
  (`manage.py export-audit-since` / `import-standby-audit`).

---

## 10. Seguridad y cumplimiento (checklist)

- [x] Login con MFA obligatorio (TOTP). Anti-fuerza-bruta (lockout por
      usuario+IP) + auditoría de cada intento. *(allowlist de IP / nunca
      expuesto = deployment.)*
- [x] RBAC con scope por tenant; deny-by-default.
- [~] Service accounts fuera del repo y fuera del disco de PAM Web Console: hoy en
      `secrets.age` (fuera de git, sólo en el server); CCP / OpenBao = Fase 2.
      Guard de arranque que rechaza `PAM_SECRET_KEY` inseguro en prod.
- [ ] Permisos del service account escalonados por fase y por cliente.
- [~] Log de auditoría append-only, con hash encadenado (hecho) + trigger
      Postgres anti UPDATE/DELETE. Export WORM y retención ≥ 2 años =
      pendiente (deployment / política).
- [~] Workflow de aprobación para operaciones destructivas; no se puede
      auto-aprobar. Implementado 2026-09-28 (`docs/specs/dual-approval.md`);
      falta probarlo en el server con dos operadores reales.
- [x] `dry-run` + diff obligatorio antes de cualquier escritura + `reason`
      obligatoria (`writeop.run_write`, Fase 2).
- [x] Circuit breaker por cantidad de objetos (`PAM_WRITE_OBJECT_CAP`) +
      rate-limit de escritura por `(operador, tenant)`.
- [ ] Snapshot de reversión antes de operaciones destructivas.
- [x] Límite de concurrencia por tenant hacia CyberArk (hecho, semáforo).
      Anti-fuerza-bruta en `/auth/login` (hecho) + rate limit global por IP
      en login (`docs/specs/done/login-hardening.md`, implementado y probado en
      el server, 2026-09-30). Rate-limit general de lecturas (`GET`) por
      `(operador, tenant)`/operador implementado 2026-09-25
      (`docs/specs/done/api-read-rate-limit.md`), probado en el server
      (2026-09-30).
- [ ] IP allowlist / acceso solo desde red corporativa o ZTNA. *(deployment)*
- [x] **SCA** (`pip-audit` + `npm audit`), **secret scanning** (`gitleaks`) y
      **SAST-lite** (reglas `S` de ruff) en CI. Ver
      `docs/specs/done/hardening-pre-escritura.md`. Falta: escaneo de imagen
      (trivy — cuando el CI buildee imágenes).
- [x] Secret scanning en el repo (gitleaks) + pre-commit
      (`.pre-commit-config.yaml`).
- [ ] Pentest antes de habilitar escritura en producción.
- [ ] Backups de Postgres con restore probado.
- [ ] Plan de respuesta a incidentes + procedimiento break-glass.
- [x] Tenant de laboratorio (Privilege Cloud del equipo PAM); ninguna
      feature de escritura llega a prod sin pasar por ahí.
- [ ] Revisión/aprobación de seguridad de cada cliente antes de conectar su
      tenant con permisos de escritura.

