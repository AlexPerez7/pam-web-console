# system-health — "System Health" cross-tenant

- **Estado:** aprobada
- **Roadmap:** `docs/PLAN.md §7` Fase 4/5 (extra a pedido de Alex,
  comparando contra la página "System Health" real del PVWA)
- **Fecha:** 2026-09-17

## Problema

El PVWA tiene una página "System Health" (ver capturas de Alex,
2026-09-17): tarjetas resumen por componente (Web Portal → Active Users,
CPM → App User Instances + Managed Accounts, PSM and PSM for SSH → App
User Instances + Concurrent Sessions) con un drill-down a una tabla de
instancias (IP, versión, componente, estado de conectividad, última
conexión). PAM Web Console tiene el equivalente del drill-down (`Components.tsx`,
tabla plana) pero no las tarjetas resumen, y **el PVWA sólo puede mostrar
esto de UN tenant a la vez** — el equipo PAM hoy tiene que
entrar a 4 tenants distintos (Globex/Umbrella/Acme/el MSP) para
saber si algo está caído en cualquiera de ellos.

"Pero mejor" (decisión de Alex, 2026-09-17): una vista **cross-tenant**
que el PVWA no puede ofrecer — los 4 tenants de un vistazo, sin entrar a
ninguno — con el detalle por tenant (tabla de instancias) a un clic.

## Alcance

**Entra:**
- `GET /system-health` — cross-tenant, misma arquitectura que
  `app/api/search.py` (client nuevo por tenant, aislado, tolerante a que
  un tenant falle). Grilla: una fila por tenant, con salud de Vault +
  Web Portal (PVWA) + CPM + PSM/PSM for SSH + cuentas gestionadas +
  sesiones concurrentes.
- `GET /tenants/{id}/system-health` — el mismo cálculo para un solo
  tenant (lo usa el fan-out de arriba, y sirve por su cuenta si hace
  falta refrescar sólo uno).
- Página nueva `pages/SystemHealth.tsx` (`/system-health`, espacio
  Operación, arriba de "Componentes"): grilla de tenants con badges de
  estado por componente; cada fila linkea a `/components?tenant=<id>`
  (la tabla de instancias YA existente, sin cambios) para el drill-down.
- Nueva capacidad real: **estado del Vault** (`client
  .components_monitoring_summary`, `GET /API/ComponentsMonitoringSummary`)
  — PAM Web Console hoy no consulta el Vault en absoluto. Degrada a "sin datos" si
  el service account no tiene permiso (mismo criterio que
  `component_details`, que ya tolera 403/404).
- Caché en Redis propia (`PAM_SYSTEM_HEALTH_CACHE_TTL`, no atada al
  caché del Dashboard — mismo criterio de independencia que
  `troubleshooting.py`, que también recalcula sobre datos que el
  Dashboard ya toca). `?refresh=1` fuerza recálculo.
- Auditado `system_health.view` por tenant (siempre, también en cache
  hit — mismo criterio que `dashboard.view`).

**Fuera (a propósito):**
- Tarjetas de "Managed Accounts"/"Concurrent Sessions"/"Active Users"
  calcadas EXACTO del PVWA usando `ComponentsMonitoringSummary.Components`
  — los nombres de campo reales de esos números no están confirmados
  (`docs.cyberark.com` bloqueó el fetch, y los tests de `psPAS` usan
  mocks genéricos que no exponen ese schema). En cambio, PAM Web Console arma los
  mismos números con **datos que ya obtiene y confirma en otros lados**:
  cuentas gestionadas = `client.list_accounts()` (ya lo usa el
  Dashboard), sesiones concurrentes = `client.live_sessions()` (ya lo usa
  el Dashboard/KPI de ayer), instancias conectadas/total por componente =
  `client.component_details()` (ya lo usa `Components.tsx`/Dashboard,
  confirmado en producción). Sólo el **Vault** es dato nuevo — y ahí sí
  se usan los campos de `ComponentsMonitoringSummary.Vaults`
  (`Role`/`IP`/`IsLoggedOn`), que estos sí están confirmados contra el
  código fuente de `psPAS` (no sólo inferidos).
- Tarjetas/summary en el detalle por tenant (`Components.tsx` sigue
  siendo la tabla plana que ya es, sin cambios) — la grilla cross-tenant
  ES la vista "mejor"; enriquecer también la vista por tenant queda como
  posible follow-up, no bloquea esta spec.
- Alertas/notificaciones (Slack, email) si un componente cae — Fase 5
  ("Reglas de alerta"), no acá.
- Historial/tendencia de disponibilidad — necesitaría muestreo periódico
  (jobs en background), mismo límite que "dashboard v3" y las campañas.
- AAM/AIM en la grilla compacta — sigue visible en el drill-down
  (`Components.tsx`, sin cambios) pero no ocupa una columna propia en la
  vista cross-tenant (la mayoría de los tenants no tiene Credential
  Providers desplegados).

## Requisitos

1. **Grilla cross-tenant, camino feliz.** Dado un operador `viewer+` con
   acceso a los 4 tenants, `GET /system-health` devuelve una fila por
   tenant con: estado de Vault (conectado/sin datos), conectadas/total de
   Web Portal, CPM y PSM/PSM for SSH, `accounts_total`,
   `sessions_active`. Cache miss → calcula; cache hit (dentro del TTL) →
   devuelve lo cacheado, `cached: true`.
2. **Aislamiento por RBAC.** Un operador sin acceso a `acme` no ve esa
   fila en la grilla (mismo filtro que `/search`,
   `rbac.accessible_tenant_ids`).
3. **Un tenant caído no rompe la grilla.** Si `acme` tiene el service
   account vencido o CyberArk no responde, esa fila llega con
   `{"error": "..."}` y las otras 3 filas igual muestran datos —
   auditado `result: error` sólo para esa fila.
4. **Vault sin permiso.** Si el service account no tiene permiso para
   `ComponentsMonitoringSummary` (403) o el endpoint no aplica (404), la
   columna Vault de ese tenant muestra "sin datos" — el resto de la fila
   (componentes/cuentas/sesiones) no se ve afectado.
5. **Refresh manual.** `GET /system-health?refresh=1` recalcula todos los
   tenants ignorando el caché.
6. **Drill-down.** Click en una fila de la grilla navega a
   `/components?tenant=<id>` (página existente, sin cambios de
   comportamiento).
7. **Auditoría.** Cada tenant de la respuesta (ok o error) genera un
   registro `system_health.view` con ese `tenant_id` — igual en cache hit
   y cache miss.

## Diseño técnico

### `app/cyberark/client.py`

```python
async def components_monitoring_summary(self) -> dict | None:
    """GET /API/ComponentsMonitoringSummary — {"Components": [...],
    "Vaults": [...]}. None si no aplica (404) o sin permiso (403) — mismo
    criterio que component_details. Sólo se usa el campo Vaults por ahora
    (Role/IP/IsLoggedOn, confirmados vía psPAS Get-PASComponentSummary);
    Components queda sin usar hasta confirmar sus campos reales."""
    try:
        data = await self.get("/API/ComponentsMonitoringSummary")
    except (NotFound, Forbidden):
        return None
    return data if isinstance(data, dict) else None
```

### `app/config.py`

```python
# TTL (segundos) del caché en Redis de System Health cross-tenant. Caché
# propia, no comparte la del Dashboard (mismo criterio que
# troubleshooting_cache_ttl, aunque toquen datos parecidos).
system_health_cache_ttl: int = 300
```

### `app/api/system_health.py` (router nuevo)

```python
router = APIRouter(tags=["system-health"])
_viewer = require_tenant_access(OperatorRole.viewer)  # sólo para el endpoint por tenant

_COMPONENT_IDS = ["PVWA", "SessionManagement", "CPM", "AIM"]  # copia local, mismo
                                                               # criterio que dashboards.py
                                                               # (ya la duplica)
_COMPONENT_LABELS = {"PVWA": "Web Portal", "SessionManagement": "PSM y PSM for SSH", ...}

async def _compute_snapshot(client: CyberArkClient) -> dict:
    """Calcula el snapshot de un tenant YA AUTENTICADO. Deja que cualquier
    excepción de CyberArk suba — cada caller decide cómo tratarla."""
    accounts, live, monitoring = await asyncio.gather(
        client.list_accounts(), client.live_sessions(), client.components_monitoring_summary()
    )
    component_lists = await asyncio.gather(*(client.component_details(c) for c in _COMPONENT_IDS))
    comp_summary = analysis.analyze_components(dict(zip(_COMPONENT_IDS, component_lists, strict=True)))
    vaults = (monitoring or {}).get("Vaults") if monitoring else None
    if isinstance(vaults, dict):
        vaults = [vaults]
    primary = next((v for v in (vaults or []) if v.get("Role") != "DR"), None)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "cached": False,
        "vault": (
            {"available": True, "connected": bool(primary.get("IsLoggedOn")), "ip": primary.get("IP")}
            if primary
            else {"available": False}
        ),
        "components": [
            {
                "id": cid,
                "name": _COMPONENT_LABELS.get(cid, cid),
                "available": comp_summary[cid]["available"],
                "connected": comp_summary[cid].get("connected", 0),
                "total": comp_summary[cid].get("total_instances", 0),
            }
            for cid in _COMPONENT_IDS
        ],
        "accounts_total": len(accounts),
        "sessions_active": len(live),
    }
```

**Dos wrappers de caché distintos**, no uno solo — descubierto al implementar:
`tenant_client` (que usan TODOS los demás endpoints del proyecto) levanta
`HTTPException` si el tenant está caído, lo cual es correcto para el
endpoint por tenant pero rompería el fan-out cross-tenant entero por un
solo tenant caído. Por eso:

- `_cached_snapshot_via_tenant_client(tenant_id, session, refresh)` — usa
  `tenant_client` (deja subir la excepción, mismo comportamiento que
  `dashboard`/`troubleshooting`/etc.). La usa `GET /tenants/{id}
  /system-health`.
- `_snapshot_for_fanout(tenant, refresh)` — nunca lanza (try/except manual
  con `get_secret`/`build_client`, calcado de `search._search_one`);
  devuelve el snapshot o `{"error": "..."}`. La usa `GET /system-health`.

Ambas comparten `_compute_snapshot` (recibe un cliente ya autenticado) y
la misma cache key (`pam:system-health:{tenant_id}`) — un refresh manual
de un tenant desde su propia página también sirve al fan-out, y viceversa.

```python
@router.get("/tenants/{tenant_id}/system-health")
async def tenant_system_health(
    tenant_id: str, refresh: bool = Query(False),
    op: Operator = Depends(_viewer), session: AsyncSession = Depends(get_session),
) -> dict:
    data = await _cached_snapshot_via_tenant_client(tenant_id, session, refresh=refresh)
    await audit.record(session, operator=op.username, role=op.role.value,
                        action="system_health.view", result="ok", tenant_id=tenant_id)
    await session.commit()
    return {"tenant": tenant_id, **data}


@router.get("/system-health")
async def system_health_all(
    refresh: bool = Query(False),
    op: Operator = Depends(current_operator), session: AsyncSession = Depends(get_session),
) -> dict:
    # mismo filtro de tenants accesibles/habilitados/privilege_cloud que search.py
    ...  # asyncio.gather(_snapshot_for_fanout(...) por tenant), audita
         # system_health.view por tenant (ok/error), un solo commit al final.
```

`analysis.analyze_components` se reusa tal cual (ya existe, portado del
Dashboard). No hace falta tocar `analysis.py`.

### Frontend

- `pages/SystemHealth.tsx` — sin `TenantScope` (es cross-tenant, mismo
  criterio que `Search.tsx`/`KnowledgeBase.tsx`). `Table` simple (no
  `DataTable` genérico — pocas filas, sin paginar): columnas Tenant /
  Vault / Web Portal / CPM / PSM y PSM for SSH / Cuentas gestionadas /
  Sesiones activas. Cada celda de componente: `Badge` success/warning/
  danger/neutral según conectadas vs. total (`success` todo conectado,
  `warning` parcial, `danger` cero conectadas, `neutral` sin datos) +
  texto `conectadas/total`. Botón "Actualizar" (`?refresh=1`, mismo
  patrón `wantRefresh` de `Dashboard.tsx`). Fila entera clickeable
  (`onClick` → `navigate("/components?tenant=" + id)`).
- `App.tsx`: nuevo `NavItem` `{ to: "/system-health", label: "System
  Health" }` en el espacio Operación, antes de `/components`.
- `lib/types.ts`: `SystemHealthVault`, `SystemHealthComponent`,
  `SystemHealthSnapshot`, `TenantSystemHealth`, `SystemHealthRow`,
  `SystemHealthError`, `SystemHealthResponse`.

## Tareas

- [x] `client.py`: `components_monitoring_summary()`.
- [x] `app/config.py`: `system_health_cache_ttl`.
- [x] `app/api/system_health.py`: `_compute_snapshot` +
      `_cached_snapshot_via_tenant_client`/`_snapshot_for_fanout` +
      `GET /tenants/{id}/system-health` + `GET /system-health`.
- [x] `app/main.py`: registrar el router nuevo.
- [x] Tests unit (`test_system_health.py`, 9 casos): forma del snapshot,
      Vault sin datos, Vault ignora entradas DR, RBAC/aislamiento del
      fan-out, cache hit no vuelve a pegarle a CyberArk, un tenant caído
      no rompe a los demás.
- [x] `ruff` + `ruff format` + `mypy` + `pytest` verdes (433 tests).
- [x] `pages/SystemHealth.tsx` + entrada de nav + tipos.
- [x] `lint` + `tsc` + `build` verdes.
- [x] Probado contra los 4 tenants con el fix (2026-09-18): degradación
      por lectura confirmada (Acme ya no tira la fila entera), números
      de cuentas/sesiones/componentes coinciden con Dashboard/Componentes.
      Spec cerrada.

## Primera prueba real (2026-09-17) — bug encontrado y corregido

Contra los 4 tenants: **Globex** anduvo de punta a punta (Vault
conectado, Web Portal 1/2, CPM 2/2, PSM 2/2, 1.895 cuentas, 36 sesiones).
Los otros 3 tenants aparecieron con toda la fila en error — dos por
motivos ajenos a esta feature (Umbrella: credenciales del service account
rechazadas por Identity, 400 `invalid client creds`; Laboratorio/lab:
`AttributeError` — ver la corrección más abajo, "Sobre el MSP
(lab)"), pero **Acme reveló un bug real de esta spec**: `GET
/API/LiveSessions -> 404` (el tenant no expone sesiones en vivo) hacía
fallar `_compute_snapshot` completo con una excepción sin atrapar,
tirando abajo TODA la fila — Vault y componentes incluidos, que sí
habían respondido bien.

**Fix:** `_compute_snapshot` ahora usa `asyncio.gather(...,
return_exceptions=True)` y degrada cada lectura por separado (mismo
patrón que `dashboards._ok()`) — `accounts_total`/`sessions_active` pasan
a `int | None` (`null` si esa lectura puntual falló). Con esto, un
tenant sólo aparece en `errors` si falla la autenticación o el secret
store (algo que impide leer CUALQUIER dato); una lectura puntual caída
ahora se ve como "—" en esa columna, con el resto de la fila intacta.
Test nuevo: `test_compute_snapshot_una_lectura_caida_degrada_sola_no_tira_todo`
(reproduce el caso real de Acme). El texto de la UI también se
reformuló para no depender de comparaciones con otros productos.

Reconfirmado contra los 4 tenants con el fix (2026-09-18): Acme ya
muestra Vault/componentes con `sessions_active: null` en vez de la fila
entera en error.

## Mensajes de error más claros (2026-09-17)

A pedido de Alex, viendo el mensaje real de Umbrella en la UI
(`auth: ISPSS auth falló (400): {"error":"access_denied",
"error_description":"invalid client creds or client not allowed"}`):
esta página la mira el equipo para saber de un vistazo si algo anda mal,
no para debuggear PAM Web Console, así que el JSON crudo de CyberArk no aporta.
`_friendly_error(e)` nuevo en `system_health.py` traduce las excepciones
típicas de un tenant caído:

- `AuthError` con `access_denied`/`invalid client` en el mensaje →
  "credenciales inválidas o rechazadas por Identity" (el caso de Umbrella).
- Otro `AuthError` → "no se pudo autenticar contra CyberArk".
- `SecretStoreError` → "no se encontraron credenciales para este tenant
  en el secret store".
- Cualquier otra excepción → "error inesperado consultando el tenant
  (`<Tipo>`)" — nunca el texto crudo (evita que algo como
  `AttributeError: 'NoneType' object has no attribute 'get'` llegue tal
  cual a la pantalla). 3 tests nuevos (`test_friendly_error_*`).

**Sobre Laboratorio (lab) — corrección (2026-09-17):** la primera versión
de esta nota decía que `pvwa_url` tenía cargada la URL de OTRO tenant por
error — **incorrecto**, `docs/PLAN.md` §"Fase 2" ya documentaba el caso
real desde el 2026-09-15: el tenant lab cambió de URL del lado de
CyberArk (motivo desconocido) a `umbrellatest.cyberark.cloud` — Alex
confirmó el redirect real (`pamlab.cyberark.cloud` →
`umbrellatest.cyberark.cloud`) y ya actualizó `pvwa_url` en
`seed/tenants.json` en su momento. Lo que esa nota marcaba como
pendiente — y lo más probable explicando el `AttributeError`/lo que sea
que muestre ahora — es que **`seed-tenants` nunca se corrió en el
server**: el seed es un upsert manual, `seed/tenants.json` cambiar en el
repo no actualiza sola la fila `Tenant` en Postgres. Además,
`connector_management_url`/`sia_connector_url` de ese tenant siguen con
el nombre viejo (`pamlab.*`, sin confirmar si cambiaron igual) —
no los usa System Health, pero si Identity fallara para este tenant
revisar ahí. No es un bug de esta feature: `_friendly_error` ahora al
menos evita que el error crudo llegue a la pantalla, pero el fix real es
correr `docker compose exec api python manage.py seed-tenants` en el
server y volver a probar.

**Resuelto de verdad (2026-10-02):** el 302 del lab no era un problema de
vinculación del lado de CyberArk (caso de soporte) sino la URL: `pvwa_url`
apuntaba a `umbrellatest.cyberark.cloud`, que sólo es el destino del
redirect del dominio raíz. Con un token válido, `umbrellatest...` da 302 y
**`https://pamlab.privilegecloud.cyberark.cloud`** da 200. Corregido en
`seed/tenants.json` (y recordar: el seed vive en la imagen, hace falta
`docker compose up -d --build` antes de `seed-tenants`). Ver
`docs/plan/fase-2.md`.
