# setup-connector-management

- **Estado:** hecha (probada contra el lab y Globex el 2026-10-07)
- **Roadmap:** Fase 5, "Connector Management" (sin empezar en `docs/PLAN.md` §7)
- **Fecha:** 2026-10-01

## Problema

El PVWA/portal de CyberArk tiene un espacio **Setup > Deployment > Connector
management** (conectores del sistema, conectores SIA, pools, networks). PAM Web Console
sólo usa esos datos para el reporte ITR; el equipo PAM tiene que entrar al
portal de cada cliente para ver qué conectores hay, en qué pool están, si están
vivos y si tienen un upgrade pendiente. Con 5 tenants eso son 5 logins.

## Alcance

Entra (corte 1, **sólo lectura**):
- Espacio de navegación nuevo **Setup** con la sección "Connector
  management".
- **System connectors**: la tabla del portal (host, componentes activos,
  plataforma, IP, SO, último heartbeat, pool, proxy, upgrade disponible).
- **SIA connectors**: la tabla de SIA (servicio aparte, ya soportado por el
  cliente).
- **Connector pools**: nombre, descripción y los conectores de cada pool.

Queda afuera:
- **Escritura** (corte 2, spec aparte): *Upgrade* de conectores, alta/edición
  de pools, asignar conectores a pools. Un upgrade puede dejar a un cliente sin
  CPM/PSM, así que va con `approver+`, dry-run y confirmación reforzada.
- **My networks**: no se pudo confirmar el endpoint (ver "Pendiente de
  confirmar").
- **HTTPS relays** e **Identity connectors**: sin API confirmada.
- Alta de conectores ("Add a connector": descarga de instalador).

## Requisitos

1. **Listar System connectors de un tenant.** `GET
   /tenants/{id}/setup/connectors` devuelve los conectores de Connector
   Management con el nombre de su pool resuelto.
   - Dado un tenant con 2 conectores en el pool `Globex_GCP`, cuando un
     `viewer` abre la página, entonces ve 2 filas con host, IP, SO, componentes
     `4/4 active`, último heartbeat y pool.
2. **Estado de componentes legible.** `components` activos sobre total se
   calcula con los `componentStatus` en `{Active, Initializing, Downloaded}`
   (mismo criterio que `analyze_connectors` de ITR). Una fila con algún
   componente caído se marca como degradada.
3. **Upgrade disponible visible, no ejecutable.** Si algún componente trae
   `upgradeVersions`, la fila lo muestra ("Upgrade disponible"). En este corte
   no hay botón de acción.
4. **Listar SIA connectors y pools.** `GET .../setup/sia-connectors` y `GET
   .../setup/connector-pools`, mismo patrón.
5. **Tenant sin la URL configurada.** Si el tenant no tiene
   `connector_management_url` (o `sia_connector_url`), el endpoint responde
   `409` con un mensaje claro y la página lo muestra, no un error genérico. Un
   `403` de SIA se traduce a "SIA no está licenciado o el service account no
   tiene el rol SIA Admin" (ya documentado en `client.list_sia_connectors`).
6. **RBAC y auditoría.** `viewer+` en el tenant. Cada lectura se audita
   (`setup.connectors.list`, `setup.sia_connectors.list`,
   `setup.connector_pools.list`) con el conteo devuelto.
7. **Navegación.** Espacio "Setup" en `AppShell` (ícono de engranaje distinto
   del de Administración) con las rutas `/setup/connectors`,
   `/setup/sia-connectors` y `/setup/connector-pools`, visible para `viewer+`.

## Diseño técnico

- **Cliente** (`app/cyberark/client.py`): ya existen `list_connectors()`
  (`GET {connector_management_url}/api/connectors`) y `list_sia_connectors()`.
  Se agrega `list_connector_pools()` →
  `GET {connector_management_url}/api/connector-pools`, respuesta
  `{"connectorPools": [{poolId, name, description, ...}]}`.
- **Dominio** (`app/domain/connectors.py`, nuevo): funciones puras que arman
  la fila de la grilla a partir del conector crudo y el mapa `poolId -> name`
  (host, IP privada o pública, SO, versión, componentes activos/total,
  heartbeat, upgrade disponible, pool). Se reutiliza el criterio de
  `status_ok` de `analyze_connectors`. Los timestamps de `components`
  (`installedAt`/`updatedAt`) vienen en **milisegundos**; el centinela `-1` de
  `usedCpu`/`usedMemory` significa "sin datos".
- **Router** (`app/api/setup.py`, nuevo, prefijo `/tenants/{tenant_id}/setup`):
  tres GET, `_viewer`, con `tenant_client` y `audit.record`, mismo patrón que
  `inventory.py`. Lecturas en vivo con caché corta (30 s, como el resto de los
  endpoints de lectura de PAM Web Console) para no pegarle al servicio en cada refresco.
- **Frontend:** `pages/SetupConnectors.tsx` (con pestañas System / SIA) y
  `pages/SetupConnectorPools.tsx`, usando `DataTable` y `TenantScope`. Texto
  de estado con los tokens `text-success-ink`/`text-warning-ink`/`text-danger-ink`
  y botones sólo con ícono con `aria-label` (convención de accesibilidad del
  repo).
- **Relación con otras páginas:** *Componentes* (Operación) y *System Health*
  muestran el estado por componente; Setup lo muestra por conector (host).
  No se pisan, pero la fila de conector linkea a Componentes filtrado por
  host si resulta útil (se decide al implementar, no bloquea).

## Pendiente de confirmar (antes de la implementación)

La documentación de CyberArk (`docs.cyberark.com/.../developer/cm_apis_*`) no
se pudo leer desde este entorno. Hay que confirmar contra un tenant real o con
la doc:
- ~~**Cómo referencia el conector a su pool** en `GET /api/connectors`~~ —
  confirmado el 2026-10-07: `connectorPoolId` (ver Tareas).
- **Endpoint de networks** ("My networks") y de pertenencia network-pool.
- **Endpoint de upgrade** y sus permisos (para el corte 2).

## Tareas

- [x] Confirmar el campo de pool en la respuesta real de `/api/connectors`.
      Confirmado contra el lab (2026-10-07): es `connectorPoolId` (ya
      cubierto por `_POOL_ID_KEYS`).
- [x] Último heartbeat: en el lab la columna salía vacía porque se leía
      `lastHeartbeat`/`lastSeen`, que no existen. El campo real es
      `updatedAt` del conector (ms), el mismo valor que el portal muestra
      como "Last heartbeat"/"Last updated". Corregido el 2026-10-07, con
      test.
- [x] SIA connectors: en Globex Host e IP salían vacíos porque se leían
      `hostname`/`ipAddress`. Los campos reales son `id`/`hostName`/`hostIp`
      (ya confirmados por `analyze_sia_connectors` de ITR). Corregido el
      2026-10-07, con test.
- [x] `list_connector_pools()` en el cliente + `app/domain/connectors.py`.
- [x] Router `app/api/setup.py` + acciones en `audit_catalog.py`.
- [x] Tests unitarios (dominio, endpoints, tenant sin URL, 403 de SIA, RBAC,
      auditoría).
- [x] Espacio "Setup" en `AppShell`, rutas en `App.tsx`, páginas y tipos.
- [x] ruff, ruff format, mypy; lint, tsc y build (pytest no corrió local: Python 3.14 sin wheels; queda para CI).
- [x] Probar contra Globex (2 conectores en `Globex_GCP`) y un tenant sin
      SIA. Probado el 2026-10-07 (lab y Globex); el tenant sin SIA queda
      cubierto por los tests unitarios.
- [x] Mover la spec a `done/` y actualizar `PLAN.md` y `ESTADO_ACTUAL.md`.
