# Reglas de alerta (Slack/mail)

- **Estado:** cerrada (primer corte) — canal y fix de componente degradado
  probados en el server contra Initech (2026-09-30). La entrega (severidad,
  debounce) sigue en `docs/specs/alert-delivery.md`.
- **Roadmap:** Fase 5, ítem "Reglas de alerta (Slack/mail): cuenta nueva
  sin gestión, safe sin CPM, componente caído, sesión fuera de horario,
  etc." (`docs/PLAN.md` §7, `docs/plan/fase-5.md`)
- **Fecha:** 2026-09-30

## Problema

PAM Web Console detecta hoy varias condiciones que ameritan atención operativa
(componente/tenant caído en System Health, una solicitud de aprobación
que nadie resuelve) pero sólo se ven si alguien entra a mirar la UI. No
hay forma de que le lleguen a Alex/al equipo sin que alguien esté
navegando PAM Web Console en ese momento.

## Alcance

**Entra en este corte:**
- Motor de reglas genérico, corrido por un job ARQ periódico (mismo
  patrón que el sync nocturno de `app/jobs/inventory.py`), con estado
  persistido para no re-notificar la misma condición en cada corrida
  (abre → recuerda si sigue activa mucho tiempo → resuelve).
- Dos reglas v1 (elegidas por Alex, priorizando lo que ya se puede
  calcular con datos existentes, sin inventar heurísticas nuevas):
  1. **Componente/tenant caído** — reusa el mismo cálculo de
     `app/api/system_health.py` (`_compute_snapshot`): Vault
     desconectado, un componente monitoreado (CPM/PSM/AIM) con **menos
     instancias conectadas que el total** (`connected < total`,
     `total > 0`) — incluye tanto la caída total (0 conectadas) como una
     degradación parcial (ej. 1 de 13 instancias caída) —, o el tenant
     entero inalcanzable (auth/secret store/CyberArk caído). **Revisado
     2026-09-30:** el v1 original sólo cubría `connected == 0`
     (caída total); probando contra Initech apareció el caso real — PSM
     11/13 y CPM 3/6 conectados, ninguno en 0 — y Alex pidió que
     cualquier instancia caída alertara, no sólo la caída completa.
  2. **Aprobación pendiente sin resolver** — una `ApprovalRequest` con
     `status=pending` hace más de `PAM_ALERTS_APPROVAL_STALE_HOURS`
     (default 4h) sin que otro operador la apruebe/rechace/cancele.
- **Canal v1: webhook de Teams** (2026-09-30, revisitado — ver "Decisión
  de canal" abajo). el MSP no usa Slack (confirmado con Alex); sí usa
  Microsoft Teams, con flujos de "Flujos de trabajo"/Power Automate ya en
  uso (Alex tiene uno que reenvía tickets a un chat). PAM Web Console hace `POST` a
  la URL de un flujo con la plantilla **"Enviar alertas de webhook a un
  chat"** (apunta a un chat, no a un canal de equipo — Alex no tenía
  permiso para crear canales en los equipos disponibles; el chat elegido
  es "Alertas PAM Web Console"). El `POST` debe ser una **Adaptive Card** válida (lo
  exige la propia plantilla, no acepta JSON plano) — `notify._adaptive_card()`
  arma una tarjeta simple con `subject`/`body` como dos `TextBlock`. No
  requiere ninguna cuenta de correo ni credencial de M365, sólo la URL
  del webhook como secreto. El envío
  se diseña como canales plugeables (`app/core/notify.py`, `send()`
  despacha en paralelo a los que estén configurados) para sumar otro
  canal después sin tocar el motor de reglas (`app/jobs/alerts.py`) ni
  las reglas puras (`app/domain/alerts.py`). SMTP queda implementado como
  canal alternativo, sin usar por ahora.
- **Decisión de canal (2026-09-30):** el corte original de este mismo día
  eligió email/SMTP por descarte (sin Slack, sin canal de chat "elegido"
  todavía). Al revisar el pendiente de prueba, Alex prefirió no meter el
  M365 corporativo del MSP para esto — se cambia a un webhook de
  Teams, que no depende de ninguna cuenta de correo ni de credenciales
  corporativas, y encaja con cómo ya trabaja el equipo (Teams, no mail).
  Canal destino: canal nuevo dedicado a PAM Web Console/PAM (a crear en Teams,
  pendiente la URL del webhook).
- Sin ningún canal configurado (`PAM_ALERTS_TEAMS_WEBHOOK_URL` y
  `PAM_ALERTS_SMTP_HOST` vacíos, default), el motor sigue corriendo y
  auditando cada apertura/recordatorio/resolución — sólo no sale la
  notificación. Mismo criterio de degradación que `app.core.cache` ante
  Redis caído: nunca bloquea el resto por un canal externo no
  configurado.
- CLI `check-alerts-now` (igual que `sync-accounts-now`) para correrlo a
  mano y probar sin esperar al cron.
- Auditado (`alert.rule.opened`/`.renotified`/`.resolved`,
  `operator="system"`, mismo criterio que `account.sync` del sync
  nocturno) — visible desde la página Audit existente, sin página propia
  en este corte.

**Queda afuera de este corte** (se suma después sin rehacer el motor):
- Slack como canal real (la interfaz `notify.send()` ya lo permite).
- Más reglas: "cuenta nueva sin gestión", "safe sin CPM", "sesión fuera
  de horario" (quedan como ideas del roadmap original, no las pidió Alex
  para este corte).
- Página propia de alertas en el frontend (hoy se ven en `/audit`,
  filtrando por acción).
- Configurar destinatarios/reglas desde la UI — v1 es por variables de
  entorno (`PAM_ALERTS_*`), como el resto de la config de PAM Web Console.

## Requisitos

1. **Un problema nuevo dispara una notificación "abierta" una sola vez.**
   Dado que un tenant tiene el Vault desconectado y no había una alerta
   abierta para ese (tenant, componente), cuando corre el chequeo,
   entonces se crea una fila `AlertState` (`opened_at=ahora`), se manda
   un email y se audita `alert.rule.opened`. La siguiente corrida, con el
   Vault todavía desconectado, NO manda un mail nuevo.
2. **Una alerta que sigue activa mucho tiempo recuerda, no spamea.** Dado
   que una alerta sigue abierta y pasaron ≥ `PAM_ALERTS_RENOTIFY_AFTER_HOURS`
   (default 6h) desde el último aviso, cuando corre el chequeo, entonces
   se manda un recordatorio y se audita `alert.rule.renotified`. Antes de
   cumplirse ese plazo, no se manda nada aunque el problema siga.
3. **Un problema que desaparece se resuelve.** Dado que una alerta estaba
   abierta y en la corrida actual esa condición ya no aparece (el
   componente volvió a conectar, la aprobación se decidió), entonces se
   marca `resolved_at=ahora`, se manda un email de resolución y se
   audita `alert.rule.resolved`.
4. **Un tenant/regla que falla no rompe el resto.** Igual criterio que
   `app/jobs/inventory.py` — un tenant sin poder autenticar no debe
   frenar la evaluación de los demás tenants ni de la otra regla.
5. **Sin ningún canal configurado, el motor igual audita.** Con
   `PAM_ALERTS_TEAMS_WEBHOOK_URL=""`/`PAM_ALERTS_SMTP_HOST=""`
   (default) → `notify.send()` loguea y vuelve sin lanzar;
   `AlertState`/audit se escriben igual.

## Diseño técnico

- `app/models/alert_state.py` — `AlertState` (migración `0019`):
  `id` (int autoincrement), `rule` (str), `tenant_id` (str, nullable —
  por si una regla futura es global), `target` (str — id de componente,
  `"_tenant"`, o id de la `ApprovalRequest`), `summary`/`detail` (texto y
  JSON para el cuerpo del mail), `opened_at`/`last_notified_at`/
  `resolved_at` (timestamps), `notify_count`. Único
  `(rule, tenant_id, target)` — se reabre (nueva fila) si se vuelve a
  disparar después de resuelta, en vez de reciclar la fila vieja (más
  simple, y el historial de aperturas pasadas queda en `audit_log`, no
  hace falta duplicarlo acá).
- `app/domain/alerts.py` — funciones puras: `component_down_problems(...)`
  y `approval_pending_problems(...)`, ambas devuelven `list[Problem]`
  (dataclass `rule`/`tenant_id`/`target`/`summary`/`detail`). Sin I/O, sin
  DB — mismo criterio que el resto de `app/domain/`.
- `app/core/notify.py` — `send(subject, body)` despacha en paralelo
  (`asyncio.gather`) a `_send_teams` (POST vía `httpx.AsyncClient`, JSON
  `{"subject", "text"}` al webhook de Teams) y `_send_email` (SMTP vía
  `smtplib`, stdlib, en `asyncio.to_thread` por bloqueante). Cada canal
  sin configurar es un no-op silencioso; un fallo de cualquiera de los
  dos se loguea y no afecta al otro ni relanza.
- `app/jobs/alerts.py` — `check_alert_rules(ctx)`: arma los `Problem` de
  las dos reglas (reusa `app.api.system_health._compute_snapshot` para
  `component_down`, reconstruyendo el cliente por tenant igual que
  `app/jobs/inventory.py`), y los sincroniza contra `AlertState` con
  `_sync_rule()` (abre/recuerda/resuelve, ver Requisitos 1-3).
- `app/jobs/worker.py` — `check_alert_rules` se suma a `functions` +
  `cron_jobs` (cada `PAM_ALERTS_CHECK_INTERVAL_MINUTES`, default 15,
  vía `minute={0,15,30,45}`).
- `manage.py check-alerts-now` — corre `check_alert_rules({})` a mano.
- Config nueva en `app/config.py` (prefijo `PAM_ALERTS_`):
  `enabled` (default true), `check_interval_minutes` (15),
  `approval_stale_hours` (4), `renotify_after_hours` (6),
  `teams_webhook_url` (""), `smtp_host` (""), `smtp_port` (587),
  `smtp_user`, `smtp_password`, `smtp_from`, `smtp_use_tls` (true),
  `email_to` (csv de destinatarios).
- `app/core/audit_catalog.py` — nuevas `action`:
  `alert.rule.opened`/`.renotified`/`.resolved` (categoría "Reportes",
  junto a `system_health.*`). De paso se registra `vendor_status.view`
  (gap real: quedó sin clasificar desde la feature de la sesión
  anterior, `test_audit_catalog.py` lo habría detectado en la próxima
  corrida de CI).

## Tareas

- [x] Modelo `AlertState` + migración `0019`.
- [x] `app/domain/alerts.py` (reglas puras) + tests.
- [x] `app/core/notify.py` (canal email) + tests.
- [x] `app/jobs/alerts.py` (`check_alert_rules`, `_sync_rule`) + tests.
- [x] Wiring en `app/jobs/worker.py` + `manage.py check-alerts-now`.
- [x] Config nueva en `app/config.py`.
- [x] Registrar acciones nuevas (+ `vendor_status.view`) en
      `app/core/audit_catalog.py`.
- [x] `app/core/notify.py` gana el canal Teams (`_send_teams`, webhook
      vía `httpx`, payload Adaptive Card) + tests (2026-09-30).
- [x] Flujo de Teams creado y activo (2026-09-30): "Flujos de trabajo" →
      plantilla "Enviar alertas de webhook a un chat" → chat "Alertas
      PAM Web Console". URL del webhook copiada.
- [x] `PAM_ALERTS_TEAMS_WEBHOOK_URL` cargada en el `.env` del server
      (2026-09-30).
- [x] Probado en el server (2026-09-30): `check-alerts-now` corrió
      limpio contra los tenants reales (sin condiciones activas en ese
      momento, `opened: 0` en ambas reglas — nada que notificar). El
      canal en sí se probó por separado, llamando a `notify.send()`
      directo desde el contenedor — la Adaptive Card llegó bien al chat
      "Alertas PAM Web Console" (título + cuerpo). Fuera del alcance de esta spec,
      encontrado de paso: el tenant Umbrella devuelve `302` (redirect a
      login) en vez de datos en varios endpoints de `ComponentsMonitoring*`/
      `LiveSessions`/`Accounts` — pinta a sesión/token del service
      account vencido. El chequeo no crasheó, pero tampoco lo clasificó
      como "tenant caído" para la regla — posible gap en cómo
      `_snapshot_for_alerts` interpreta esa respuesta, a investigar
      aparte.
- [x] **Componente degradado (no sólo caída total)** — encontrado
      probando contra Initech (2026-09-30): PSM 11/13 y CPM 3/6 conectados
      no disparaban nada porque la regla exigía `connected == 0`. Ahora
      dispara con cualquier `connected < total` (`app/domain/alerts.py`),
      con distinto texto de resumen según sea caída total o parcial.
      Tests nuevos (`test_component_down_degradado_parcial_tambien_es_problema`).
- [x] Pendiente confirmar en el server: correr `check-alerts-now` de
      nuevo contra Initech y verificar que ahora sí abre `AlertState` para
      PSM/CPM y llega el mensaje al chat de Teams.
- [x] **Regla `vendor_status` (2026-10-01)** — los incidentes y
      mantenimientos (en curso o programados) de status.cyberark.com, los
      mismos que muestra la campana de notificaciones, ahora también llegan
      a Teams. Regla global (`tenant_id=None`), sin re-aviso periódico: un
      mensaje al aparecer el aviso y otro al resolverse (`renotify=False`
      en `_sync_rule`). Si status.cyberark.com no responde se omite la
      regla en esa corrida (no se resuelven avisos abiertos por un fallo de
      red). Tests en `tests/unit/test_jobs_alerts.py`.
