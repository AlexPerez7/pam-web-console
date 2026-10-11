# Entrega de alertas a Teams (fiabilidad, ruido y formato)

- **Estado:** hecha (puntos 1, 2 y 3 implementados, 2026-10-06), falta probar en el server
- **Roadmap:** Fase 5, ítem "Reglas de alerta" — mejora de
  `docs/specs/done/alert-rules.md` (motor y reglas ya existentes)
- **Fecha:** 2026-10-01

> **2026-10-07, tras un día en el server:** llegaban unas 50 tarjetas al
> día, casi todas por instancias fantasma (el `PVWAAppUser` de reserva de
> cada tenant y servidores retirados que siguen registrados), que
> mantenían 11 alertas abiertas para siempre con un re-aviso cada 6 h.
> Se agregó el req. 8 y el re-aviso pasó a cada 24 h (decisión de Alex).

> **2026-10-06:** los tres puntos están implementados y con tests
> (pytest completo en verde con Python 3.12). Lo único pendiente es la
> prueba en el server (última tarea del checklist). La migración del
> debounce quedó como la migración `0021`.

## Problema

El motor de reglas abre, recuerda y resuelve bien, pero la entrega a Teams
es frágil y poco legible:

- `notify.send()` se traga los errores y `_sync_rule` marca la alerta como
  "notificada" igual. Si el flujo de Power Automate está caído o
  desactivado, la alerta se pierde sin rastro y nunca se reintenta.
- Un componente que oscila (ej. PSM 11/13 ↔ 13/13) genera abre/resuelve
  repetidos. Y si empeora (11/13 → 0/13) el `summary` se actualiza sin
  avisar a nadie.
- La tarjeta son dos `TextBlock` de texto plano ("Regla: component_down…"),
  sin color por severidad ni botón al lugar donde se atiende.

## Alcance

**Entra:**
1. **Entrega fiable:** reintento de avisos no entregados + heartbeat diario.
2. **Menos ruido:** debounce de apertura/resolución y aviso explícito
   cuando una alerta empeora.
3. **Tarjeta mejorada:** severidad con color, datos en `FactSet`, botón
   "Abrir en PAM Web Console", tiempo relativo.

**Queda afuera** (se suma después sin rehacer esto):
- Agrupar varios problemas de un tenant en una sola tarjeta.
- Ruteo por severidad a chats distintos y menciones (`@`).
- Silenciar por tenant/ventana de mantenimiento y página `/alerts`.
- Resolver como respuesta en el hilo (requiere Graph o un bot).
- Gap de Umbrella (302 no clasificado como tenant caído): se investiga
  aparte.

## Requisitos

1. **Un aviso no entregado se reintenta.** Dado que se abre una alerta y
   Teams responde error (o timeout), cuando corre el chequeo siguiente,
   entonces se vuelve a intentar el envío hasta que algún canal entregue.
   No se cuenta como notificada (`notify_count`/`last_notified_at`) hasta
   entonces. Lo mismo para "resuelta" y "sigue activa". Sin ningún canal
   configurado, no hay nada que reintentar (se considera entregado, como
   hoy).
2. **Heartbeat diario.** Dado `PAM_ALERTS_HEARTBEAT_HOUR` (hora local de
   `alerts_timezone`, default `9`, `-1` lo desactiva), cuando llega esa
   hora, entonces se manda una tarjeta corta "PAM Web Console alertas: OK — N
   activas, última corrida hace X min". Si la tarjeta deja de llegar, el
   flujo de Teams o el worker están caídos.
3. **Debounce de `component_down`.** Dado un componente que aparece con
   problema, cuando se detecta en `PAM_ALERTS_CONFIRM_RUNS` corridas
   consecutivas (default `2`), entonces recién ahí se abre y notifica.
   Una alerta abierta se resuelve tras `PAM_ALERTS_RESOLVE_RUNS`
   corridas limpias consecutivas (default `2`). Un candidato que
   desaparece antes de confirmarse se descarta sin notificar ni auditar.
   Las reglas `approval_pending` y `vendor_status` usan 1 (ya son
   condiciones estables o con su propio umbral de horas).
4. **Aviso al empeorar.** Dado una alerta `component_down` abierta con
   `connected` = 11 de 13, cuando una corrida posterior ve `connected`
   estrictamente menor (ej. 5/13, o 0/13), entonces se notifica con el
   título "Empeoró" (aunque no hayan pasado las horas de re-aviso), se
   reinicia `last_notified_at` y se audita `alert.rule.escalated`. Si el
   problema pasa de parcial a tenant inalcanzable/Vault desconectado, ya
   es otra clave (`target` distinto) y abre una alerta nueva como hoy.
5. **Severidad.** Cada `Problem` lleva `severity`: `critical` (Vault
   desconectado, componente 0/N, tenant inalcanzable, incidente del
   fabricante), `warning` (componente parcial, aprobación pendiente),
   `info` (mantenimiento programado del fabricante). La resolución es
   siempre `good`.
6. **Tarjeta legible.** Dada cualquier alerta, la Adaptive Card muestra:
   título con ícono, `Container` con `style` según severidad
   (`attention`/`warning`/`accent`/`good`), `FactSet` (Tenant, Regla, Estado,
   Abierta hace, Avisos enviados, última actualización del fabricante si
   aplica), y un `Action.OpenUrl` "Abrir en PAM Web Console" (o "Ver en status.cyberark.com"
   para `vendor_status`). El pie "Generado automáticamente…" se mantiene
   como texto chico.
7. **El canal email sigue funcionando** con el mismo contenido, renderizado
   a texto plano desde la misma estructura.
8. **Sin alertas por instancias fantasma (2026-10-07).** Dada una
   instancia desconectada que PAM Web Console no vio conectada en los últimos
   `PAM_ALERTS_STALE_INSTANCE_DAYS` días (default 7) y cuyo `last_logon`
   en CyberArk también es más viejo (o no existe), no cuenta para
   `component_down`: se descuenta del total, la tarjeta la lista en "No
   contadas" y sigue visible en System Health. Si todas las instancias
   desconectadas son fantasma, no hay alerta. No alcanza con `last_logon`:
   es el último inicio de sesión, no el último contacto, y una instancia
   que lleva semanas conectada lo tiene igual de viejo; por eso PAM Web Console
   guarda cuándo vio conectada cada instancia (tabla
   `component_instances_seen`, migración `0028`). Una caída real que dure
   más de esos días termina resolviéndose sola (se asume dada de baja).
   El re-aviso de una alerta abierta pasa de 6 h a 24 h
   (`PAM_ALERTS_RENOTIFY_AFTER_HOURS`).

## Diseño técnico

- **Modelo `AlertState`:** la migración `0020` (punto 1) agrega
  sólo `pending_notify`; las columnas del debounce van en la migración
  `0026` (punto 2). Columnas nuevas `confirmed_at` (nullable; `NULL` = candidato aún sin notificar),
  `consecutive_hits` (int, default 1), `consecutive_misses` (int, default
  0), `pending_notify` (str nullable: `nueva`/`sigue activa`/`resuelta`/
  `empeoró` — el aviso que falta entregar). Las filas existentes se
  migran con `confirmed_at = opened_at`.
- **`app/domain/alerts.py`:** `Problem` gana `severity: str` (default
  `"warning"`). Las funciones puras asignan la severidad (req. 5).
- **`app/core/notify.py`:**
  - Nuevo `@dataclass Notification` (`title`, `severity`, `facts:
    list[tuple[str, str]]`, `link`, `link_label`, `footer`).
  - `send(n: Notification) -> bool`: despacha en paralelo, devuelve
    `True` si algún canal entregó o si no hay ninguno configurado; `False`
    si hubo canales configurados y todos fallaron. Sigue sin lanzar.
  - `_adaptive_card(n)` arma la tarjeta del req. 6; `_render_text(n)` el
    texto del email.
- **`app/jobs/alerts.py`:**
  - `_sync_rule(..., confirm_runs=1, resolve_runs=1, renotify=True)`
    implementa debounce (req. 3) y escalada (req. 4); cada envío usa el
    resultado de `send()` para decidir si limpia o deja `pending_notify`.
  - Al inicio de `_sync_rule`, antes de evaluar, reintenta las filas con
    `pending_notify` (req. 1) y audita `alert.rule.notify_failed` una vez
    por fila al primer fallo (no en cada reintento, para no inundar
    `audit_log`).
  - `check_alert_rules` pasa `confirm_runs`/`resolve_runs` de config sólo a
    `component_down`.
  - Nuevo job ARQ `alerts_heartbeat` (cron cada hora; manda sólo si es la
    hora configurada y no se mandó hoy). Registrado en `worker.py`.
- **`app/config.py`:** `alerts_confirm_runs` (2), `alerts_resolve_runs` (2),
  `alerts_heartbeat_hour` (9).
- **`app/core/audit_catalog.py`:** registrar `alert.rule.escalated`,
  `alert.rule.notify_failed`, `alert.heartbeat.sent` (categoría "Reportes").
- **`manage.py`:** `test-alert` y `sample_messages` pasan a la nueva
  estructura (mismo comando, para ver la tarjeta nueva sin tocar la DB);
  `check-alerts-now` muestra también los pendientes de entrega.
- **Trade-off:** el debounce retrasa una alerta real ~15 min extra
  (2 corridas × 15 min). Se acepta para evitar el flapping; es
  configurable (`PAM_ALERTS_CONFIRM_RUNS=1` lo desactiva).
- El mensaje a Teams sigue sin llevar secretos ni datos de cuentas; sólo
  nombres de tenant/componente y conteos.

## Tareas

- [x] Req. 8: tabla `component_instances_seen` (migración `0028`, que
      además cierra sin aviso las alertas `component_down` de componentes
      abiertas, para no mandar una tarjeta "Resuelta" por cada fantasma),
      filtro en `component_down_problems`, `alerts_stale_instance_days` (7),
      `alerts_renotify_after_hours` 6 → 24, tests.

- [x] Migración `0020` (`pending_notify`) + columna en `AlertState`.
- [x] Migración `0026` + columnas del debounce en `AlertState` (punto 2).
- [x] `Problem.severity` (`severity_of()`, recalculable en los re-avisos)
      + tests.
- [x] `send() -> bool` + tests (falla de Teams → `False`, sin canales →
      `True`, un canal que entrega aunque otro falle → `True`).
- [x] `Notification`, `_adaptive_card`, `render_text` (punto 3) + tests.
- [x] Reintento de `pending_notify` en `_sync_rule` + tests.
- [x] Debounce (`confirm_runs`/`resolve_runs`) + tests (candidato que
      desaparece, apertura a la 2.ª corrida, resolución a la 2.ª limpia).
- [x] Aviso "empeoró" + tests.
- [x] Job `alerts_heartbeat` + wiring en `worker.py` + tests.
- [x] `alerts_heartbeat_hour` + acciones `alert.rule.notify_failed` y
      `alert.heartbeat.sent` en `audit_catalog.py` (el resto de config y
      acciones llega con los puntos 2 y 3).
- [x] `manage.py test-alert` informa cuántos avisos se entregaron.
- [x] `check-alerts-now` muestra las alertas abiertas, las candidatas y
      los avisos pendientes de entrega; `test-alert worse` muestra el aviso
      "Empeoró".
- [ ] Probar en el server: `test-alert` (ver la tarjeta), apagar el flujo de
      Teams y confirmar el reintento al reactivarlo, esperar un heartbeat.
- [ ] Al cerrar: mover a `docs/specs/done/`, actualizar
      `docs/plan/fase-5.md` y `docs/PLAN.md` §7.
