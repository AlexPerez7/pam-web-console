# Arquitectura de PAM Web Console

Árbol completo del backend y frontend, con notas de diseño por módulo.
Referenciado desde `CLAUDE.md` § Arquitectura — leer antes de tocar
cualquier router, servicio o componente para entender dónde encaja y
qué ya existe.


```
backend/  FastAPI async + SQLAlchemy 2 async + Alembic + httpx
  app/main.py        crea la app, incluye routers
  app/config.py      pydantic-settings, prefijo PAM_ (db_url, redis_url,
                     secret_key, cyberark_max_concurrency, secrets_file,
                     secrets_age_key)
  app/db.py          engine + AsyncSession (get_session = dep de FastAPI)
  app/models/        Tenant, Operator, RoleAssignment, AuditLog, AccountBackup,
                     PlatformBackup, Campaign/CampaignAccount, KbArticle,
                     AccountSnapshot, ItrInfraFile, RefreshToken,
                     ApprovalRequest, AlertState, ComponentInstanceSeen
                     (+ Base, Mixin). Tenant
                     gana `alero_tenant_id` (2026-09-21, migración `0013`)
                     — id público del tenant de Remote Access (va en claro
                     en el JWT, no es secreto), nulo = el cliente no tiene
                     Alero configurado, gatea el espacio de nav.
  app/alero/         (2026-09-21) `AleroClient` — CyberArk Remote Access
                     (Alero), acceso remoto de terceros/proveedores.
                     **API separada** de Privilege Cloud/Identity
                     (`api.alero.io`), con su propio modelo de auth
                     (Private Key JWT / RFC 7523 contra un Service Account
                     por tenant) — no reusa nada de `app.cyberark`, ni su
                     jerarquía de errores (`AleroError`/`AleroAuthError`/
                     `AleroNotFound`, propia). Puerto directo de
                     `Alero-ApiClient.ps1` (`Telemetry-Reporter`,
                     que ya había resuelto el modelo de auth contra un
                     tenant real) — en Python no hace falta parsear el PEM
                     a mano, `cryptography` (dependencia de PyJWT para
                     RS256) lo importa directo. `list_vendors`/
                     `get_vendor_detail`/`list_users`/`get_user_detail`/
                     `list_groups`/`list_sites`/`list_site_applications`/
                     `list_activities`, todos paginados (`limit` capeado
                     en 100 por la API salvo `activities`, 500).
                     `get_vendor_last_login` (2026-09-22, fast-follow) —
                     login real de UN vendor, troceando `activities` en
                     ventanas de 30 días con corte temprano (ver "Estado
                     actual"). Ver `docs/specs/done/alero-vendors.md`.
  app/cyberark/      cliente async (portado de cyberark_api.py de ITR)
    auth.py            ISPSSAuth (OAuth2, header Bearer, expone
                       identity_tenant_url) / SelfHostedAuth (logon, token
                       crudo). login/refresh/logout(client).
    client.py          CyberArkClient: _request con retry (429/5xx) +
                       re-auth (401) + semáforo de concurrencia; get_all
                       (paginación offset/limit); list_accounts/safes/
                       platforms/users/psm_servers/server_info;
                       redrock_query(script) (POST {identity}/Redrock/query,
                       sólo SELECT); get_identity_users_full() (paginado
                       LIMIT/OFFSET, tabla `Users` completa — más campos
                       que `GET /identity-users`, para el reporte ITR).
                       get_license_report/list_connectors/
                       list_sia_connectors/get_all_sia_connector_details
                       (Licencias/Connector Management/SIA — dominios
                       propios, mismo bearer token, alimentan sólo el
                       reporte ITR). build_client(tenant, secret).
    errors.py          CyberArkError y subtipos (NotFound/Forbidden/Conflict/
                       RateLimited/UpstreamError).
  app/core/
    audit.py          record() / verify_chain() — hash encadenado sha256.
                       record() toma el request_id del ContextVar si no se
                       le pasa uno. `verify_chain()` devuelve
                       `ChainVerification` (2026-09-29, Tanda 3 de
                       docs/specs/done/audit-page.md — antes una tupla
                       `(ok, broken_id)`): suma `checked`/`first_id`/
                       `last_id` (R11), sin cambiar cómo se calcula el
                       hash. `manage.py verify-audit` y los tests se
                       actualizaron al nuevo tipo.
    audit_catalog.py   (2026-09-29) `classify(action) -> kind` (consulta/
                       cambio/sensible/acceso) + `category(action) -> str`
                       (las 10 categorías del filtro de `/audit`) +
                       `label(action) -> str` (Tanda 2, R6 — traducción al
                       español; sin entrada explícita muestra el código
                       tal cual, nunca "undefined"; `report.{name}`
                       dinámico cae en un genérico "Corrió el reporte
                       '{name}'"). Todo puro, sin I/O. `KNOWN_ACTIONS` es
                       el registro de toda `action` que existe hoy en el
                       código — `test_audit_catalog.py` escanea el código
                       fuente y falla si aparece una `action` nueva sin
                       sumarla acá (y sin traducirla en `_LABELS`, salvo
                       las `report.*` dinámicas), para que no quede mal
                       clasificada/sin traducir en silencio (una acción no
                       listada usa el default seguro: `classify` ->
                       `"cambio"`, `category` -> `"Administración"`). Ver
                       docs/specs/done/audit-page.md.
    context.py        request_id_var (ContextVar) + current_request_id().
    logging.py        configure_logging(): un handler en el root, formato
                       JSON o consola (PAM_LOG_FORMAT auto|json|console),
                       RequestIdFilter, alinea uvicorn. Se llama en el
                       lifespan.
    secrets.py        get_secret(ref) — descifra secrets/secrets.age con
                       el binario `age` (subprocess). available() para /health.
    cache.py          get_json/set_json sobre Redis (redis.asyncio), tolerante
                       a Redis caído. Sólo métricas agregadas, TTL corto —
                       excepto la caché opt-in de `app.api.itr`
                       (2026-09-18), que SÍ guarda metadata cruda de
                       cuentas (nunca secretos) para testear cambios al
                       reporte ITR sin repegarle a CyberArk.
    arq.py            (2026-09-24) `get_arq_pool()` — pool ARQ lazy-singleton
                       para encolar jobs on-demand desde la API (Batch 4 de
                       Reportes), mismo patrón que `cache.py`. Distinto del
                       pool que usa el propio worker (arq lo arma solo desde
                       `WorkerSettings.redis_settings`).
    csv_export.py     to_csv(rows, columns) + helpers (dotted / epoch_iso /
                       flag). Lo usa app/api/exports.py.
    writeop.py        run_write(): motor común de las escrituras de Fase 2 y
                       Fase 3 — reason obligatoria (el gating por rol ya lo
                       resolvió la dependencia del endpoint antes de llegar
                       acá; run_write no vuelve a chequearlo),
                       rate-limit (Redis), circuit breaker, dry-run+diff,
                       ejecución, audit con before/after. Cada op le pasa
                       fetch/build_plan/apply. Dataclass Plan
                       (`confirm_phrase`: Fase 3 — si no es `None`, ejecutar
                       exige que el `confirm` del body lo iguale
                       exactamente; se valida antes de tocar CyberArk;
                       `None` en las 7 ops de Fase 2, sin cambios).
                       Con `confirm_phrase` y un operador que no es
                       `platform_admin`, en vez de ejecutar crea una
                       `ApprovalRequest` (ver `approvals.py` abajo); con el
                       header `X-PAM-Approval-Id` ejecuta como aprobación
                       si el plan recalculado coincide.
    approvals.py      (2026-09-28) aprobación de un segundo operador:
                       `requires_approval`, `create_request`,
                       `load_for_execution` (otro operador, `approver+`,
                       mismo método/path/feature/target/`changes`),
                       `plan_digest`. Ver `docs/specs/dual-approval.md`.
    backups.py        Fase 3, `account.delete`: encrypt_secret/decrypt_secret
                       (binario `age`, keypair PROPIO — PAM_BACKUPS_AGE_KEY/
                       _RECIPIENT — separado del secret store de tenants) +
                       save_account_backup (cifra y COMMITEA su propia fila
                       ANTES de que el endpoint borre en CyberArk; si el
                       borrado falla después, el backup igual queda) +
                       list_backups/get_backup (scopeados por tenant_id).
    notify.py          (2026-09-30) `send(subject, body)` — canal de
                       notificaciones de las reglas de alerta (Fase 5).
                       Hoy sólo email (SMTP, `smtplib` stdlib, sin
                       dependencia nueva) — nunca lanza (un fallo de
                       envío se loguea y sigue, mismo criterio que
                       `cache.py` ante Redis caído). Sin
                       `PAM_ALERTS_SMTP_HOST`, no hace nada salvo
                       loguear. Diseñado plugeable para sumar Slack/
                       Teams después sin tocar `app/jobs/alerts.py`. Ver
                       docs/specs/done/alert-rules.md.
    seed.py           upsert idempotente desde seed/tenants.json.
    tenants.py        tenant_client(id, session) = ctx manager: registro +
                       secret store + cliente autenticado, cierra al salir.
                       alero_client(id, session) (2026-09-21) — mismo
                       patrón, para `AleroClient`: 400 si el tenant no
                       tiene `alero_tenant_id`, 503 si falta la sub-clave
                       `"alero"` en el secret store del tenant.
    security.py       Argon2 (hash/verify_password), TOTP (pyotp) + QR del
                       otpauth:// (segno, data URI SVG), JWT HS256 access +
                       refresh (typ:access/refresh, make/read_*token). Sin FastAPI.
    rbac.py           RBAC scopeado: effective_role(op,tenant), 
                       accessible_tenant_ids, audit_tenant_ids, ROLE_RANK.
                       platform_admin global = acceso total; el resto sale de
                       role_assignments (deny-by-default).
    deps.py           current_operator (JWT access) · require_role(min) — rol
                       de PLATAFORMA · require_tenant_access(min) — rol EFECTIVO
                       sobre el tenant del path · require_audit_reader.
    inventory_sync.py  (2026-09-16) `sync_accounts(tenant)` — copia local en
                       Postgres (`AccountSnapshot`, migración `0009`) de la
                       metadata de cuentas de un tenant (nunca secretos,
                       sólo lo ya visible en el listado), para poder
                       filtrar por columna server-side en la grilla de
                       Cuentas (la API Gen2 de CyberArk sólo filtra
                       server-side por safeName/modificationTime). Pagina
                       TODO el tenant (mismo criterio de corte que
                       `client.get_all`), upsert + borra huérfanas. TTL
                       corto (no un cron — eso es Fase 5 "Jobs
                       programados"). Ver
                       `docs/specs/done/inventory-sync-postgres.md`.
  app/api/
    health.py         /health (db + secret_store), /version        (abierto)
    auth.py           POST /auth/login (password + TOTP, anti-reutilización
                       vía `Operator.totp_last_step`), POST /auth/refresh
                       (cookie HttpOnly + header `X-PAM-Refresh`; refresh de
                       un solo uso, rota por familia, reutilización fuera
                       de 30 s revoca la familia; vence a los 60 min sin
                       uso y a las 12 h desde el login), GET /auth/me,
                       POST /auth/logout?scope=all|current, POST
                       /auth/change-password. Access token 15 min, sólo en
                       memoria del navegador. Ver
                       `docs/specs/done/session-hardening.md`.
    operators.py      CRUD de operadores + GET/PUT /operators/{id}/assignments
                       (asignaciones RBAC por tenant) — solo platform_admin,
                       todo auditado. Alta/reset dejan must_change_password=True.
    tenants.py        GET /tenants + GET /tenants/{id} (incluyen `my_role` =
                      rol efectivo del que consulta) + GET/PUT /tenants/{id}/
                      compliance-settings **(2026-09-17)** — umbral de días
                      para `disabled_stale` del Dashboard (ver
                      `app/analysis.py`), default 30. GET en `tenant_admin+`
                      (no gatea ninguna escritura contra CyberArk, es sólo
                      un criterio de reporte) y PUT en `platform_admin`,
                      auditado `tenant.compliance_settings.update` +
                      GET/POST/DELETE `/tenants/{id}/logo`
                      **(2026-09-18)** — logo del cliente para el header
                      del reporte ITR (`client_logo`/
                      `client_logo_content_type`, bytes en la DB,
                      migración `0011`; PNG/JPEG, 2 MB). GET `viewer+`
                      (sólo branding), POST/DELETE `platform_admin`
                      (config del tenant, no una operación sobre CyberArk),
                      auditado `tenant.logo.update`/`.delete`.
                                                        (require_tenant_access)
    inventory.py      GET /tenants/{id}/{accounts,safes,platforms,users,groups,
                      server,sessions,components} + /accounts/{id}(/activities)
                      + /safes/{id}/members + /groups/{id}/members (target=id)
                      + /safe-names (nombres para el filtro de Cuentas)
                      + /safes/defaults (CPM y retención más frecuentes
                      entre las safes existentes, `suggest_safe_defaults`,
                      precarga safe.create) + /cpm-names (CPMs distintos
                      entre las safes existentes, `list_cpm_names` — no hay
                      API de CyberArk que los liste, se infiere; alimenta
                      `CpmField.tsx` en `safe.create`/`safe.update`) —
                      las tres comparten `safes_rows_cached()` (caché Redis
                      de la lista completa de Safes, `PAM_SAFES_CACHE_TTL`,
                      120s; `safe.create`/`safe.update` la invalidan al
                      escribir). `platforms`
                      pasa por `platforms_rows_cached()` (mismo patrón,
                      `PAM_PLATFORMS_CACHE_TTL`, 300s) — alimenta también
                      el paso "Tipo de sistema" del wizard de
                      `account.create`, que antes pegaba en vivo a
                      `/API/Platforms` (payload pesado) en cada apertura.
                      `_cached_rows()` es el helper genérico que usan ambas.
                      `platforms_rows_cached()` además le agrega
                      `psmServerId` a cada fila (desde
                      `client.list_target_platforms()`, tolerante a que
                      falle) para pintarlo directo en el tab Plataformas sin
                      un fetch por plataforma; `platform.psm.update` invalida
                      esta caché al ejecutar.
                      + /member-search?q= (usuarios/grupos del Vault +
                      usuarios de Identity, para "agregar miembro a safe";
                      auditado member.search, tolerante a fuente caída)
                      + /users/{id} (un usuario puntual, auditado user.get)
                      + /platforms/{id}/psm (PSM asignado a una plataforma
                      puntual — el `GET /platforms` genérico no lo trae, usa
                      `client.list_target_platforms()`/"Get target
                      platforms") + /psm-servers (PSM configurados en el
                      tenant, reusa `client.list_psm_servers()` — mismo
                      patrón que /cpm-names, alimenta `PsmServerField.tsx`).
                      accounts y safes: paginación server-side
                      (?search&sort&offset&limit, devuelven count total).
                      accounts además ?safe_name (filter=safeName eq) y
                      ?modified_after (filter=modificationTime gt) — lo único
                      que la API Gen2 filtra server-side. _run() maneja
                      tuple(rows,total) + `extra` para el detail del audit.
                      **(2026-09-16)** `accounts` gana `?platform_id`/
                      `?user_name`/`?address` opcionales: sin ellos sigue en
                      vivo contra CyberArk (sin cambio de comportamiento);
                      con alguno, sirve desde `AccountSnapshot` (ver
                      `app/core/inventory_sync.py` más abajo) — la API Gen2
                      no filtra server-side por esas columnas, y la grilla
                      de Cuentas corre en modo servidor por volumen (miles
                      de cuentas), a diferencia de Plataformas/Usuarios/
                      Grupos (modo cliente, filtran gratis). Ver
                      `docs/specs/done/inventory-sync-postgres.md`.
                      **(2026-09-28)** `accounts` gana `?status=failure|
                      manual|cpm_disabled` (también desde `AccountSnapshot`,
                      filtrado en Python sobre `raw` con
                      `app.domain.accounts.account_status_flags`) + `GET
                      /accounts/summary` (conteos por estado para los chips
                      de la grilla, auditado `account.summary`).
                      + /accounts/{id}/password-versions **(2026-09-16)**
                      — historial de versiones de contraseña
                      (`client.list_password_versions`, metadata sin el
                      valor — fecha/modificado por), alimenta la pestaña
                      "Versiones" de `AccountDetailPanel.tsx`.
                      `require_tenant_access(tenant_admin)` (mismo criterio
                      que listar backups de cuentas borradas). Revelar el valor de una
                      versión puntual (`account.reveal.history`) vive en
                      `writes.py`, no acá. Ver
                      `docs/specs/done/account-password-versions.md`.
                                              (require_tenant_access viewer)
    discovery.py      (2026-09-22, reescrito el mismo día contra la doc
                      oficial) Cuentas pendientes descubiertas por
                      CyberArk Discovery (Gen2, Privilege Cloud/ISPSS —
                      `docs.cyberark.com/manage/latest/en/content/
                      discovery/discovery-apis/discovery-account-
                      discovery.htm`), aún sin onboardear al Vault.
                      `GET /discovered-accounts` + `GET .../{id}/
                      activities` son lecturas (`_run`, `viewer+`).
                      Onboard/ignore/restore/delete son escrituras reales
                      contra CyberArk → `writeop.run_write`, `operator+`
                      (onboardear es riesgo equivalente a `account.create`;
                      ignorar/restaurar/borrar son reversibles, ninguna
                      necesita confirmación reforzada). El endpoint real de
                      onboarding es `POST .../discovered-accounts/{id}/
                      onboard` — dedicado, NO reusa `account.create` (el
                      primer corte de esta feature sí lo hacía, con un
                      body que ni siquiera coincidía). La API vive en el
                      dominio base del tenant SIN `/PasswordVault`
                      (`CyberArkClient._discovery_url()` en
                      `app/cyberark/client.py`), no en un Gen1 adivinado.
                      Ver `docs/specs/done/discovery.md` (incluye la
                      historia completa de los 3 bugs del primer corte:
                      parseo de la respuesta con nombres de campo
                      equivocados que dejaba la grilla siempre vacía,
                      onboard a una ruta inexistente, y un prefix de
                      router duplicado que rompía el arranque de la app —
                      ninguno se detectó antes porque el primer corte no
                      tenía tests).
    identity.py       (2026-09-15) Usuarios/roles de **Identity/SSO**
                      (distinto de `/users`/`/groups` arriba, que son del
                      Vault) — `GET /identity-users` (lista vía Redrock,
                      `SELECT ID, Username, DisplayName, Email, Status FROM
                      User`) + `GET /identity-users/{id}` (detalle,
                      `client.identity_get_user_info` +
                      `identity_is_user_locked` en paralelo) + `GET
                      /identity-roles` (lista vía Redrock, `SELECT ID, Name,
                      Description FROM Role`) + `GET /identity-roles/
                      {role_id}/members` (2026-09-28, `client.
                      identity_role_members` — `Roles/GetRoleMembers?name=
                      <id>`, sin confirmar contra un tenant real) +
                      `.../candidates?q=` (buscador para agregar). Agregar
                      miembros (`identity_role.member.add`, `operator+`,
                      `Roles/UpdateRole`) vive en `writes.py`. Todo
                      `require_tenant_access(viewer)`; bloquear/desbloquear
                      un usuario vive en `writes.py`. Ver
                      `docs/specs/done/identity-users.md`.
    alero.py          (2026-09-21) Espacio "Alero" — CyberArk Remote Access
                      (acceso remoto de terceros/proveedores), API propia
                      (`api.alero.io`), nada del Vault ni de Identity
                      (mismo criterio que `identity.py`). Cuatro recursos
                      DISTINTOS de la misma API, todo sólo lectura
                      `require_tenant_access(viewer)`:
                      `GET /alero-vendors` (+ `/{vendor_id}`) — cuentas de
                      terceros que se autentican vía Remote Access (218 en
                      Globex); `GET /alero-users` (+ `/{user_id}`) —
                      personas del equipo propio con la app móvil
                      (Identities > Users del portal, recurso DISTINTO de
                      Vendors), rol TenantAdmin/User/VendorManager; `GET
                      /alero-roles` — "Identity roles" del portal, en la
                      API son "groups" de tipo `IdaptiveRole` (roles
                      importados de CyberArk Identity) filtrados de los
                      `ProvisionedByAlero` (grupos nativos de Alero, esa
                      pantalla no los muestra); `GET /alero-activities?
                      hours=` — feed de auditoría de Alero (login,
                      altas/bajas, cambios de config; `activityTypes` es
                      obligatorio en la API de Alero, se manda el enum
                      completo — `ALL_ACTIVITY_TYPES` en
                      `app/alero/client.py` — para replicar el feed sin
                      filtro del portal; `_ACTIVITY_LABELS` traduce cada
                      tipo a un verbo legible, `OfflineAccessUserViewedPassword`
                      —el evento más sensible del feed para un tenant
                      PAM— muestra cuenta+safe como detalle). El detalle de
                      vendor/usuario resuelve `applications` (pares
                      siteId/applicationId sin nombre) contra
                      `list_sites()`/`list_site_applications()`, y
                      `invitedBy` de un usuario (un ID crudo, a diferencia
                      del de un vendor que ya trae el nombre) contra
                      `list_users()` — se omite si no aparece (usuario
                      borrado/inactivo) en vez de mostrar un ID sin
                      sentido. Auditado (`alero_vendor.list`/`.get`,
                      `alero_user.list`/`.get`, `alero_role.list`,
                      `alero_activity.list`) con un `_run_alero` propio
                      (mismo patrón que `app.api.inventory._run`, para la
                      jerarquía de errores de `AleroError`).
                      `idaptiveRoles`/`groups` vienen de la API como
                      string separado por comas cuando hay varios (nunca
                      lista) — se normalizan en el backend (`_as_list`) a
                      `string[] | null`, nunca en el frontend. Ver
                      `docs/specs/done/alero-vendors.md`.
    sia.py            (2026-10-06) Espacio "SIA" (Secure Infrastructure
                      Access), sólo lectura, `viewer+`: `GET /tenants/{id}/
                      sia/policies` (+ `/{policy_id}?source=uap|legacy&
                      kind=vm|db`), `/sessions?status=active|recent` (+
                      `/{session_id}` con actividades), `/strong-accounts`
                      (cuentas fuertes de VM con sus target sets y de DB
                      con sus bases), `/settings`. Cada servicio vive en su host
                      (`{sub}.dpa|uap|sessionmonitoring.cyberark.cloud`,
                      derivado de `sia_connector_url` por
                      `isp_service_url` en `client.py`), mismo bearer
                      token. Políticas: UAP primero y, si no hay, las rutas
                      legado de `dpa`. Partes independientes (DB/VM) se
                      piden por separado: si una falla vuelve en
                      `warnings`, sólo si fallan todas hay error. Secretos
                      sólo por lista blanca (`app/domain/sia.py`) y sin
                      caché. 409 sin `sia_connector_url`; 403 traducido a
                      "SIA no está licenciado o falta el rol SIA Admin".
                      Ver `docs/specs/done/sia-read-only.md`.
    recordings.py     (2026-09-16) Grabaciones de sesiones PSM YA
                      TERMINADAS (distinto de `inventory.py` `/sessions`,
                      que sólo lista sesiones EN VIVO) — `GET /recordings`
                      (`?from_time&to_time&safe&search`, default últimas
                      48h) + `GET /recordings/{session_id}/activities`,
                      ambos `viewer+` (metadata). `POST
                      /recordings/{session_id}/play` (descarga el archivo)
                      es `tenant_admin+` + `reason` obligatoria — mismo
                      techo que `account.reveal`, el video puede mostrar
                      contraseñas tipeadas en pantalla. No pasa por
                      `writeop.run_write` (no hay dry-run posible), mismo
                      criterio que `account_reveal`. El identificador real
                      es `SessionID`, no `RecordingID` (nombre que sólo
                      usaba `psPAS`) — confirmado contra la doc oficial de
                      CyberArk (`docs.cyberark.com/snapshot/
                      privilege-cloud-secrets-rotation`, que sí responde al
                      fetch, a diferencia del portal genérico). Ver
                      `docs/specs/done/session-recordings.md`. **Gotcha real
                      probando contra Globex (2026-09-17):**
                      `client.play_recording` necesita
                      `data={"isVideo": "true"}` (body
                      `application/x-www-form-urlencoded`, NO JSON) — la
                      doc oficial dice "Body parameters: None", pero es
                      incompleta; sin ese parámetro CyberArk devuelve el
                      componente de TEXTO/keystrokes de la grabación en
                      vez del video, aun con `VideoSize` > 0. El video es
                      un AVI con códec propietario **SCPR (ScreenPressor)**
                      — no lo decodifica VLC estándar; según CyberArk
                      KB000003904 se instala desde los archivos de PSM o
                      `infognition.com/ScreenPressor`, reproduce con
                      Windows Media Player Classic/MPC-HC/MPV. El nombre
                      de archivo NO debe salir de `RecordingFiles[0]`
                      (es el nombre del componente de texto, no el del
                      video) — se usa `${sessionId}.avi` fijo.
    dashboards.py     GET /tenants/{id}/dashboard[?refresh=1] — KPIs + series.
                      Cache miss: accounts+safes+platforms + 4 savedFilters de
                      compliance + 4 componentes, en paralelo. Cachea el
                      resultado en Redis (PAM_DASHBOARD_CACHE_TTL, def 900s).
                      Audita dashboard.view SIEMPRE (también cache hit).
                                                                    (rol viewer)
    compliance.py     (2026-09-17) `GET /tenants/{id}/compliance/accounts?
                      reason=<PolicyFailures|DisableByUser|DisableByCPM|
                      Locked|DisabledStale|ActiveOverdue>` — drill-down
                      navegable de `compliance_breakdown` del Dashboard
                      (que sólo agrega). Los primeros 4 reusan `client.
                      saved_filter_accounts()` (mismo savedFilter que ya
                      cuenta el Dashboard); `DisabledStale`/`ActiveOverdue`
                      no tienen savedFilter propio, se derivan del listado
                      completo con `analysis.disabled_stale_rows`/
                      `active_overdue_rows` — vencida contra la política
                      de SU plataforma (`analysis.platform_policy_days`,
                      de `GET /API/Platforms`) o el default del tenant si
                      no define una. `GET .../accounts/{id}/status`
                      (mismo día) — compliance de UNA cuenta puntual,
                      alimenta la `ComplianceCard` del detalle de cuenta;
                      `analysis.account_compliance()` es la única fuente
                      de verdad, compartida con `compliance_breakdown`.
                      RBAC viewer+, **sin caché propia a propósito**
                      (drill-down puntual, prioriza el dato más fresco).
                      Se llega clickeando el KPI Compliance o el desglose
                      del Dashboard. Ver "Estado actual" más abajo para el
                      detalle completo del cruce con la política de
                      plataforma.
                                                (require_tenant_access viewer)
    exports.py        GET /tenants/{id}/export/{accounts,safes,platforms,users,
                      groups} — CSV (dataset completo, columnas curadas),
                      auditado <recurso>.export.       (require_tenant_access)
    reports.py        GET /reports (catálogo) + GET /tenants/{id}/reports/
                      {name} ({columns,rows,meta}, caché Redis + ?refresh) +
                      .../export (csv|json). Auditado report.<name>.
                      `heavy=True` -> `501` **sólo si no hay nada
                      cacheado todavía** (2026-09-24) — antes era
                      siempre 501; ahora, con Batch 4 corriendo como job
                      ARQ, sirve el resultado cacheado por el job igual
                      que un reporte síncrono. Para un `heavy` sin nada
                      cacheado: `POST .../reports/{name}/run-job` encola
                      el job (`app.jobs.reports.run_heavy_report`) y
                      devuelve `{job_id}`; `GET .../jobs/{job_id}`
                      consulta el estado (`queued`/`in_progress`, o
                      `complete` con el resultado) vía `app.core.arq.
                      get_arq_pool` — ver `docs/specs/done/
                      batch4-reports.md`. `_compute()`
                      (2026-09-22) especial-casea `license-candidates`: si
                      el tenant tiene `alero_tenant_id`, abre un
                      `alero_client` aparte y llama a
                      `app.reports.license_candidates.enrich_with_alero()`
                      después de `report.run()` — best-effort, si Alero
                      falla el reporte se sigue devolviendo con el login
                      de Identity como respaldo (`meta.alero_enriched =
                      False`).                              (rol viewer)
    writes.py         **Fase 2.** POST /tenants/{id}/accounts/{id}/
                      {update,change,verify,reconcile} + POST /tenants/{id}/
                      safes/{safe_id}/members (agregar miembro) + POST
                      /tenants/{id}/users/{user_id}/activate (reactivar
                      usuario suspendido) + POST /tenants/{id}/accounts/{id}/
                      link (vincular logon/reconcile; audit action account.link)
                      + POST /tenants/{id}/accounts (alta, wizard estilo
                      PVWA; audit action account.create) + POST /tenants/{id}/safes
                      (alta; audit action safe.create) + POST /tenants/{id}/
                      accounts/bulk-update (editar el mismo campo a varias
                      cuentas con UN valor igual para todas — tilde-múltiple
                      estilo PVWA clásico; mismo audit action account.update,
                      reusa build_account_plan por cuenta, ver
                      `build_account_bulk_plan` en app/domain/accounts.py).
                      `EDITABLE_TOP_LEVEL` incluye `platformId` (verificado
                      contra la doc oficial de Update Account — no estaba al
                      principio por cautela, no por límite de la API) con
                      warning propio; `safeName` NO — mover de safe no está
                      en las "Properties that can be updated" de esa API, el
                      PVWA clásico lo hace con un servicio `.asmx` interno
                      sin Bearer token, fuera de alcance (ver
                      docs/specs/done/account-change-platform.md).
                      update = editar metadata (JSON Patch + diff); change/
                      verify/reconcile = disparar el CPM; member/activate/
                      link/create(ambos) = ops de acción/alta con
                      Plan.effect. Body {changes?, dry_run, reason}. Todo por
                      writeop.run_write; RBAC operator+. account_create:
                      `fetch` valida que la safe y la plataforma existan
                      (get_safe / get_platform_details — este último NO
                      expone el esquema de campos del Add Account, sólo
                      confirma existencia, ver la nota en
                      app/domain/accounts.py) y levanta 422 (sin auditar,
                      igual criterio que la validación de `reason`) si se
                      deshabilita la gestión automática sin
                      `manual_management_reason` — el secreto, si vino, sólo
                      lo lee `build_account_create_body`, nunca
                      `plan_account_create` (nunca aparece en diff/audit).
                      El wizard también manda
                      `secretManagement.automaticManagementEnabled` ("Allow
                      automatic password management") y `remoteMachinesAccess`
                      ("Remote Machine Access") — son campos del Account, no
                      propiedades de plataforma. "Additional Properties" es
                      una lista libre clave/valor que arma el operador (con
                      sugerencias de nombres comunes por tipo de sistema
                      sólo en el frontend, nunca leídas de CyberArk).
                      **Fase 3:** PUT/DELETE /tenants/{id}/safes/{safe_id}/
                      members/{member_name} (`safe.member.update` /
                      `safe.member.remove`) — RBAC `approver+` (no
                      `operator+`) y confirmación reforzada: `Plan.
                      confirm_phrase` (el `memberName` real) debe coincidir
                      con `confirm` del body para ejecutar (chequeo en
                      `writeop.run_write`, antes de tocar CyberArk). El
                      `member_name` de la URL es la fuente de verdad, no va
                      duplicado en `changes`. Probado contra Globex
                      (2026-09-12). Ver `docs/specs/done/safe-member-edit.md`.
                      **Fase 3, `safe.update`:** `PUT /tenants/{id}/safes/
                      {safe_id}` — description/managingCPM/retención/OLAC.
                      Mismo RBAC `approver+` + confirmación reforzada
                      (`confirm` = `safeName`). El `apply` reusa el `current`
                      que ya trajo el `fetch` (closure `fetched["safe"]`, sin
                      un segundo `GET /API/Safes/{id}`) porque
                      `safe_update_body` necesita el objeto completo — la
                      API real de Update Safe no acepta un patch parcial.
                      Invalida `safes_rows_cached` al ejecutar (el CPM puede
                      cambiar). Probado contra Globex (2026-09-12). Ver
                      `docs/specs/done/safe-update.md`.
                      **Fase 3, `account.delete`/`safe.delete`:** `DELETE
                      /tenants/{id}/accounts/{id}` — el `apply` recupera la
                      contraseña vigente (`client.retrieve_password`, mismo
                      `reason` del body), guarda un `AccountBackup` cifrado
                      (`app.core.backups.save_account_backup`, commitea
                      ANTES de borrar) y recién ahí
                      `client.delete_account`. `DELETE /tenants/{id}/safes/
                      {id}` — el `fetch` cuenta las cuentas de la safe
                      (`accounts_page(..., limit=1)`, usa el `total` de la
                      paginación) y levanta 422 sin auditar si no está
                      vacía, antes de tocar CyberArk. Mismo RBAC
                      `approver+` + confirmación reforzada (`confirm` =
                      `name`/`safeName`) que el resto de Fase 3. Ver
                      `docs/specs/done/account-safe-delete.md`.
                      **`account.move`** (2026-09-22): `POST /tenants/{id}/
                      accounts/{id}/move` — mueve una cuenta a otra Safe.
                      CyberArk no expone un "Move" real vía API con Bearer
                      token (el PVWA clásico usa `AccountsMgt.asmx/Move`,
                      autenticado con la sesión de navegador del operador —
                      ver `docs/specs/done/account-change-platform.md`), así
                      que es un workaround: retrieve + `create_account` en
                      el safe destino + `delete_account` de la original.
                      Pierde el historial nativo de CyberArk (versiones de
                      contraseña, actividad) y los vínculos Logon/Reconcile
                      — por eso RBAC **`platform_admin`**, el techo más alto
                      de PAM Web Console (más que el `approver+` del resto de Fase 3).
                      Antes de borrar la original se guarda un
                      `AccountBackup` cifrado, mismo criterio que
                      `account.delete` — nunca se borra sin backup. Si el
                      alta en el destino falla, no se tocó nada; si el alta
                      funciona pero el borrado de la original falla, se
                      revierte automáticamente borrando la copia recién
                      creada (para no dejar dos copias vivas de la misma
                      credencial); si el rollback también falla, se audita
                      el estado completo (`action=account.move.
                      rollback_failed`, con el id de la copia nueva) antes
                      de cortar con un error explícito — mismo patrón que
                      `platform.psm.update`. Confirmación reforzada
                      (`confirm` = el `name` real de la cuenta).
                      `AccountEditDialog.tsx`: cambiar el Safe es una
                      operación aparte de "editar metadata" — mientras el
                      Safe elegido difiera del original, el resto del
                      formulario queda deshabilitado (nunca se combinan los
                      dos endpoints en un mismo request; antes de este fix,
                      cambiar el Safe Y otro campo a la vez descartaba el
                      otro campo en silencio). Implementado originalmente
                      con Antigravity (otro agente) y corregido en esta
                      sesión: le faltaban el backup antes de borrar, el
                      rollback, la separación de los dos flujos en el
                      frontend, `confirm_phrase`, y tenía texto con
                      acentos corrompidos en los warnings. 11 tests nuevos
                      (`test_account_move.py`, incluye los dos escenarios
                      de rollback). **Warnings (mismo día):** Alex pasó una
                      captura real del diálogo nativo "Move Account" de
                      **Privilege Cloud** (Accounts View Classic UI, no el
                      PVWA on-prem clásico de `account-change-platform.md`
                      — son hallazgos distintos) — los 9 puntos que lista
                      CyberArk ahí se portaron tal cual (traducidos) como
                      `warnings` de `build_account_move_plan`, en vez de
                      los 4 inferidos originalmente. Confirma algo
                      importante: el Move NATIVO de CyberArk **también**
                      pierde versiones de contraseña e historial de
                      actividad — no es una limitación exclusiva de este
                      workaround por clonación, es inherente al modelo de
                      datos de CyberArk. **Investigación cerrada, mismo
                      día:** Alex capturó con DevTools la llamada real que
                      dispara el botón "Move Account" nativo de Privilege
                      Cloud contra Globex —
                      `POST .../PasswordVault/services/AccountsMgt.asmx/
                      Move`, **sin header `Authorization`**, autenticado
                      entero por cookies de sesión del navegador
                      (`ASP.NET_SessionId` + `idToken` de Identity +
                      `XSRF-TOKEN`/`__AntiXsrfToken`). Confirma que
                      Privilege Cloud usa el mismo servicio legacy sin
                      Bearer token que ya se había concluido para el PVWA
                      on-prem clásico — PAM Web Console no puede llamarlo (su modelo
                      es un `CyberArkClient` nuevo por request con OAuth2
                      Bearer del service account, no una cookie jar de
                      sesión de un operador). **No hay endpoint REST real
                      que reemplace el workaround de clonar+borrar** — se
                      descarta la idea, `account.move` se queda como está.
                      **Probado contra Globex (2026-09-24), primera vez
                      — 2 bugs reales encontrados y corregidos en el
                      camino, ninguno del propio `account.move`:**
                      `SafeField.tsx` (el combobox de Safe destino)
                      pegaba a `/safes/names` en vez de `/safe-names`,
                      405 de siempre (nunca se había usado este selector
                      hasta ahora); y el diálogo de confirmación base
                      (`ui/dialog.tsx`) no tenía `max-height`/scroll — con
                      los 9 warnings reales de `account.move` el
                      contenido se salía de la pantalla, sin forma de
                      llegar al botón "Confirmar" (fix a nivel del
                      componente base `ui/dialog.tsx`, no sólo
                      `DryRunDialog`). Con los dos fixes aplicados, el
                      movimiento (backup + clonar + borrar) **funcionó de
                      punta a punta** — cerrado.
                      **Mover VARIAS cuentas de una (`account.move.bulk`,
                      2026-09-22, mismo día)** — Alex: la vista clásica del
                      PVWA permite tildar varias cuentas y moverlas juntas
                      con la misma advertencia; PAM Web Console no lo tenía. `POST
                      /accounts/bulk-move/preview` (sin tocar CyberArk, un
                      diff por cuenta vía `build_account_move_plan`) +
                      `POST /accounts/bulk-move` (ejecuta). Extrae
                      `_move_account()` como función compartida entre el
                      move individual y el de a varias — el mismo
                      retrieve+backup+create+delete+rollback de una
                      cuenta, sin duplicar esa lógica. **No pasan por
                      `writeop.run_write`** (mismo criterio que
                      `account.create.bulk`/`account.reveal.bulk`: cada
                      cuenta es independiente, una que falla no aborta el
                      lote — ya tiene su propio backup+rollback a nivel de
                      UNA cuenta, así que un fallo puntual no deja el
                      resto a mitad de camino). Se mueven **una por una,
                      secuencial, nunca con `asyncio.gather`** — a
                      diferencia del resto de los endpoints bulk de PAM Web Console:
                      cada cuenta hace sus propios `session.commit()`
                      (backup, y el audit de rollback si hace falta), y
                      correrlos en paralelo sobre el mismo `AsyncSession`
                      no es seguro (no es thread/task-safe para uso
                      concurrente). Sin `Plan.confirm_phrase` (no aplica a
                      N objetos): confirmación manual, `confirm` debe
                      repetir el nombre exacto de la Safe destino. Tope de
                      cuentas por lote: reusa `PAM_WRITE_OBJECT_CAP`
                      (default 25), el mismo que el circuit breaker de
                      `account.update`/`bulk-update`. RBAC `platform_admin`,
                      igual que el move de una cuenta. Frontend:
                      `AccountBulkMoveDialog.tsx` (mismo patrón de
                      preview→tabla de resultados→confirmar que
                      `AccountBulkCreateDialog.tsx`, no el genérico
                      `DryRunDialog` — la forma de la respuesta no es la
                      de `writeop.run_write`), botón "Mover N cuenta(s)"
                      en el toolbar de Cuentas junto a "Editar N
                      cuenta(s)", visible sólo si `roleAtLeast(my_role,
                      "platform_admin")`. 11 tests nuevos
                      (`test_account_move.py`, 22 en total con los del
                      move individual). Backend (ruff+format+mypy+pytest)
                      y frontend (lint+tsc+build) verdes. Pendiente probar
                      contra un tenant real.
                      **`account.reveal`:** `POST /tenants/{id}/accounts/
                      {id}/reveal` — revela la contraseña VIGENTE de una
                      cuenta activa (`client.retrieve_password`). No pasa
                      por `writeop.run_write` (no hay dry-run: revelar ES
                      la acción) — repite a mano el gating de feature +
                      `reason` + auditoría. RBAC `tenant_admin+`
                      (dependencia `_tenant_admin`, el techo más alto de
                      PAM Web Console — más que `approver+`, porque expone un secreto
                      real en uso, no uno histórico). Ver
                      `docs/specs/done/account-reveal.md`.
                      **`account.reveal.history`** (2026-09-16): `POST
                      /tenants/{id}/accounts/{id}/reveal-version` — revela
                      el valor de una versión HISTÓRICA puntual de la
                      contraseña (`client.retrieve_password(...,
                      version=N)`, mismo endpoint de CyberArk que
                      `account.reveal` con un `Version` opcional). Feature
                      propia (separada de `account.reveal`: mismo nivel de
                      riesgo, radio de exposición distinto — mismo criterio
                      que `account.reveal.bulk`), mismo RBAC
                      `tenant_admin+` + `reason`, tampoco pasa por
                      `writeop.run_write`. Alimenta la pestaña "Versiones"
                      de `AccountDetailPanel.tsx` (lista en
                      `GET .../password-versions`, ver `inventory.py`
                      arriba). Ver `docs/specs/done/account-password-versions.md`
                      (cerrada — nombres de campo confirmados contra
                      Globex, 2026-09-18).
    backups.py        Fase 3: `GET /tenants/{id}/account-backups` (listar,
                      sin el secreto — `require_tenant_access(tenant_admin)`)
                      + `POST .../account-backups/{id}/reveal` (descifra
                      on-demand, exige `reason`, audita
                      `account.backup.reveal` sin el valor de la contraseña
                      — mismo RBAC `tenant_admin+`, NO pasa por
                      `writeop.run_write` porque no escribe en CyberArk) +
                      `POST .../account-backups/{id}/restore` (RBAC
                      `approver+`: recrea la cuenta reusando tal cual
                      `plan_account_create`/`build_account_create_body` de
                      Fase 2 — gateado por la feature `account.create`, SIN
                      una feature `account.restore` propia — con una
                      confirmación reforzada de "restaurar" chequeada a mano
                      en el endpoint, no vía `Plan.confirm_phrase`, para no
                      tocar `account.create`). Ver
                      `docs/specs/done/account-safe-delete.md`.
    credentials_backup.py  `POST /tenants/{id}/credentials-backup/export` —
                      backup cifrado de TODAS las cuentas del tenant (a
                      pedido de un cliente, antes de una limpieza masiva):
                      lista el inventario completo, recupera el secreto de
                      cada cuenta (`asyncio.gather`, la concurrencia real la
                      limita el semáforo interno del cliente) y devuelve un
                      ZIP protegido con contraseña (AES real vía
                      `pyzipper`, contraseña al azar por export, viaja en
                      el header `X-Backup-Password` de la misma respuesta
                      y nunca se guarda en PAM Web Console — pensado para entregarle
                      el archivo directo al cliente y la clave por un
                      canal separado; cambio del 2026-09-24, antes cifraba
                      con el keypair `age` de `account.delete`, que sólo
                      se puede descifrar en el server) con userName/
                      address/platformId/safeName/remoteMachines/secret +
                      `retrieveStatus` (OK/EMPTY/ERROR — una cuenta que
                      falla no aborta el resto, mismo criterio que
                      `Account_Secret_Backup.ps1`). Mismo RBAC
                      `tenant_admin+` + `reason` (audit action
                      `account.reveal.bulk`) que `account.reveal`, pero acá
                      el radio de exposición es todo el tenant de una vez.
                      **NO vive en `app.api.reports`** a propósito (ese
                      catálogo es siempre sin secretos, rol `viewer`,
                      cacheado en Redis) aunque la UI lo muestre dentro de
                      la página Reportes. Puede tardar varios minutos con
                      miles de cuentas — el nginx del server tiene
                      `proxy_read_timeout` extendido a 600s para `/api/`.
                      **Probado contra Globex (2026-09-24):** el ZIP
                      abre correctamente con la contraseña del header.
                      Ver `docs/specs/done/account-credentials-backup.md`.
                      **`account.change.vault_only` / `account.change.next`:**
                      `POST .../accounts/{id}/change-in-vault` ("Change
                      credentials in the Vault" del PVWA —
                      `client.update_password_in_vault`,
                      `POST /API/Accounts/{id}/Password/Update`, nunca toca
                      el target) y `POST .../accounts/{id}/set-next-password`
                      ("Set next password" — `client.set_next_password`,
                      `POST /API/Accounts/{id}/SetNextPassword` con
                      `ChangeImmediately: false`, el CPM lo usa en su
                      próximo cambio automático). Distinto de
                      `account.change` (Fase 2, `operator+`, el CPM elige
                      un valor aleatorio): acá el operador escribe la
                      contraseña, así que van con RBAC `tenant_admin+`
                      (mismo techo que `account.reveal`) — el valor nunca
                      aparece en diff/audit. Ver
                      `docs/specs/done/account-password-manual.md` (incluye cómo
                      se verificaron los endpoints exactos: docs.cyberark.com
                      bloquea el fetch automatizado, así que se confirmaron
                      con `Safe_Password_Reset.ps1` propio + el código
                      fuente del módulo open-source `psPAS`).
                      **`platform.psm.update`:** `PUT /tenants/{id}/
                      platforms/{platform_id}/psm` — reasigna el PSM de una
                      plataforma. El `apply` hace GET del objeto
                      `PrivilegedSessionManagement` completo -> muta sólo
                      `PSMServerId`/`PSMServerName` -> PUT -> releé y
                      compara con `app.domain.platforms.normalize_psm`; si
                      algo más cambió (bug conocido de CyberArk que borra
                      `PSMConnectors`/Connection Components al hacer PUT
                      parcial), revierte automáticamente con un segundo PUT
                      del objeto original, y si el rollback en sí falla,
                      audita `platform.psm.rollback_failed` con el objeto
                      original completo ANTES de responder el error. Mismo
                      RBAC `approver+` + confirmación reforzada (`confirm`
                      = `platform_id`) que el resto de Fase 3 — el mayor
                      blast radius de todos (afecta a TODAS las cuentas de
                      esa plataforma). Puerto de
                      `Platform_PSM_Migration.ps1` (ya probado en
                      producción). Ver `docs/specs/done/platform-psm-migration.md`.
                      **`platform.psm.update.bulk`** (2026-09-30, a pedido
                      de Alex — Globex va a parchear uno de sus dos PSM
                      y hay que migrar todas las plataformas que apuntan
                      ahí al otro): `_apply_platform_psm_update()` se
                      extrae del `apply` de arriba (mismo GET completo ->
                      mutar -> PUT -> verificar -> rollback automático),
                      compartida por el endpoint individual y por
                      `POST .../platforms/bulk-psm-update/preview` +
                      `POST .../platforms/bulk-psm-update`. Preview en
                      paralelo (sólo lecturas); ejecución **secuencial**
                      (no `asyncio.gather`, mismo motivo que
                      `account_bulk_move`: puede auditar+commitear un
                      `platform.psm.rollback_failed` por plataforma sobre
                      la misma `session`). Una plataforma que falla no
                      aborta el lote. Mismo RBAC `approver+` que el
                      individual (no sube a `platform_admin` — a
                      diferencia de `account.move.bulk`, acá cada
                      plataforma ya lleva su propia verificación/rollback,
                      el lote no suma riesgo estructural nuevo). Sin
                      `Plan.confirm_phrase` (no aplica a N objetos):
                      confirmación manual, `confirm` = el `psm_server_id`
                      destino exacto. Ver
                      `docs/specs/done/platform-psm-bulk-migration.md`.
                      **`identity_user.lock`** (2026-09-15): `POST
                      /tenants/{id}/identity-users/{user_id}/lock` y
                      `.../unlock` — bloquear/desbloquear un usuario de
                      **Identity/SSO** (`UserMgmt/SetCloudLock`), distinto
                      de `user.activate` (Vault). Dos endpoints en vez de un
                      body `{lock: bool}`, mismo criterio que
                      `platform.activate`/`deactivate` (para que el
                      `<DryRunDialog>` genérico con `changes={}` funcione
                      sin cambios). RBAC `operator+`, sin confirmación
                      reforzada. Ver `docs/specs/done/identity-users.md`.
                      **Fase 4, `account.create.bulk`** (2026-09-15):
                      `POST /accounts/bulk-create/preview` +
                      `POST /accounts/bulk-create` (multipart, CSV) —
                      onboarding masivo. **No pasan por `writeop.
                      run_write`** (N filas con resultado mixto no encaja
                      con el modelo de un objeto) — replican a mano el
                      gating (reusa la feature `account.create`, `reason`
                      obligatoria para ejecutar) y auditan una sola vez
                      por lote. `app/domain/accounts.py` `parse_bulk_csv`
                      arma un `changes`-dict por fila (mismo shape que
                      `AccountCreateChanges`); resuelven safe/plataforma
                      únicas y crean las cuentas en paralelo
                      (`asyncio.gather`, deduplicado). v1 síncrono, sin
                      cola de jobs — tope `bulk_create_max_rows` (100)
                      antes de tocar CyberArk. Ver `docs/specs/
                      account-bulk-onboard.md`.
    search.py         GET /search/{accounts,safes,platforms,users,groups}?q= — busca
                      en los tenants accesibles en paralelo (client nuevo por
                      tenant, audita <recurso>.search por tenant, un tenant que
                      falla no rompe la búsqueda).              (rol viewer)
    system_health.py  (2026-09-17) `GET /system-health` — equivalente
                      cross-tenant de "System Health" del PVWA (que sólo
                      puede mostrar UN tenant). Fan-out con el mismo
                      aislamiento que `search.py` (`_snapshot_for_fanout`,
                      client nuevo por tenant, nunca lanza) + `GET
                      /tenants/{id}/system-health` (usa `tenant_client`,
                      sí levanta ante un tenant caído — mismo criterio que
                      el resto del proyecto). Ambos comparten
                      `_compute_snapshot(client)` y la cache key Redis
                      (`pam:system-health:{tenant_id}`,
                      `PAM_SYSTEM_HEALTH_CACHE_TTL`, propia, no atada al
                      caché del Dashboard). Cuentas gestionadas/sesiones
                      activas/conectadas-por-componente salen de
                      endpoints YA confirmados (`list_accounts`,
                      `live_sessions`, `component_details` vía
                      `analysis.analyze_components`) — a propósito NO usa
                      los campos de "Managed Accounts"/"Active Users" de
                      `ComponentsMonitoringSummary.Components` (no
                      confirmados). El único dato nuevo es el estado del
                      **Vault** (`client.components_monitoring_summary()`,
                      campos `Role`/`IP`/`IsLoggedOn` confirmados vía el
                      código fuente de `psPAS`, ignora entradas `DR`).
                      Auditado `system_health.view` por tenant, ok y error
                      (un tenant caído no rompe el fan-out). Ver
                      `docs/specs/done/system-health.md`.
    vendor_status.py  (2026-09-30) `GET /vendor-status/cyberark` — estado
                      público de status.cyberark.com (el FABRICANTE, no un
                      tenant de PAM Web Console): ayuda a distinguir "esto anda mal en
                      la config del cliente" de "CyberArk tiene una
                      incidencia real en su SaaS" antes de escalar un
                      ticket. Recurso GLOBAL (sin tenant_id, mismo patrón
                      que `knowledge_base.py`), sólo exige estar
                      autenticado (`current_operator`, sin RBAC adicional —
                      es información pública, sin exposición de datos de
                      PAM de ningún cliente). La consulta real vive en
                      `app/vendor_status.py` (`fetch_summary`, httpx puro,
                      sin secret store). Cacheado en Redis
                      (`PAM_VENDOR_STATUS_CACHE_TTL`, 300s), auditado
                      SIEMPRE (`vendor_status.view`, también en cache hit,
                      mismo criterio que `dashboard.view`).
    itr.py            `POST /tenants/{id}/reports/itr/export` — el reporte
                      ITR mensual (antes sólo por CLI vía
                      `Telemetry-Reporter`), generado desde Reportes.
                      `tenant_admin+`, `reason` opcional; sin caché por
                      default (ver `use_cache` más abajo).
                      Orquesta el fetch (Accounts/Platforms
                      imprescindibles, el resto degrada) y llama a
                      `app.itr.report_builder.build_html_report` (en un
                      hilo, `asyncio.to_thread`). Auditado
                      `report.itr.generate`. `include_sessions` (opt-in,
                      2026-09-18) suma `_all_account_activities` — 1
                      request por cuenta. `GET/POST /itr/infra-files` +
                      `DELETE .../{file_id}` (mismo RBAC) gestionan los
                      `HealthCheck_*.json` (`ItrInfraFile`) que alimentan
                      la pestaña Infraestructura vía
                      `app.itr.infra_data.load_infra_data`.
                      **(2026-09-18)** `excluded_tabs` (ids de
                      `report_builder.TAB_ORDER`, 422 si no existen) +
                      `use_cache` (Redis, `PAM_ITR_CACHE_TTL`, clave
                      separada por `include_sessions` — opt-in, sólo para
                      testear cambios al reporte, primera excepción real
                      al criterio de `app.core.cache` de nunca guardar
                      objetos de cuentas). **(2026-09-29, adenda a la
                      spec)** fix de header por cliente, alcance chico a
                      propósito (Alex): Acme/Hooli traen un logo con
                      fondo sólido propio que se veía con un borde blanco
                      bajo el estilo base — se portó sólo ESE override
                      (`app/itr/clientes/<tenant_id>/templates/partials/
                      _header.html.j2`) vía el `ChoiceLoader` que
                      `report_builder._make_jinja_env` ya traía sin usar;
                      `report_builder.client_templates_dir_for(tenant_id)`
                      resuelve la carpeta (mismo `id` que el tenant en
                      PAM Web Console, sin mapeo aparte). **A propósito NO se portó**
                      el layout completo de Initech ("dashboard
                      jerarquizado", que sí existe en el repo de ITR): el
                      reporte de un tenant generado desde PAM Web Console tiene que
                      salir tan completo como el de cualquier otro (mismas
                      10 pestañas, layout genérico) — para sacar una
                      pestaña puntual ya existe `excluded_tabs`, no un
                      template por cliente que reorganiza dónde vive cada
                      dato. Ver `docs/specs/done/itr-report.md`.
    approvals.py      (2026-09-28) `GET /approvals?status=pending|all`,
                      `GET /approvals/count` (badge), `GET /approvals/{id}`,
                      `POST .../reject` (otro operador, motivo) y
                      `.../cancel` (sólo el solicitante). Aprobar NO vive
                      acá: es re-enviar el request original con
                      `X-PAM-Approval-Id`. Ver `docs/specs/dual-approval.md`.
    audit.py          **(2026-09-29, Tanda 1 de docs/specs/done/audit-page.md)**
                      `GET /audit` oculta por defecto las lecturas (`kind ==
                      "consulta"` y `result == "ok"`, calculado en Python por
                      `app.core.audit_catalog.classify()` sobre las acciones
                      DISTINCT presentes — sin migración, no hay columna
                      propia) — `include_reads=true` las muestra. Filtros:
                      `tenant_id`, `target`, `action` (repetible, como
                      `result`), `category` (`audit_catalog.CATEGORIES`),
                      `operator`, `since`/`until`, `result` (repetible),
                      `include_dry_run`. Paginado por cursor `before_id` (no
                      offset: la tabla es append-only e `id` es monótono),
                      `limit` 1–200 (default 100). Responde `{items,
                      next_before_id}` — **cambio de contrato** respecto a
                      la lista simple de antes. Cada fila suma `kind`/
                      `category`/`label`/`target_label`/`request_id`
                      (calculados al serializar, `_row()`; `label()` y
                      `_target_label()` son Tanda 2). `request_id=` (Tanda
                      2, R8 "ver todo el mismo request") filtra por ese
                      request y de paso ignora el filtro de ruido de
                      Tanda 1 (`include_reads`/`include_dry_run` forzados a
                      `true`) — para ver TODO lo que pasó en ese request,
                      incluida una lectura que lo disparó o un dry-run
                      previo.
                      `GET /audit/summary?tenant_id=&since=&until=` —
                      conteos de eventos sensibles del período (24h
                      default): contraseñas reveladas, grabaciones
                      descargadas, backups de credenciales accedidos,
                      accesos denegados, errores, rollbacks fallidos,
                      logins fallidos y aprobaciones pendientes (única
                      cifra que sale de `ApprovalRequest`, no de
                      `audit_log`). `GET /audit/operators` — operadores
                      DISTINCT en el alcance, para el filtro.
                      **(Tanda 3, R10)** `GET /audit/export?format=csv|
                      json&…` — mismos filtros que `GET /audit` (factoreados
                      en `_filtered_stmt()`, que ahora comparten `recent()`
                      y este endpoint), sin paginar, tope `_EXPORT_CAP`
                      (50.000). El CSV arranca con dos filas de metadata
                      (integridad de la cadena + rango de `id` exportado,
                      `_export_csv_meta()`) antes del header real; el JSON
                      lleva el mismo resumen en `meta`. Audita
                      `audit.export` con los filtros aplicados en el
                      `detail`. **(Tanda 3, R11)** `GET /audit/verify`
                      suma `checked`/`first_id`/`last_id`/`verified_at` a
                      `intact`/`first_broken_id` (`ChainVerification`, ver
                      `app/core/audit.py` arriba); sin cambios de RBAC.
                      `GET /tenants/{id}/audit` — rastro de un tenant
                      puntual, **sin cambios de contrato** (sigue siendo
                      una lista simple; lo consume
                      `AccountDetailPanel.tsx`), aunque ahora también suma
                      `kind`/`category` a cada fila (mismo `_row()`
                      compartido, compatible hacia atrás).
                                              (require_tenant_access viewer)
  app/analysis.py     funciones puras analyze_* (portadas de analysis.py de
                      ITR): métricas de accounts/safes/platforms, compliance
                      breakdown, errores de CPM, salud de componentes.
                      **`compliance_breakdown`** no es sólo el
                      `savedFilter=PolicyFailures` nativo de CyberArk —
                      resta también `disabled_stale` (de
                      `analyze_accounts`: gestión automática deshabilitada
                      + más de `Tenant.compliance_stale_days` sin rotar, o
                      nunca rotada — default 30, ajustable por tenant vía
                      `PUT /tenants/{id}/compliance-settings`), porque
                      CyberArk no cuenta esas cuentas como fallo de
                      política (sin gestión activa no hay política que
                      incumplir) aunque lleven años sin rotar — criterio
                      propio de PAM Web Console, no heredado 1:1 del PVWA.
                      **(2026-09-17)** el umbral ya no es siempre el
                      parejo del tenant: `platform_policy_days(platforms)`
                      arma `{platform_id: requirePasswordChangeEveryXDays}`
                      desde `GET /API/Platforms` (campo sin confirmar
                      contra un tenant real todavía) y se usa en su lugar
                      cuando la plataforma tiene rotación periódica
                      activa. `compliance_breakdown` ahora recibe la
                      lista de `accounts` + `policy_failure_ids` (un
                      `set`, no un conteo) y calcula la unión real cuenta
                      por cuenta — ya no aproxima sumando conteos que
                      pueden solaparse. Nueva categoría `active_overdue`
                      (gestión ACTIVA pero igual vencida) + función
                      `account_compliance()` para una cuenta puntual
                      (misma lógica, la usa `app/api/compliance.py`). Ver
                      "Estado actual" más abajo.
  app/itr/            paquete NUEVO (2026-09-17) para el reporte ITR —
                      `analysis.py` + `report_builder.py` +
                      `templates/*.j2` + `logos/` portados CASI VERBATIM
                      de `Telemetry-Reporter`, self-contained,
                      **sin** importar nada de `app.analysis` (que
                      divergió demasiado para reusarlo — ver "Estado
                      actual"). Único cambio real:
                      `build_html_report(..., output_path=None)` devuelve
                      el HTML como `str` si no se le pasa una ruta (PAM Web Console
                      lo sirve como `Response`, no escribe un archivo).
                      `infra_data.py` (2026-09-18, mismo criterio, copiado
                      tal cual) — `load_infra_data(dir)` parsea los
                      `HealthCheck_*.json` de una CARPETA, sin cambios.
                      Usado sólo por `app/api/itr.py`. Excluido de
                      `mypy` (`pyproject.toml`, `ignore_errors` — código
                      vendorizado sin type hints).
  app/reports/        sección de Reportes (port de los scripts de
                      PowerShell-Scripts). base.py (Report / ReportResult
                      / REGISTRY); un módulo por reporte con su `compute_*`
                      puro + `run()`. `Report.category` (2026-09-22) agrupa
                      el catálogo en el picker del frontend — Cuentas/Safes/
                      Identity y Licencias/Infraestructura, sin valor cae a
                      "Otros" en `catalog()`. `_identity.py`: helpers de
                      Redrock (parse_redrock_epoch / days_since /
                      source_label, ver `license_candidates.py` más abajo).
                      Batch 1: safe_duplicates, safe_permissions,
                      account_inventory. Batch 2: psm_balance
                      (list_psm_servers). Batch 3: license_candidates
                      (Identity Redrock, client.redrock_query) —
                      `identity_user_activity` (mismo batch) se dio de baja
                      el 2026-09-22, fusionado dentro de
                      `license_candidates`. **Batch 4** (2026-09-24):
                      `target_inactivity.py`/`account_activity.py`,
                      `heavy=True` — corren como job ARQ
                      (`app.jobs.reports.run_heavy_report`, genérico, ver
                      `app/jobs/` más abajo), no síncronos. `_activities.py`
                      nuevo: `all_account_activities(client, accounts)`
                      compartida por los tres reportes `heavy` (1 `GET
                      .../Activities` por cuenta, en paralelo, una que
                      falla queda en `None` sin abortar el resto).
                      `account_inventory.py` gana `report_full`
                      (`account-inventory-full`, `heavy=True`, mismo día)
                      — reusa `compute_account_inventory` y le suma
                      último acceso + falla de cambio/verificación con
                      motivo real, acercándolo al reporte nativo del
                      PVWA que no se puede llamar directo (ver
                      `docs/specs/done/batch4-reports.md`).
                      **`non_rotating_accounts.py`** (2026-09-28, a pedido
                      de un cliente, `docs/specs/done/non-rotating-accounts.md`)
                      — cuentas que no rotan (error del CPM, PolicyFailures,
                      vencidas según `analysis.account_compliance`, gestión
                      deshabilitada, bloqueadas), con todos sus motivos y el
                      último error real del CPM (`last_cpm_failure`, extraída
                      de `account_inventory.py` y compartida). `heavy=True`,
                      pero `Activities` sólo se consulta para las filas con
                      falla del CPM. Excluir incluye "Cuentas internas de
                      CyberArk" (`is_cyberark_internal`: usuarios de
                      componentes como `PluginManagerUser`/`PSMConnect`/
                      `PSMAdminConnect`/`PSMApp_*`, o safes internos del
                      producto). Estrena el tipo de parámetro
                      `multiselect` (`Param.option_labels`, valores separados
                      por comas, `base.multi_values`) y `Report.tenant_params`
                      (configuración del tenant, ej. `compliance_stale_days`,
                      que se suma a los params sólo al llamar a `run()`, en
                      `_compute` y en el job; nunca en la cache key).
                      **`rotation_failures_by_system.py`** (2026-10-07, a
                      pedido de un cliente,
                      `docs/specs/done/rotation-failures-by-system.md`) — cuentas
                      con error del CPM, deshabilitadas por el CPM o
                      gestionadas y vencidas, agrupadas por `systemType` de
                      su plataforma, con el resumen por sistema (total,
                      con problemas, %) en `meta.by_system`. Reusa los
                      helpers de `non_rotating_accounts.py`. Primer reporte
                      con Excel propio: `Report.xlsx` (resultado serializado
                      + nombre del cliente -> bytes) habilita
                      `format=xlsx` en el export genérico (`Report.formats`
                      viaja en el catálogo); el Excel (Portada + una pestaña
                      por sistema) lo arma **`xlsx.py`** con openpyxl, a
                      partir del resultado cacheado del job.
                      **`user_access.py`** (2026-09-28, pedido de Globex,
                      `docs/specs/done/user-access-report.md`) — safes de un
                      usuario (directo, por rol de Identity o por grupo del
                      Vault) + las cuentas (targets) de cada uno. Roles vía
                      `client.identity_find_user`/`identity_user_roles`
                      (`UserMgmt/GetUsersRolesAndAdministrativeRights`, sin
                      confirmar contra un tenant real); si fallan, degrada a
                      directo + grupos con un `aviso` en la meta.
                      `license_candidates.py` (2026-09-22, fast-follow de
                      Alero) — `enrich_ext_users_with_alero()`
                      (pura)/`enrich_with_alero()` (I/O, abre un
                      `AleroClient`): las filas `ExtUser` (vendors de
                      Remote Access) reemplazan el `lastLogin`/status/
                      candidato que traían de Identity (no confiable para
                      estas cuentas) por datos reales de Alero, si el
                      tenant lo tiene configurado — disparado desde
                      `app.api.reports._compute()`, no desde `report.run()`
                      (que sólo recibe un `CyberArkClient`, Alero es una
                      API separada). Ver `docs/specs/done/alero-vendors.md`.
                      Gana también `origen` ("Origen": AD/Entra ID/
                      Federated/CyberArk, vía `app.reports._identity.
                      source_label` — mismo `KNOWN_SOURCE_DS_CODES` que
                      `app.itr.analysis`, sin importar de ahí a propósito,
                      vendorizado) y `gruposVault` ("Grupos (Vault)",
                      `groupsMembership` de `client.list_users()`) — dos
                      columnas que tenía `License_Candidates_Report.ps1`
                      y el port a PAM Web Console nunca incluyó, sin relación con
                      Alero (aplican también a las filas `EPVUser`).
                      **Fusión con `identity-user-activity` (2026-09-22,
                      mismo día)** — a pedido de Alex, que notó que los dos
                      reportes de "Identity y Licencias" mostraban
                      prácticamente lo mismo. Se da de baja
                      `identity_user_activity.py` (sacado de `REGISTRY`) y
                      `license_candidates` absorbe su alcance completo: ya
                      no filtra por `userType` (antes sólo EPVUser/ExtUser,
                      ahora TODOS los usuarios de Vault+Identity, con
                      `licenseType = "Sin licencia"` para el resto), suma
                      `displayName`/`email` al `SELECT` de Identity (tabla
                      `Users`, que ya era superset de la `User` que usaba
                      el reporte dado de baja), y `candidato` ahora también
                      considera el status de Identity (disabled/suspended/
                      locked/deactivated vía `_identity.INACTIVE_STATUS`),
                      no sólo los días sin login. `meta` cambia
                      `usuarios_con_licencia` por `usuarios_totales` +
                      `con_licencia` (conteo separado, sólo EPVUser/
                      ExtUser). Ver el docstring del módulo y
                      `docs/specs/done/reportes.md`.
  app/domain/         lógica de negocio pura de las escrituras (Fase 2).
    accounts.py         build_account_plan (editar metadata -> JSON Patch +
                        diff) · plan_cpm(action) (change/verify/reconcile ->
                        Plan.effect + warnings según el estado de la cuenta) ·
                        plan_account_link / account_link_body (vincular una
                        cuenta como logon [índice 1] / reconcile [índice 3]) ·
                        build_account_create_body / plan_account_create (alta
                        de cuenta — safeName/platformId/userName/address +
                        properties libres + secretManagement/
                        remoteMachinesAccess si vienen; el secreto sólo pasa
                        por build_account_create_body, nunca por el diff).
                        **Fase 3:** build_account_delete_plan (diff = la
                        cuenta completa from->None, confirm_phrase = name) ·
                        account_restore_changes (arma un `changes`
                        compatible con plan_account_create/
                        build_account_create_body a partir del
                        `metadata_json` de un AccountBackup + su secreto ya
                        descifrado — el restore reusa tal cual la
                        maquinaria de account.create) ·
                        plan_password_vault_only / plan_password_set_next
                        (diff siempre "(definida)", nunca el valor real —
                        el operador fija la contraseña a mano, no el CPM).
    safe_permissions.py catálogo canónico de flags de `permissions` de safe
                        (PERMISSION_KEYS — lo importa el reporte), agrupación
                        para la UI (PERMISSION_GROUPS), presets de onboarding
                        (PRESETS) + resolve_permissions/granted_keys.
    safes.py            build_safe_member_plan (agregar miembro -> Plan.effect
                        + diff "de alta" + warnings) · safe_member_body (form
                        -> cuerpo de AddSafeMember) · suggest_safe_defaults
                        (CPM y retención más frecuentes entre las safes
                        existentes) · build_safe_create_body /
                        plan_safe_create (alta de safe). **Fase 3:**
                        find_member (busca por nombre, case-insensitive,
                        en la lista de `client.safe_members`) ·
                        build_safe_member_update_plan / build_safe_member_
                        remove_plan (reciben `current={"member": {...}}` ya
                        resuelto por el `fetch` del endpoint — el 404 de
                        "no es miembro" se levanta ahí, no acá, para que
                        `run_write` lo mapee bien) · safe_member_update_body
                        · list_cpm_names (CPMs distintos entre las safes
                        existentes, para `/cpm-names`) · build_safe_update_plan
                        / safe_update_body (editar description/managingCPM/
                        retención/OLAC; `safe_update_body` parte del objeto
                        completo — la API real de Update Safe no acepta un
                        patch parcial) · build_safe_delete_plan (`current` =
                        {"safe": {...}} ya validada vacía por el `fetch` del
                        endpoint; diff = la safe from->None, confirm_phrase
                        = safeName).
    users.py            plan_user_activate (reactivar usuario suspendido ->
                        Plan.effect + warnings según suspended/enableUser).
    identity_users.py   (2026-09-15) `plan_identity_user_lock(target_lock)`
                        — factory igual a `plan_platform_activation`: dos
                        endpoints (`/lock`, `/unlock`) comparten la misma
                        lógica con `target_lock` fijo. `current` =
                        `{"locked": bool, "username": str}`.
    discovery.py         (2026-09-22) planes de `discovered_account.*`
                        (onboard/ignore/restore/delete) — `Plan.effect`
                        sin diff, mismo patrón que `plan_cpm`.
                        `plan_discovered_account_onboard` nunca lee
                        `changes["secret"]`, no llega al diff/audit. Ver
                        `app/api/discovery.py` y
                        `docs/specs/done/discovery.md`.
    member_search.py    merge_member_candidates (combina usuarios/grupos del
                        Vault + usuarios de Identity, dedup, alreadyMember) +
                        sanitize_search_term / redrock_user_search_sql.
    platforms.py        find_target_platform (resuelve PlatformID string ->
                        objeto con el ID numérico de "Get target
                        platforms") · normalize_psm (anula PSMServerId/
                        PSMServerName para detectar si algo más cambió) ·
                        plan_platform_psm(platform_id) (factory de
                        build_plan, mismo patrón que plan_cpm).
    alerts.py            (2026-09-30) `component_down_problems(tenant_id,
                        tenant_name, snapshot)` / `approval_pending_problems
                        (pending, stale_after, now)` — reglas de alerta de
                        Fase 5, puras (sin I/O, sin DB), devuelven
                        `list[Problem]` (dataclass rule/tenant_id/target/
                        summary/detail). Las evalúa `app/jobs/alerts.py`.
                        Ver docs/specs/done/alert-rules.md.
  app/vendor_status.py  (2026-09-30) `fetch_summary()` — estado público de
                      status.cyberark.com (SorryApp, API v1, sin auth —
                      **no** es Statuspage.io, asunción inicial descartada
                      al confirmar que `/api/v2/summary.json` da 404). Trae
                      en paralelo `/api/v1/status` + `/api/v1/components?
                      filter[parent_id_null]=true` (sólo los 13 productos
                      de nivel raíz, no los 349 con subcomponentes por
                      región) + `/api/v1/notices?filter[timeline_state_eq]
                      =current|future`. Nada que ver con ningún `Tenant` de
                      PAM Web Console — sin secret store, sin `CyberArkClient`. Usado
                      por `app/api/vendor_status.py`.
  seed/tenants.json   registro de tenants (URLs + secret_ref, CERO credenciales)
  alembic/versions/   0001 (tablas) · 0002 (trigger anti UPDATE/DELETE en
                      audit_log) · 0003 (operators.must_change_password) ·
                      0004 (role_assignments) · 0005 (account_backups) ·
                      0006 (platform_backups) · 0007 (campaigns/
                      campaign_accounts) · 0008 (knowledge_base, GLOBAL sin
                      tenant_id) · 0009 (account_snapshots) · 0010
                      (tenants.compliance_stale_days, default 30) · 0011
                      (tenants.client_logo/client_logo_content_type) ·
                      0012 (itr_infra_files) · 0013 (tenants.alero_tenant_id) ·
                      0014 (drop tenants.enabled_features) · 0015
                      (operators.token_version) · 0016 (refresh_tokens +
                      operators.totp_last_step) · 0017
                      (approval_requests) · 0018 (compliance_exemptions) ·
                      0019 (alert_states) · 0020 (alert_states.pending_notify) ·
                      0021 (debounce de alert_states) · 0022
                      (component_instances_seen)
  app/main.py         create_app() + middleware request_context (request-id:
                      genera/propaga X-Request-ID, línea de acceso pam.access).
  app/jobs/           (2026-09-22) Jobs programados — worker ARQ, primer
                      código de PAM Web Console que corre fuera de un
                      request/response de FastAPI. `worker.py`:
                      `WorkerSettings` (`functions`/`cron_jobs` —
                      convención propia de arq, se leen de la CLASE, no
                      de una instancia — `redis_settings` desde
                      `PAM_REDIS_URL`, `on_startup` llama a
                      `configure_logging()` igual que `app/main.py`).
                      `inventory.py`: `sync_all_tenants_accounts` (cron
                      nocturno, hora configurable por
                      `PAM_INVENTORY_SYNC_CRON_HOUR`, default 2) — recorre
                      los tenants Privilege Cloud habilitados y reusa
                      `inventory_sync.sync_accounts` (la misma función
                      del sync on-demand con TTL de la grilla de
                      Cuentas), sin reimplementar nada. No usa
                      `tenant_client` (levanta `HTTPException`, pensado
                      para un request real) — arma el cliente a mano,
                      mismo patrón que `search.py`/`system_health.py`: un
                      tenant que falla (secret store caído, auth
                      rechazada, CyberArk caído) se audita como error y
                      NO aborta el resto. **Primer job de PAM Web Console auditado
                      sin un operador humano real detrás** —
                      `operator="system"`/`role="system"` (convención
                      nueva, `AuditLog.operator`/`.role` son strings
                      libres sin FK). Corre en un contenedor `worker`
                      propio (mismo `Dockerfile`/imagen que `api`,
                      `command: arq app.jobs.worker.WorkerSettings`) — ver
                      `docker-compose.yml` más abajo. Ver
                      `docs/specs/done/jobs-programados.md`.
                      `reports.py` (2026-09-24): `run_heavy_report` —
                      job **on-demand** (no cron), encolado desde
                      `POST /tenants/{id}/reports/{name}/run-job`
                      (`app.core.arq.get_arq_pool`). Genérico: busca el
                      `Report` en `REGISTRY` y llama a `report.run()` tal
                      cual, sirve para cualquier reporte `heavy=True` sin
                      tocar el worker de nuevo. A diferencia del cron de
                      inventario, se atribuye al operador real que lo
                      pidió (pasado explícito al encolar, no
                      `Depends(current_operator)` — el job corre en el
                      worker, no en un request). `timeout=900` en
                      `WorkerSettings.functions` (el default de arq,
                      300s, no alcanza con miles de cuentas). Ver
                      `docs/specs/done/batch4-reports.md`.
                      `alerts.py` (2026-09-30): `check_alert_rules` —
                      cron cada `PAM_ALERTS_CHECK_INTERVAL_MINUTES`
                      (default 15, `WorkerSettings.cron_jobs` con
                      `minute={0,15,30,45}`, arq no tiene un "cada N
                      minutos" nativo). Evalúa dos reglas puras
                      (`app/domain/alerts.py`): `component_down` (reusa
                      `app.api.system_health._compute_snapshot`, mismo
                      cálculo que System Health, arma el cliente a mano
                      por tenant igual que `inventory.py`) y
                      `approval_pending` (`ApprovalRequest.status=
                      "pending"` hace más de `PAM_ALERTS_APPROVAL_STALE_HOURS`).
                      `_sync_rule()` cruza los problemas activos contra
                      `AlertState` (modelo nuevo, migración `0019`) para
                      abrir/recordar (cada `PAM_ALERTS_RENOTIFY_AFTER_HOURS`,
                      no en cada corrida)/resolver, notificando por
                      `app/core/notify.py` (canal v1: email SMTP,
                      stdlib, diseñado plugeable para sumar Slack/Teams
                      después) y auditando (`alert.rule.opened`/
                      `.renotified`/`.resolved`, `operator="system"`,
                      mismo criterio que `account.sync`). Sin
                      `PAM_ALERTS_SMTP_HOST` (default, el MSP no
                      tiene SMTP cargado todavía), sigue evaluando/
                      auditando, sólo no manda el mail. Ver
                      `docs/specs/done/alert-rules.md`.
                      `component_down` descarta instancias fantasma
                      (desconectadas sin conexión ni login en
                      `PAM_ALERTS_STALE_INSTANCE_DAYS`); la última vez
                      que PAM Web Console vio conectada cada instancia vive en
                      `ComponentInstanceSeen` (migración `0022`, req. 8
                      de `docs/specs/alert-delivery.md`).
  manage.py           CLI (seed-tenants / verify-audit / check-secrets /
                      check-alerts-now)
  pyproject.toml      [tool.mypy] + [tool.pytest.ini_options]. ruff sigue en
                      ruff.toml.
  tests/              unit/ (SQLite in-memory, conftest.py con fixture session)
                      + contract/ (tenant real, skip sin PAM_CONTRACT_*).
docker-compose.yml    db (postgres:16) + redis + api (127.0.0.1:8000) +
                      **worker** (2026-09-22, mismo `Dockerfile` que
                      `api`, sin puertos — sólo consume jobs de Redis, ver
                      `app/jobs/` arriba) + web (nginx, 127.0.0.1:8080,
                      sirve la SPA y proxea /api/ -> api:8000). Monta
                      ./secrets:/run/secrets:ro (`api` y `worker` — el
                      worker también autentica contra CyberArk por
                      tenant).
secrets/              secret store real (age.key + secrets.age) — NO en git,
                      solo en el server. secrets/README.md: alta y rotación.
                      Además `backups-age.key`: keypair `age` PROPIO de los
                      backups de cuentas borradas (Fase 3, `account.delete`),
                      separado de `age.key` — comprometer uno no compromete
                      el otro.
frontend/             SPA React 18 + TS + Vite + Tailwind v4 + shadcn/ui
                      (primitivas Radix) + lucide-react + TanStack
                      Query/Table/Virtual.
  src/index.css         Tailwind + tokens del design system propio
                       (navy #002142 / sky #14baed / business-line accents /
                       Noto Sans **self-hosted** en src/assets/fonts/) mapeados
                       a variables semánticas shadcn. Bloque `@media print`.
  src/components/ui/    primitivas shadcn (button, card, badge, input, label,
                       select, tabs, table, sheet, dialog, dropdown-menu,
                       alert, skeleton, switch, toast) — código propio, estilo
                       new-york. `toast.tsx` (2026-09-28): notificaciones
                       efímeras sin dependencias — `toast({title,
                       description?, variant})` / `toastError(title, e)`
                       desde cualquier lado, `<Toaster />` montado una vez
                       en `main.tsx`; `role="status"` (éxito) o
                       `role="alert"` (error), pausa con hover/foco, Esc
                       cierra. Para confirmar acciones cuyo diálogo se
                       cierra solo; los errores de formulario siguen
                       inline, junto al campo. `switch.tsx` es un botón `role="switch"`
                       simple, sin `@radix-ui/react-switch` (no está
                       instalado).
  src/lib/utils.ts      cn() (clsx + tailwind-merge)
  src/lib/api.ts        fetch a /api, adjunta el JWT, desloguea ante 401.
                       apiBlob() para descargas (CSV) con el mismo manejo.
  src/lib/download.ts   saveBlob(blob, filename) — descarga con <a download>.
  src/lib/auth.tsx      AuthProvider / useAuth
  src/pages/Login.tsx   login password + TOTP (+ flujo de enrolamiento).
                       **Rediseño (2026-09-28):** pantalla dividida — panel
                       navy plano (marca + 3 garantías: MFA, auditoría,
                       aprobación dual; se oculta bajo `lg`) + formulario
                       con indicador de pasos, `<form>` real (autocomplete
                       `username`/`current-password`, gestores de
                       contraseñas), mostrar/ocultar contraseña, aviso de
                       Bloq Mayús, errores traducidos por status (401/429/
                       5xx), enrolamiento en 3 pasos con clave TOTP en
                       grupos + copiar, "Usar otro usuario", aviso de uso
                       restringido y badge de entorno si no es `prod`
                       (`GET /version`). Tras el login vuelve a la página
                       que se intentó abrir (`location.state.from`).
  src/components/TotpInput.tsx  (2026-09-28) código TOTP en 6 casillas:
                       sólo dígitos, avance/retroceso con teclado, pegar
                       un código completo, `autocomplete="one-time-code"`
                       y `onComplete` (el login envía solo al completar).
  src/pages/Accounts.tsx, Safes.tsx, Platforms.tsx,
  Sessions.tsx          (2026-09-15) un recurso = una página = un ítem
                       propio de la barra navy (espacio Operación) — ya no
                       existe `Inventory.tsx` (tenía los 5 como tabs
                       horizontales de una sola página, deshecho a pedido
                       de Alex: quería cada recurso al mismo nivel que
                       Buscar/Dashboard en el sidebar, como "Accounts" en
                       el PVWA). Cada página usa `TenantScope` (título +
                       selector de tenant + estados vacíos) +
                       `QueryState`/`useServerGrid`/`boolBadge`
                       (`lib/gridHelpers.tsx`) para no duplicar. Plataformas
                       agrupa por `systemType` en secciones colapsables
                       (`PlatformGroup`, igual al "Platform Management" del
                       PVWA clásico — Windows/*NIX/Database/...).
                       **Cuentas, rediseño (2026-09-28, a pedido de Alex):**
                       chips de estado arriba (Todas/Con falla/Gestión
                       automática deshabilitada/Deshabilitada por el CPM,
                       con conteo; clic filtra con
                       `?status=`, conteos de `GET .../accounts/summary`),
                       columna Estado con pastillas ("Con falla"/"Gestión
                       automática deshabilitada" -- la deshabilitó un
                       usuario -- /"Deshabilitada por el CPM" -- motivo
                       `(CPM)…`, ej. `(CPM)MaxRetries`, el CPM se rindió
                       tras los reintentos --, o "En orden"), columna "Último cambio"
                       (hace N días), "Crear cuenta" como botón principal
                       con desplegable para "Alta masiva", Actualizar/
                       Exportar/Columnas sólo con ícono, filtro de fecha con
                       atajos (7/30/90 días), fila de filtros por columna
                       detrás de "Filtros" con chips de los activos, barra
                       flotante al tildar cuentas (Editar/Mover), acciones
                       al pasar el mouse por la fila (copiar usuario, ver
                       contraseña `tenant_admin+`, cambiar ahora
                       `operator+`) y paginación con páginas y tamaño.
                       `DataTable` ganó props opcionales para esto
                       (`leadingToolbar`, `countNoun`,
                       `collapsibleColumnFilters`, `compactColumnsButton`,
                       `server.onLimitChange`); el resto de las grillas no
                       cambia salvo la paginación.
  src/pages/Recordings.tsx  (2026-09-16) Grabaciones de sesiones PSM YA
                       TERMINADAS (distinto de `Sessions.tsx`, en vivo) —
                       ruta `/recordings`, espacio Operación, después de
                       Sesiones. Toolbar con Safe/búsqueda/rango de fechas
                       (server-side, default últimas 48h) — sin
                       `useServerGrid`/paginación server-side (el endpoint
                       no pagina, sólo `Limit`). Columna **Riesgo**
                       (`RiskScore`/`Severity`, que CyberArk sólo calcula
                       para grabaciones). `onRowClick` abre
                       `components/RecordingDetailPanel.tsx` — reusa la
                       fila que ya trajo la lista para el detalle (evita
                       una llamada extra a CyberArk) + actividades +
                       `components/RecordingDownloadSection.tsx` (razón ->
                       `apiBlob` POST -> `saveBlob`, calcado de
                       `CredentialsBackupSection.tsx`), gateado por
                       `recording.play` + `tenant_admin+`. Ver
                       `docs/specs/done/session-recordings.md`.
  src/pages/Discovery.tsx  (2026-09-22, reescrito el mismo día) cuentas
                       pendientes de CyberArk Discovery — ruta
                       `/discovery`, espacio Operación, entre Cuentas y
                       Safes. Columnas armadas desde `identifiers`/
                       `customProperties` (la forma real de la API, no
                       campos planos), badge de `lastActivityStatus`.
                       Botón "Onboard" (`operator+`) abre
                       `components/DiscoveryOnboardDialog.tsx` — pega al
                       endpoint REAL de onboarding de Discovery vía
                       `<DryRunDialog>` (preview -> razón -> confirmar),
                       ya no el workaround que reusaba `account.create`.
                       Menú `···` con "Ver actividad"
                       (`components/DiscoveredAccountActivitiesDialog.tsx`,
                       sólo lectura, por qué una cuenta no se
                       auto-remedió) + Ignorar/Restaurar/Borrar
                       (`operator+`, cada uno un `<DryRunDialog>` genérico
                       con `changes={}`, mismo patrón que las acciones de
                       CPM del detalle de cuenta). Ver
                       `docs/specs/done/discovery.md`.
  (`src/pages/Users.tsx`/`Groups.tsx` — Usuarios/Grupos **del Vault** —
   se sacaron del frontend el 2026-09-24: "Users and Groups" es un
   concepto de PAM Self-Hosted, no de Privilege Cloud, donde la
   administración de usuarios es 100% vía Identity. Ver
   `docs/specs/done/vault-users-groups-crud.md` "Estado 2026-09-24".)
  src/pages/SsoUsers.tsx, IdentityRoles.tsx  Usuarios/Roles de
                       **Identity/SSO** (espacio de nav "Identity",
                       `/sso-users` + `/identity-roles`) — ver
                       `docs/specs/done/identity-users.md`. `SsoUsers.tsx`
                       abre `DetailDrawer` (prop `identityUserId`) con
                       badge bloqueado/activo +
                       `IdentityUserLockSection` (bloquear/desbloquear,
                       calcado de `PlatformActivateSection`).
                       `IdentityRoles.tsx` es sólo lectura; clic en un rol
                       abre `IdentityRoleDetailPanel.tsx` (2026-09-28, a
                       pedido de Alex, como "Members" de Identity
                       Administration) con descripción + miembros
                       (usuarios/grupos/roles), pedidos al abrir; con
                       `operator+`, "Agregar miembros"
                       (`IdentityRoleMemberAdd.tsx`, usuarios/roles de
                       Identity → `DryRunDialog`). Ver
                       `docs/specs/identity-role-members.md`.
  src/pages/AleroVendors.tsx, AleroUsers.tsx, AleroRoles.tsx,
  AleroActivities.tsx     (2026-09-21) espacio de nav propio "Alero"
                       (CyberArk Remote Access — acceso de terceros/
                       proveedores), mismo criterio que "Identity": sólo
                       contenido de esa API, nada del Vault. Cuatro ítems
                       planos — Vendors/Usuarios/Roles/Activities — ver
                       `docs/specs/done/alero-vendors.md`. `AleroVendors.tsx`/
                       `AleroUsers.tsx` con avatares de iniciales (color
                       determinístico por nombre, paleta `line-*` de
                       marca) y badges de estado con color
                       (`components/AleroBadges.tsx`, compartido entre
                       ambas); clic en la cantidad de vendors de un rol en
                       `AleroRoles.tsx` navega a Vendors filtrado por ese
                       rol (client-side). `AleroActivities.tsx`: selector
                       de ventana (1h/24h/7d/30d), mismo patrón
                       `DataTable` + toolbar que `Audit.tsx`.
  src/pages/AleroVendorDetail.tsx, AleroUserDetail.tsx  (2026-09-21) detalle
                       de un vendor/usuario, como **página completa**
                       (rutas propias `/alero-vendors/:id`,
                       `/alero-users/:id`, `?tenant=` para volver a la
                       lista) — no un panel lateral: la primera versión
                       usó un `Sheet` genérico y Alex pidió pantalla
                       completa, como el portal nativo, porque mostraba
                       muy poca información en un panel angosto. Mismo
                       patrón que `KnowledgeBaseArticle.tsx` (Link "<
                       Volver" + card con secciones en grilla de
                       columnas).
  src/pages/SiaPolicies.tsx, SiaSessions.tsx, SiaStrongAccounts.tsx
  (+ SiaStrongAccountDetail.tsx), SiaSettings.tsx  (2026-10-06) espacio de nav "SIA",
                       sólo lectura. Tipos y etiquetas en `src/lib/sia.ts`;
                       detalle de política como **página completa**
                       (`SiaPolicyDetail.tsx`, ruta `/sia/policies/:id
                       ?tenant&source&kind`, mismo criterio que Alero): las
                       secciones del portal (datos generales, identidades,
                       targets, permisos, condiciones) visibles a la vez,
                       una frase de resumen arriba y el JSON original
                       colapsado; las de API legado muestran sus reglas;
                       avisos de partes caídas en
                       `components/SiaWarnings.tsx`. Ver
                       `docs/specs/done/sia-read-only.md`.
  src/pages/Dashboard.tsx  selector de tenant + KPIs + gráficos (BarList) +
                       "Imprimir / PDF" (window.print + `@media print`).
                       **(2026-09-17)** el KPI "Compliance" y cada barra
                       del "Compliance (desglose)" son clickeables →
                       `/compliance?tenant=<id>&reason=<label>`
                       (`Kpi` ganó un `onClick` opcional; `BarList` ganó
                       `onItemClick`).
  src/pages/Compliance.tsx  (2026-09-17) drill-down navegable de
                       Compliance — tabs por motivo (PolicyFailures/
                       ActiveOverdue/DisabledStale/DisableByUser/
                       DisableByCPM/Locked), `?reason=` pre-seleccionado
                       si se llega desde el Dashboard. Tabla con
                       `DataTable` (columna "Umbral aplicado" para
                       ActiveOverdue/DisabledStale — el umbral real de la
                       plataforma de esa cuenta, o el default del tenant),
                       clic en una fila abre `AccountDetailPanel`
                       genérico. `TenantScope` (lee `?tenant=`) +
                       `useSearchParams` propio para `?reason=`. Ruta
                       `/compliance`, espacio Operación, después de
                       Dashboard.
  src/pages/Search.tsx     búsqueda cross-tenant de cuentas, resultados
                       agrupados por tenant (una Card por tenant)
  src/pages/Search.tsx     búsqueda cross-tenant de cuentas, resultados
                       agrupados por tenant (una Card por tenant)
  src/pages/SystemHealth.tsx  (2026-09-17) grilla cross-tenant — una fila
                       por tenant (Vault/Web Portal/CPM/PSM y PSM for SSH/
                       cuentas gestionadas/sesiones activas), `Badge`
                       success/warning/danger/neutral por componente según
                       conectadas vs. total. Sin `TenantScope` (cross-
                       tenant, igual que `Search.tsx`).
                       Botón "Actualizar" (`?refresh=1`, patrón
                       `wantRefresh` de `Dashboard.tsx`). Ruta
                       `/system-health`, espacio Operación.
                       **Absorbe la página Componentes (2026-09-28):**
                       `pages/Components.tsx` se eliminó (el detalle de
                       instancias ya está en cada tile); `/components`
                       redirige a `/system-health`. El endpoint
                       `GET /tenants/{id}/components` sigue en el backend,
                       sin uso en el frontend.
                       **Rediseño (2026-09-28, a pedido de Alex, con el
                       System Health del PVWA como referencia):** resumen
                       arriba (operativos/degradados/críticos), una tarjeta
                       por tenant con borde de color según el peor estado
                       (un componente entero caído = "Crítico"), tiles de
                       Vault/CPM/PSM (AIM sólo si hay) con barra
                       segmentada, una marca verde/roja por instancia (sin
                       Web Portal: en Privilege Cloud lo administra
                       CyberArk, no cuenta para el estado); clic
                       en un tile muestra sus instancias (IP, versión,
                       usuario del componente, última conexión — el backend
                       suma `instances` a cada componente del snapshot).
                       Tenants con problemas primero.
  src/pages/CyberArkStatus.tsx  (2026-09-30) estado de status.cyberark.com
                       (el FABRICANTE, no un tenant de PAM Web Console) — banner con
                       el estado general de la página + grilla de los 13
                       productos de nivel raíz (`Badge` success/warning/
                       danger/info por estado) + listas de incidentes/
                       mantenimientos en curso y próximos mantenimientos
                       programados (cada notice linkea a
                       status.cyberark.com). Sin `TenantScope` (mismo
                       criterio que `KnowledgeBase.tsx`/`Audit.tsx`: dato
                       global, no de un tenant). Botón "Actualizar"
                       (`?refresh=1`, mismo patrón `wantRefresh` que
                       `SystemHealth.tsx`) + `refetchInterval` de 5 min.
                       Ruta `/cyberark-status`, espacio Operación, después
                       de "System Health".
  src/pages/Account.tsx    "Mi cuenta" + cambio de contraseña
  src/pages/Operators.tsx  panel de gestión de operadores (platform_admin)
  src/pages/Reports.tsx    catálogo de reportes -> DataTable (columnas
                       dinámicas) + "Exportar CSV" + `CredentialsBackupSection`
                       (backup cifrado de TODAS las cuentas del tenant, fuera
                       del catálogo — ver `app.api.credentials_backup`).
                       **Rediseño (2026-09-22, `/impeccable critique` +
                       `layout`)**: el `<Select>` de reporte (catálogo
                       invisible hasta abrirlo, heurística "Recognition
                       rather than recall" 1/4) se reemplaza por un picker
                       agrupado por categoría (`Report.category`, nuevo en
                       `app.reports.base` — Cuentas/Safes/Identity y
                       Licencias/Infraestructura) con nombre+descripción
                       siempre visibles, mismo lenguaje visual que ya usa
                       PAM Web Console (fila con hover/selected `bg-accent`, label
                       `text-xs uppercase tracking-wide` — no una grilla de
                       cards genéricas, que el propio skill banea como "the
                       lazy container"). Tenant/params/Ejecutar aparecen
                       recién al elegir un reporte (progressive disclosure).
                       Pestaña "Operaciones Avanzadas" renombrada a
                       "Exportaciones y Backups" + subtítulo explicando la
                       diferencia con los reportes analíticos. `meta`
                       post-ejecución con label capitalizado y números
                       localizados (antes: snake_case crudo). Verificado con
                       un mock visual (Tailwind CDN + tokens de
                       `DESIGN.md`) vía Chrome, no contra el stack real
                       (Docker no accesible desde la sesión) — pendiente
                       probar contra datos reales. **Mismo día:** Alex notó
                       que los dos reportes de "Identity y Licencias" se
                       solapaban — se fusionan en uno solo
                       (`license-candidates`, ver `app/reports/
                       license_candidates.py` más arriba), esa categoría
                       queda con un único ítem en el picker.
                       **Batch 4 (2026-09-24):** los reportes `heavy`
                       (`target-inactivity`/`account-activity`) ya no
                       están deshabilitados en el picker — al elegir uno
                       y ejecutar, `POST .../run-job` encola el job y el
                       botón "Ejecutar" queda en spinner mientras
                       `GET .../jobs/{job_id}` pollea cada 3s
                       (`refetchInterval`); al completar, reusa el mismo
                       render de tabla/meta/"Exportar CSV" que un
                       reporte síncrono (`app.jobs.reports.
                       run_heavy_report` cachea el resultado en la misma
                       key que usaría el `GET` normal). Ver
                       `docs/specs/done/batch4-reports.md`.
                       **Rediseño en dos columnas (2026-09-28, a pedido
                       de Alex — antes, al elegir un reporte había que
                       bajar para ver tenant/filtros/Ejecutar):** catálogo
                       fijo a la izquierda (búsqueda, categorías, ícono de
                       reloj en los `heavy`), configuración + resultado a
                       la derecha (grilla de parámetros `ParamField`, "Falta:
                       …" junto a Ejecutar, `meta` como tarjetas). El
                       tenant va en el encabezado y vale para las dos
                       pestañas. El catálogo se oculta para dar todo el
                       ancho a la tabla (`localStorage`
                       `pam:reports-catalog-collapsed`). Sólo viajan los
                       parámetros del reporte elegido (antes se mandaban
                       también los que quedaban cargados de otro).
  src/pages/Tenants.tsx    registro de tenants (id/nombre/cliente) + config
                       que no es una operación sobre CyberArk —
                       `ComplianceSettings` **(2026-09-17)**: input del
                       umbral de días de `disabled_stale` (PUT .../
                       compliance-settings) + `LogoSettings`
                       **(2026-09-18)**: subir/reemplazar/quitar el logo
                       del cliente para el reporte ITR — vista previa vía
                       `apiBlob` + `URL.createObjectURL` (un `<img src>`
                       no puede mandar el Bearer token), subida con
                       `apiForm` (multipart). Arriba de las features.
  src/pages/AccountBackups.tsx  (Fase 3) selector de tenant (sólo los que el
                       operador ve con `tenant_admin+`) + tabla de backups de
                       cuentas borradas + "Revelar" (diálogo propio: reason ->
                       contraseña de sólo lectura con botón copiar, nunca
                       persiste en el DOM tras cerrar) + "Restaurar"
                       (`<DryRunDialog method="POST">`). Ruta `/backups`,
                       gateada en `App.tsx` (`TenantAdminOnly`) + entrada de
                       sidebar sólo si el operador es `tenant_admin+` en
                       algún tenant (`useAnyTenantAdmin`, o platform_admin).
  src/pages/Audit.tsx  (2026-09-16, reescrita 2026-09-29 — Tandas 1, 2 y 3
                       de docs/specs/done/audit-page.md) rastro de auditoría de
                       PAM Web Console — distinto del audit nativo de CyberArk (que
                       sólo ve al service account, nunca al operador
                       real). Resumen de eventos sensibles (tarjetas
                       clickeables que aplican el filtro correspondiente;
                       "Aprobaciones pendientes" no filtra, esa cifra sale
                       de `/approvals`, no de `audit_log`) → barra de
                       filtros con estado en la URL (`useSearchParams`):
                       tenant, categoría (`Select`), resultado
                       (`MultiSelectField`, repetible), operador, acción
                       exacta, target, rango de fechas, "Incluir
                       consultas"/"Incluir previsualizaciones"
                       (`Switch`) → `DataTable` con columna "Acción" en
                       español (`entry.label`, código en el `title` del
                       `<span>`) y "Target" con nombre (`target_label`),
                       ambas — junto con "Operador" — clickeables para
                       filtrar por ese valor (R9, `stopPropagation` para no
                       abrir el detalle de la fila) → "Cargar más" (cursor
                       `before_id`, acumula en estado local — no es el modo
                       `server` de offset de `DataTable`) → detalle por
                       registro en `components/AuditEntryPanel.tsx`
                       (nuevo): razón, error, antes/después como tabla
                       (desde `detail.diff` o `before`/`after`), efecto,
                       cuentas fallidas, aprobación, "Ver todo el mismo
                       request" (filtra por `request_id`, reemplaza el
                       resto de los filtros) y JSON crudo colapsado.
                       `resultBadge`/`kindBadge` viven en
                       `lib/auditFormat.tsx` (no en un componente, por la
                       regla de fast-refresh de ESLint) y los comparten
                       `Audit.tsx` y `AuditEntryPanel.tsx`. **(Tanda 3)**
                       Botón "Exportar" (`DropdownMenu` CSV/JSON, mismo
                       patrón `apiBlob` → `saveBlob` que
                       `ExportCsvButton.tsx`, con los filtros activos —
                       `buildParams` sin `limit`/`before_id`) junto al de
                       "Verificar integridad", que ahora muestra cuántos
                       registros se verificaron, el rango de `id` y cuándo
                       (`AuditVerifyResult` ampliado); si la cadena está
                       rota, un link abre ese registro (`openById()`, un
                       `GET /audit?before_id=id+1&limit=1&include_reads=
                       true&include_dry_run=true` — sin depender de los
                       filtros activos ni de un endpoint nuevo). Ruta
                       `/audit`, espacio Administración, mismo gating que
                       `/backups` (`tenant_admin+` efectivo en algún
                       tenant, o `platform_admin`). Spec `audit-page.md`
                       cerrada en código, falta probarla contra el server.
  src/components/DataTable.tsx  grilla genérica sobre TanStack Table +
                       TanStack Virtual. Modo client (orden multi, filtro
                       global + por columna, virtualización) o modo `server`
                       (paginación/orden/búsqueda contra el backend). Resize
                       de columnas, menú "Columnas" (visibilidad), header
                       sticky. Anchos/visibilidad/orden se persisten en
                       localStorage por `tableId` (`pam:table:<id>`).
                       Selección de filas opcional (`selectedIds`/
                       `onSelectedIdsChange`, controlada por el padre —
                       sobrevive a la paginación server-side porque se
                       guarda por id real, no por índice); la usa el tab
                       Cuentas para editar varias a la vez
                       (`AccountBulkEditDialog.tsx`).
  src/components/BarList.tsx    barras horizontales sin librería de charting
  src/components/MiniBars.tsx   barras verticales compactas (series temporales)
  src/components/DetailDrawer.tsx  panel lateral (Sheet) genérico para
                       usuarios de Identity: objeto crudo (`KV`, recursivo)
                       + (prop `identityUserId`) badge bloqueado/activo +
                       `IdentityUserLockSection`. Perdió la rama de
                       plataformas el 2026-09-29 (ver
                       `PlatformDetailPanel.tsx`). Perdió las ramas de
                       usuarios/grupos del Vault el 2026-09-24 (ver
                       `docs/specs/done/vault-users-groups-crud.md`). Safes
                       usa su propio panel, `SafeDetailPanel.tsx` — sus
                       miembros delegan cada fila a `SafeMemberRow`. Cuentas
                       usa un panel propio, ver `AccountDetailPanel.tsx`.
  src/components/TenantScope.tsx  (2026-09-15) título + selector de tenant +
                       estados vacíos ("sin tenants"/"elija un tenant"),
                       común a toda página con selector de tenant —
                       `children` (render prop) sólo se monta con un tenant
                       elegido. Usa `lib/useTenantParam.ts` (query param
                       `?tenant=`). Reemplaza el bloque duplicado que tenía
                       cada página antes de separar `Inventory.tsx`.
  src/components/QueryState.tsx  loading/error de una query con el nombre
                       del tenant en el mensaje — común a toda página con
                       selector de tenant.
  src/components/IdentityUserLockSection.tsx  "Bloquear/Desbloquear
                       usuario" en el detalle de un usuario de Identity —
                       calcado de `PlatformActivateSection` (dos endpoints,
                       `/lock`/`/unlock`, no un body `{lock: bool}`).
                       Gateado por `identity_user.lock` + `operator+`.
  src/components/SafeMemberRow.tsx  (Fase 3) una fila de "Miembros" en el
                       detalle de una safe — sólo lectura por defecto; agrega
                       "Editar" (panel inline de presets/checkboxes, mismo
                       catálogo que `SafeMemberAddSection`) y "Quitar"
                       (confirmación + `<DryRunDialog>`) si el tenant tiene
                       `safe.member.update`/`safe.member.remove` **y** el
                       operador es `approver+`.
  src/components/SafeEditSection.tsx  (Fase 3) botón "Editar safe" en el
                       detalle de una safe — description/CPM (`CpmField`)/
                       retención/OLAC, prefilled con el objeto de la grilla.
                       Gateado por `safe.update` + `approver+`.
  src/components/SafeDeleteSection.tsx  (Fase 3) botón "Borrar safe" en el
                       detalle de una safe — confirmación inline (mismo
                       patrón que "Quitar" en `SafeMemberRow`) ->
                       `<DryRunDialog method="DELETE">`. Gateado por
                       `safe.delete` + `approver+`. `onDeleted` cierra el
                       `DetailDrawer` además de invalidar la grilla del
                       inventario. El equivalente de cuentas es
                       `AccountDeleteDialog.tsx`, ver `AccountDetailPanel.tsx`.
  src/components/RevealPasswordDialog.tsx  diálogo genérico "Revelar":
                       razón obligatoria -> contraseña de sólo lectura +
                       copiar al portapapeles (nunca persiste en el DOM
                       tras cerrar). Prop `path` (el POST que devuelve
                       `{"password"}`) — lo comparten el reveal de backups
                       (`AccountBackups.tsx`) y el botón "Ver contraseña" de
                       `AccountDetailPanel.tsx` (cuentas activas, gateado por
                       `account.reveal` + `tenant_admin+`).
  src/components/CpmField.tsx  selector de CPM compartido por
                       `SafeCreateDialog` y `SafeEditSection` — `<Select>`
                       con `GET /cpm-names` (CPMs que ya gestionan alguna
                       safe del tenant) + "…o ingresarlo a mano" (mismo
                       patrón buscador/manual que `SafeMemberAddSection`).
  src/components/DryRunDialog.tsx  flujo genérico previsualizar -> diff +
                       warnings -> (Fase 3: si `confirm_phrase` viene en el
                       dry-run, input "escriba X para confirmar" antes de
                       habilitar "Confirmar") -> razón -> confirmar. Prop
                       `method` (default POST; Fase 3 usa PUT/DELETE). Prop
                       `extraBody` — campos extra a nivel raíz del body
                       junto a `changes`/`dry_run`/`reason` (ej.
                       `account_ids` en un bulk-update). Lo usan todas las
                       operaciones de escritura. Props `approvalId`/
                       `requestedBy` (2026-09-28): aprobar una solicitud
                       de otro operador (header `X-PAM-Approval-Id`);
                       si el dry-run trae `requires_approval`, el botón
                       dice "Solicitar aprobación".
  src/pages/Approvals.tsx  (2026-09-28) cola de aprobaciones de un segundo
                       operador — ruta `/approvals`, espacio Operación,
                       badge con `GET /approvals/count`. "Revisar y
                       aprobar" abre `DryRunDialog` con el body guardado.
                       Ver `docs/specs/dual-approval.md`.
  src/components/AccountDetailPanel.tsx  panel de detalle de cuenta,
                       rediseñado (2026-09-14) sobre el "Platform Management"
                       nativo como referencia de estilo, sin copiarlo — Sheet
                       propio (más ancho que `DetailDrawer`), no el genérico.
                       Header: eyebrow + nombre@address + chips de
                       plataforma/safe + pill de estado de gestión. Tab
                       Resumen gana **`ComplianceCard`** (2026-09-17, a
                       pedido de Alex — "como el PVWA pero con diseño
                       propio de PAM Web Console"; primero fue un pill en el header,
                       después pidió una card para también ver hace
                       cuántos días rotó) — inspirada en el círculo "Days
                       ago" del PVWA (mismo dato: antigüedad de la
                       contraseña), pero con los verdes/rojos de marca de
                       PAM Web Console en vez del genérico de CyberArk, y mostrando
                       el/los motivo(s) reales de la no-compliance en
                       texto (el PVWA sólo muestra el número, sin
                       explicar el motivo ahí mismo). **(2026-09-17,
                       mismo día)** ya no calcula nada localmente — un
                       solo `useQuery` a `GET .../compliance/accounts/
                       {id}/status` (backend, `analysis.
                       account_compliance()`) trae `compliant`/
                       `age_days`/`threshold_days`/`reasons` ya resueltos,
                       cruzando `PolicyFailures` nativo con la política de
                       rotación de LA PLATAFORMA de la cuenta (o el
                       default del tenant) — el frontend no sabía nada de
                       políticas de plataforma, sólo podía usar el
                       default parejo antes de este cambio.
                       `CompliancePill` (el badge chico, reusado en el
                       header de la card). Barra de
                       acciones: "Ver contraseña" (`RevealPasswordDialog`) /
                       "Editar" (`AccountEditDialog.tsx`) / "Cambiar ahora" /
                       menú "Más" (`DropdownMenu`: Verificar, Reconciliar,
                       Cambiar sólo en la Vault / Especificar próxima
                       contraseña — `AccountPasswordDialog.tsx` con `mode`
                       fijo —, Vincular — `AccountLinkDialog.tsx`) / "Borrar"
                       (`AccountDeleteDialog.tsx`, separado a la derecha) —
                       cada botón oculto si falta la feature o el rol. Tabs
                       Resumen (tarjeta "Gestión del secreto" con 3 stat
                       tiles — último cambio/verificación/reconciliación,
                       cada uno con acceso directo a esa acción — + tarjeta
                       "Actividad reciente") / Detalles (tarjeta curada con
                       las propiedades de la cuenta — address, usuario,
                       plataforma, safe, fecha de creación, remote machines,
                       platformAccountProperties) / Actividad (reusa
                       `AccountActivityFeed`/`AuditTrailFeed` de
                       `ActivityFeed.tsx` **sin fusionar** los dos feeds —
                       filter-chips Todo/CyberArk/PAM Web Console alternan cuál se
                       muestra) / **Versiones** (2026-09-16, visible sólo
                       con `tenant_admin+`) — historial de contraseñas
                       anteriores (`GET .../password-versions`), botón
                       "Revelar" por fila (`RevealPasswordDialog` con
                       `extraBody={{version}}`) gateado además por
                       `account.reveal.history`. Los 4 diálogos de
                       escritura de cuenta son
                       componentes controlados (`open`/`onClose` desde el
                       panel, sin trigger propio) — reemplazan las 7
                       secciones inline que tenía el `DetailDrawer` genérico
                       antes de este rediseño (`AccountEditSection`,
                       `AccountRevealSection`, `AccountActions`,
                       `AccountPasswordSection`, `AccountLinkSection`,
                       `AccountDeleteSection`, `AccountManagementStatus`,
                       eliminadas).
  src/components/AccountBulkEditDialog.tsx  editar el mismo campo (Address/
                       Usuario/Nombre/Plataforma) a las cuentas tildadas en
                       la grilla, con UN valor igual para todas — botón
                       "Editar N cuenta(s)" en el toolbar de
                       `pages/Accounts.tsx`, gateado por `account.update` +
                       `operator+`. Ver `docs/specs/done/account-bulk-edit.md`.
  src/components/AccountBulkMoveDialog.tsx  (2026-09-22) mover a la MISMA
                       Safe destino las cuentas tildadas en la grilla —
                       botón "Mover N cuenta(s)" junto al de editar,
                       gateado por `account.move` + `platform_admin`.
                       Patrón preview→tabla de resultados→confirmar igual
                       a `AccountBulkCreateDialog.tsx` (no el genérico
                       `DryRunDialog`, la forma de la respuesta de
                       `bulk-move`/`bulk-move/preview` no es la de
                       `writeop.run_write`). Confirmación: escribir el
                       nombre exacto de la Safe destino (no hay
                       `confirm_phrase` para N objetos). Ver `account.move`
                       en `app/api/writes.py` más arriba.
  src/components/PlatformField.tsx  selector de plataforma compartido por
                       `AccountEditDialog` y `AccountBulkEditDialog` —
                       reusa la lista cacheada de `/platforms`, ahora un
                       Combobox con búsqueda (2026-09-22, antes un
                       `<Select>` plano) y un filtro opcional `systemType`
                       (en `AccountEditDialog` se precarga con el
                       `systemType` de la plataforma actual de la cuenta,
                       para no ofrecer p.ej. plataformas de Database al
                       editar una cuenta Windows). Ver
                       `docs/specs/done/account-change-platform.md` — el "Move"
                       de safe documentado ahí como "no implementado" SÍ se
                       terminó implementando después, ver `account.move`
                       más abajo y en `app/api/writes.py` más arriba (es un
                       workaround por clonación, no el `AccountsMgt.asmx/
                       Move` real del PVWA clásico, que sigue fuera de
                       alcance por el mismo motivo — depende de la sesión
                       de navegador del operador, no del service account).
  src/components/SafeField.tsx  (2026-09-22) Combobox con búsqueda para
                       elegir Safe destino, mismo patrón que
                       `PlatformField` — reusa `/safe-names`. Sólo se
                       muestra en `AccountEditDialog` a operadores
                       `platform_admin` (RBAC real la vuelve a validar en
                       el backend); elegir un Safe distinto del actual
                       dispara el flujo de `account.move`, no de
                       `account.update` — ver el docstring de
                       `AccountEditDialog.tsx`. **Bug real encontrado
                       probando `account.move` contra Globex
                       (2026-09-24, primera vez que se probaba):**
                       llamaba a `GET /tenants/{id}/safes/names` (con
                       barra) en vez de `/safe-names` (con guión) — esa
                       URL matchea por patrón de ruta con
                       `/safes/{safe_id}` (sólo `PUT`/`DELETE`
                       registrados ahí, para `safe.update`/`safe.delete`),
                       de ahí un `405` en vez de un `404` más obvio. El
                       selector nunca había funcionado — es el único
                       lugar que usa `SafeField` (`AccountCreateWizard.tsx`/
                       `Accounts.tsx` ya llamaban a `/safe-names` bien).
                       De paso, `SafeField`/`PlatformField` no manejaban
                       `isLoading`/`isError` de su query — mostraban "No
                       se encontraron…" tanto si de verdad no había
                       resultados como si la request seguía cargando o
                       había fallado; corregido en los dos.
  src/components/AccountCreateWizard.tsx  botón + wizard "Crear cuenta" del
                       toolbar de la tab Cuentas — tipo de sistema ->
                       plataforma -> safe -> campos. "Additional Properties"
                       es una lista libre clave/valor con sugerencias por
                       tipo de sistema (`suggestedProperties`, sólo UI —
                       "Get Platform Details" no expone ese esquema, ver
                       app/domain/accounts.py). Gateado por `account.create`
                       + `operator+`. **Rediseño (2026-09-16, a pedido de
                       Alex, calcado del Add Account real):** panel a
                       pantalla completa anclado a la derecha (`Sheet`, no
                       `Dialog` — el sidebar de PAM Web Console queda visible) con
                       stepper vertical clickeable (sólo los pasos ya
                       completados); reemplaza el modal chico centrado
                       original.
  src/components/SafeCreateDialog.tsx  botón + diálogo "Crear safe" del
                       toolbar de la tab Safes, precargado con
                       `/safes/defaults`, CPM vía `CpmField`. Gateado por
                       `safe.create` + `operator+`.
  src/lib/roles.ts     roleAtLeast() (jerarquía de rbac.py en el front).
  src/components/MultiSelectField.tsx  (2026-09-28) selector de varios
                       valores para parámetros `multiselect` de Reportes —
                       DropdownMenu con checkboxes (no se cierra al tildar),
                       valor = cadena separada por comas en el orden de
                       `options` (misma selección = misma cache key).
  src/components/ExportCsvButton.tsx  botón "Exportar CSV" por recurso
                       (apiBlob -> saveBlob). Va en el `toolbar` del DataTable.
  src/components/AccountPasswordDialog.tsx  diálogo "Cambiar sólo en la
                       Vault" / "Especificar próxima contraseña (CPM)"
                       (distinto de "Cambiar ahora" del `AccountDetailPanel`,
                       donde el CPM elige el valor): acá el operador tipea
                       la contraseña en un input antes de previsualizar.
                       Controlado, con `mode` fijo desde el ítem del menú
                       "Más" que lo abrió. Gateado por
                       `account.change.vault_only` / `account.change.next`
                       + `tenant_admin+`.
  src/components/PlatformDetailPanel.tsx  (2026-09-29, a pedido de Alex)
                       detalle de una plataforma, mismo lenguaje visual que
                       `AccountDetailPanel` (encabezado fijo con estado/ID/
                       tipo/clase/PSM + barra de acciones Descargar
                       configuración/Duplicar/Activar-Desactivar/Borrar,
                       gateadas por rol). Pestañas Resumen (política del
                       CPM en mosaicos — rotación/verificación/
                       reconciliación —, PSM + opciones de sesión, flujos
                       de acceso, cuentas vinculadas) / Configuración
                       (cada sección de `GET /API/Platforms` con nombres en
                       español, `LABELS`; lo desconocido se muestra con la
                       clave humanizada) / JSON (objeto crudo + copiar).
                       Todo sale de la fila de la grilla; `Platforms.tsx`
                       guarda el ID elegido (no la fila) para que el panel
                       refleje el estado nuevo tras activar/desactivar.
                       `PlatformActivateSection`/`DuplicateSection`/
                       `DeleteSection` pasaron a ser controlados
                       (`open`/`onClose`), sin botón propio.
                       **Pestaña Configuración (mismo día):**
                       `PlatformConfigExplorer.tsx` — la configuración
                       COMPLETA (el árbol de `editor.aspx` del PVWA, que
                       PAM Web Console no puede llamar: se autentica con la sesión del
                       navegador), leída del paquete de "Export Platform"
                       vía `GET .../platforms/{id}/config`
                       (`app/domain/platform_config.py`). Sólo lectura —
                       la API no permite editar esos parámetros. Ver
                       `docs/specs/platform-config-viewer.md`.
  src/components/PlatformPsmSection.tsx  sección "Gestión de sesiones (PSM)"
                       en el detalle de una plataforma (`DetailDrawer` ganó
                       la prop `platformId`) — PSM actual + botón "Cambiar"
                       (`PsmServerField.tsx`, selector) ->
                       `<DryRunDialog method="PUT">`, con el warning del bug
                       conocido de CyberArk siempre visible al editar.
                       Gateado por `platform.psm.update` + `approver+`. Ver
                       `docs/specs/done/platform-psm-migration.md`.
  src/components/PlatformBulkPsmDialog.tsx  (2026-09-30) "Mover N
                       plataforma(s) de PSM" — mismo patrón preview→tabla
                       de resultados→confirmar que
                       `AccountBulkMoveDialog.tsx` (no el genérico
                       `DryRunDialog`, la forma de `bulk-psm-update`/
                       `.../preview` no es la de `writeop.run_write`),
                       `PsmServerField` para el destino. `pages/
                       Platforms.tsx` suma selección de filas
                       (`DataTable` `selectedIds`, compartida entre los
                       `DataTable` de cada grupo colapsable de
                       `systemType`) + barra flotante inferior "Mover de
                       PSM", visible sólo `approver+`. Ver
                       `docs/specs/done/platform-psm-bulk-migration.md`.
  src/components/CredentialsBackupSection.tsx  caja en `Reports.tsx`:
                       razón -> "Descargar backup cifrado" de TODAS las
                       cuentas del tenant (`apiBlob` extendido con
                       `method`/`body`, antes sólo GET). Gateado por
                       `account.reveal.bulk` + `tenant_admin+`.
  src/components/ItrReportSection.tsx  (2026-09-17) caja en
                       `Reports.tsx`, junto a `CredentialsBackupSection` —
                       razón OPCIONAL -> "Generar reporte ITR" ->
                       `apiBlob` POST -> `saveBlob`. Gateado sólo por
                       `tenant_admin+` (es lectura, no toca CyberArk).
                       **(2026-09-18)** checkbox "Incluir Sesiones"
                       (`include_sessions` en el body) + `InfraFilesSection`
                       interna: lista/sube (`apiForm`, multipart, varios
                       archivos)/borra los `HealthCheck_*.json` que
                       alimentan la pestaña Infraestructura + checklist
                       "Pestañas a incluir" (`OPTIONAL_TABS`, mismos ids
                       que `report_builder.TAB_ORDER` menos "sessions") +
                       checkbox "Usar caché de datos crudos"
                       (`use_cache`, con aviso de que es sólo para
                       testear cambios al reporte).
  src/components/AppShell.tsx  (2026-09-28) el shell de la app, extraído de
                       `App.tsx` y rediseñado: sidebar navy con marca +
                       botón contraer (sólo íconos, `w-16`, preferencia en
                       `localStorage` `pam:sidebar-collapsed`), selector de
                       espacio (tarjeta "Espacio: X"; el menú muestra ícono
                       + descripción de cada espacio), ítems con ícono
                       agrupados con títulos (Operación: Inventario/
                       Sesiones/Salud y cumplimiento/Gestión), la página
                       activa se lleva a la vista sola, y el usuario al pie
                       (avatar de iniciales, nombre, rol en español vía
                       `lib/roles.ts` `roleLabel`) con menú Mi cuenta /
                       Cerrar sesión (`scope=current`) / Cerrar sesión en
                       todos los equipos (`scope=all`). Barra superior con
                       la ubicación (espacio › página); en celular, menú
                       hamburguesa + avatar. `useSpaces()`/
                       `useAnyTenantAdmin()` viven acá ahora.
                       **(2026-09-30)** el `Topbar` suma
                       `components/NotificationsBell.tsx` — campanita con
                       badge (inspirada en "Messages" del PVWA): dropdown
                       con incidentes/mantenimientos de status.cyberark.com
                       (comparte la queryKey `["vendor-status","cyberark"]`
                       con `pages/CyberArkStatus.tsx`, no duplica el
                       request), link "Ver todo" a `/cyberark-status`.
                       Pensada para sumar más tipos de evento de PAM Web Console más
                       adelante — hoy "Aprobaciones" sigue con su propio
                       badge en el nav, sin fusionar.
  src/App.tsx           rutas + guards (Protected/AdminOnly/
                       TenantAdminOnly); el shell está en `AppShell.tsx`.
                       Historia del nav: sidebar navy con selector de **espacios**
                       (2026-09-15, `docs/specs/done/nav-spaces.md`) —
                       Operación/Identity/Alero/Reportes/Administración
                       (Alero sumado 2026-09-21), con
                       ícono por espacio (`lucide-react`); el espacio
                       activo se deriva de la ruta actual (nunca se
                       persiste). Cada espacio agrupa ítems planos (un
                       recurso = una página = un ítem, sin sub-tabs
                       internas). La navegación conserva el query string
                       (`nav({ pathname, search: loc.search })`) para que
                       el `?tenant=` elegido sobreviva al cambiar de
                       página. Topbar, menú de usuario. Rutas protegidas.
  src/lib/format.ts        fmtDate / dash para las grillas
  Dockerfile           multi-stage: node build -> nginx. nginx.conf: SPA
                       fallback + proxy /api/ + `include security-headers.conf`
                       (CSP + headers, en :80 y :443). Túnel SSH a 8080.
connector/            futuro, si se desparkea Self-Hosted.
```

### Cómo fluye un request de inventario

`GET /tenants/globex/accounts`
→ `tenant_client("globex", session)`: carga `Tenant` de la DB → `get_secret("globex")` (descifra `secrets.age`) → `build_client(tenant, secret)` → `client.authenticate()` (OAuth2 contra el Identity del tenant)
→ `_run(...)` ejecuta `client.list_accounts()` (paginado) y llama a `audit.record(result="ok", detail={"count": N})`
→ `client.close()` al salir del ctx manager
→ responde `{"tenant", "count", "accounts": [...]}`.

### Cómo fluye una escritura (Fase 2)

`POST /tenants/globex/accounts/42/update {changes, dry_run, reason}`
→ `require_tenant_access(operator)` (rol RBAC efectivo — único gate, ya
resuelto acá) → `writeop.run_write(...)`:
1. si ejecuta: `reason` ≥ 8 chars, si no 422.
2. si ejecuta: rate-limit `pam:write:<op>:<tenant>` (Redis; si cae, permite).
3. `tenant_client(...)` → `fetch(client)` (estado actual) → `build_plan(current, changes)` (JSON Patch + diff, puro).
4. circuit breaker: `plan.object_count > PAM_WRITE_OBJECT_CAP` → 409.
5. `dry_run` → audita (`dry_run=true`) y devuelve `{diff, patch, warnings}`, **sin tocar CyberArk**.
6. si no hay cambios reales → no llama a CyberArk.
7. `apply(client, plan)` → `PATCH /API/Accounts/42` → re-fetch.
8. audita `account.update` (`dry_run=false`, `before`/`after` de los campos que cambian, `reason`).

### Decisiones que importan al modificar el flujo

- **Un `CyberArkClient` por request.** No compartir clientes entre
  tenants (aislamiento). Lo único que se reusa es el bearer token de ISPSS,
  en una caché en memoria con clave = URL de Identity + client_id + hash
  del secret (nunca se comparte entre credenciales distintas), TTL ≤ 600 s
  (`PAM_CYBERARK_TOKEN_CACHE_SECONDS`, 0 = apagada) e invalidación ante un
  401. Ver `docs/specs/cyberark-token-cache.md`.
- **El secret store es una interfaz** (`get_secret(ref) -> dict`). Hoy es un
  JSON cifrado con `age`; en Fase 2 pasa a OpenBao **sin tocar el resto**.
  No acoplar código a la implementación de archivo.
- **`seed/tenants.json` nunca lleva credenciales**, solo URLs +
  `secret_ref`. El `secret_ref` matchea una clave del JSON descifrado.
- **`audit.record()` calcula el hash con `_payload()`** — si se cambia el
  set de campos del audit, hay que actualizar `_payload` en `record` **y**
  en `verify_chain` juntos, o la cadena queda inválida.
- **La migración 0002 pone un trigger** que bloquea UPDATE/DELETE en
  `audit_log` a nivel Postgres. No intentar editar filas de audit.
- **`asyncio.to_thread`** para llamadas bloqueantes en handlers async (ej.
  `secrets.available()` que invoca el binario `age`).
- **Escrituras: SIEMPRE por `writeop.run_write`.** No hacer un `client.patch`
  suelto desde un handler — se salta el dry-run, el rate-limit y el audit
  before/after. Una operación nueva de Fase 2 = un endpoint en `writes.py`
  (con el rol RBAC mínimo que le corresponda en `require_tenant_access`/
  `require_role`) + un `build_*_plan` puro en `app/domain/`.
- **`retry_safe=False` para POST de creación.** Un timeout puede haber creado
  el objeto; reintentar duplica. PATCH/DELETE son idempotentes (`retry_safe`
  default True). `retrieve_password` (GET real, pero POST en la API de
  CyberArk) también va con `retry_safe=False` explícito: un retry
  duplicaría el evento "Retrieve" en el audit nativo de CyberArk.
- **`account.delete`: backup ANTES que borrado, y en ese orden.** El `apply`
  hace retrieve → `save_account_backup` (commitea su propia fila) → recién
  ahí `delete_account`. Si el borrado falla después del backup, el backup
  queda igual — es el orden seguro, nunca se borra una cuenta sin backup.

