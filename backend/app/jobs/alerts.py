"""Job ARQ (cron cada `PAM_ALERTS_CHECK_INTERVAL_MINUTES`) que evalúa las
reglas de alerta de Fase 5 y notifica (Teams/email, `app.core.notify`) los
cambios de estado (nueva / sigue activa / empeoró / resuelta), con debounce
y reintento de los avisos no entregados. Ver docs/specs/done/alert-rules.md
y docs/specs/alert-delivery.md.

Reusa `app.api.system_health._compute_snapshot` (mismo cálculo que la
página System Health, incluido el import de una función "privada" de un
módulo de router — mismo patrón que ya usa `tests/unit/test_system_health.py`,
no vale la pena mover esa función a `app/core/` sólo para este segundo
caller). La regla `approval_pending` lee directo de `approval_requests`.

No usa `app.core.tenants.tenant_client` (levanta `HTTPException`, pensado
para un request real) — arma el cliente a mano por tenant, mismo patrón
que `app/jobs/inventory.py`: un tenant que falla en `component_down` se
reporta como "tenant inalcanzable" (es justamente lo que la regla quiere
detectar), nunca aborta la corrida completa.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.system_health import _compute_snapshot
from app.config import get_settings
from app.core import audit, notify
from app.core.approvals import aware
from app.core.secrets import SecretStoreError, get_secret
from app.cyberark import CyberArkClient, CyberArkError, build_client
from app.cyberark.auth import AuthError
from app.db import SessionLocal
from app.domain.alerts import (
    RULE_APPROVAL_PENDING,
    RULE_COMPONENT_DOWN,
    RULE_VENDOR_STATUS,
    Problem,
    approval_pending_problems,
    component_down_problems,
    severity_of,
    vendor_status_problems,
    worsened,
)
from app.models import AlertState, ApprovalRequest, ComponentInstanceSeen, Deployment, Tenant
from app.vendor_status import VendorStatusError, fetch_summary

logger = logging.getLogger(__name__)


async def _snapshot_for_alerts(tenant: Tenant) -> dict:
    """Nunca lanza -- mismo criterio que
    `app.api.system_health._snapshot_for_fanout`: un tenant caído se
    reporta como {"error": "..."} en vez de tumbar la corrida."""
    max_conc = get_settings().cyberark_max_concurrency
    try:
        secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
    except SecretStoreError as e:
        return {"error": f"secret store: {e}"}

    client: CyberArkClient = build_client(
        tenant,
        secret,
        max_concurrency=max_conc,
        token_cache_seconds=get_settings().cyberark_token_cache_seconds,
    )
    try:
        await client.authenticate()
        return await _compute_snapshot(client)
    except (AuthError, CyberArkError) as e:
        return {"error": str(e)}
    except Exception as e:  # un tenant no debe romper la corrida completa
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        await client.close()


def _icon(rule: str, status: str, detail: dict | None) -> str:
    """Ícono para distinguir de un vistazo en el chat."""
    if status == "resuelta":
        return "✅ "
    d = detail or {}
    if rule == RULE_VENDOR_STATUS:
        return "🔴 " if d.get("type") == "incident" else "🛠️ "
    if rule == RULE_APPROVAL_PENDING:
        return "⏳ "
    if rule == RULE_COMPONENT_DOWN:
        total, connected = d.get("total"), d.get("connected")
        # degradación parcial (algunas instancias conectadas) vs caída total
        return "🟠 " if total and connected else "🔴 "
    return ""


_STATUS_LABEL = {
    "nueva": "Alerta",
    "sigue activa": "Sigue activa",
    "empeoró": "Empeoró",
    "resuelta": "Resuelta",
}

_RULE_LABEL = {
    RULE_COMPONENT_DOWN: "Componente o tenant caído",
    RULE_APPROVAL_PENDING: "Aprobación pendiente",
    RULE_VENDOR_STATUS: "Aviso de CyberArk (status.cyberark.com)",
}

_FOOTER = "Generado automáticamente por PAM Web Console, ver /audit (acciones alert.rule.*)."


def _subject(rule: str, status: str, summary: str, detail: dict | None = None) -> str:
    return f"{_icon(rule, status, detail)}[PAM Web Console] {_STATUS_LABEL[status]}: {summary}"


def _ui_link(rule: str) -> str:
    """Link a la página de PAM Web Console donde se atiende la regla; vacío si no hay
    `PAM_ALERTS_UI_BASE_URL` configurada."""
    base = get_settings().alerts_ui_base_url.rstrip("/")
    page = {RULE_COMPONENT_DOWN: "/system-health", RULE_APPROVAL_PENDING: "/approvals"}.get(rule)
    return f"{base}{page}" if base and page else ""


def _ago(delta: timedelta) -> str:
    """ "5 min", "3 h 10 min", "2 d 4 h" — tiempo relativo para la tarjeta."""
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def _notification(
    rule: str,
    tenant_id: str | None,
    target: str,
    status: str,
    summary: str,
    detail: dict | None,
    *,
    opened_at: datetime | None = None,
    notify_count: int = 0,
    now: datetime | None = None,
) -> notify.Notification:
    """Arma el aviso (docs/specs/alert-delivery.md, req. 6). El resumen va
    en el título y no se repite en los datos."""
    d = detail or {}
    facts: list[tuple[str, str]] = []
    if tenant_id:
        name = d.get("tenant_name")
        facts.append(("Tenant", f"{name} ({tenant_id})" if name else tenant_id))
    facts.append(("Regla", _RULE_LABEL.get(rule, rule)))
    facts.append(("Estado", status))
    if opened_at is not None and now is not None and status != "nueva":
        facts.append(("Abierta hace", _ago(now - aware(opened_at))))
    if notify_count:
        facts.append(("Avisos enviados", str(notify_count)))
    if d.get("ignored_instances") and status != "resuelta":
        facts.append(
            (
                "No contadas (sin conexión hace días)",
                ", ".join(d["ignored_instances"]),
            )
        )
    if d.get("latest_update"):
        facts.append(("Última actualización", str(d["latest_update"])))
    link = d.get("url") or _ui_link(rule)
    return notify.Notification(
        title=_subject(rule, status, summary, d),
        severity="good" if status == "resuelta" else severity_of(rule, target, d),
        facts=facts,
        link=link,
        link_label="Ver en status.cyberark.com"
        if rule == RULE_VENDOR_STATUS
        else "Abrir en PAM Web Console",
        footer=_FOOTER,
    )


def _row_notification(row: AlertState, status: str, now: datetime) -> notify.Notification:
    return _notification(
        row.rule,
        row.tenant_id,
        row.target,
        status,
        row.summary,
        row.detail,
        opened_at=row.opened_at,
        notify_count=row.notify_count,
        now=now,
    )


def sample_messages(kind: str = "all") -> list[notify.Notification]:
    """Avisos de EJEMPLO para `manage.py test-alert`: mismo formato que los
    reales (título, severidad, datos, link) con datos inventados y marcados
    "[PRUEBA]". No toca la DB ni ningún tenant."""
    samples: dict[str, tuple[str, str, str, str, dict, str | None]] = {
        "component": (
            RULE_COMPONENT_DOWN,
            "nueva",
            "PSM",
            "Initech: PSM sin instancias conectadas (0/13)",
            {"tenant_name": "Initech", "total": 13, "connected": 0},
            "initech",
        ),
        "partial": (
            RULE_COMPONENT_DOWN,
            "nueva",
            "PSM",
            "Initech: PSM con instancias desconectadas (11/13)",
            {"tenant_name": "Initech", "total": 13, "connected": 11},
            "initech",
        ),
        "worse": (
            RULE_COMPONENT_DOWN,
            "empeoró",
            "PSM",
            "Initech: PSM con instancias desconectadas (5/13)",
            {"tenant_name": "Initech", "total": 13, "connected": 5},
            "initech",
        ),
        "approval": (
            RULE_APPROVAL_PENDING,
            "nueva",
            "approval-demo",
            "Aprobación pendiente hace 5h: account.delete sobre cuenta-demo (pidió operador)",
            {"tenant_name": "Initech"},
            "initech",
        ),
        "vendor": (
            RULE_VENDOR_STATUS,
            "nueva",
            "notice-demo",
            "CyberArk - Mantenimiento: Mantenimiento de ejemplo "
            "(programado: dom 04-oct 04:30 a 07:00 (hora de Chile))",
            {
                "type": "planned",
                "latest_update": "Texto de ejemplo de la última actualización.",
                "url": "https://status.cyberark.com",
            },
            None,
        ),
        "resolved": (
            RULE_COMPONENT_DOWN,
            "resuelta",
            "PSM",
            "Initech: PSM con instancias desconectadas (11/13)",
            {"tenant_name": "Initech", "total": 13, "connected": 11},
            "initech",
        ),
    }
    chosen = samples if kind == "all" else {kind: samples[kind]}
    now = datetime.now(UTC)
    out = []
    for rule, status, target, summary, detail, tenant_id in chosen.values():
        n = _notification(
            rule,
            tenant_id,
            target,
            status,
            summary,
            detail,
            opened_at=now - timedelta(hours=2, minutes=15),
            notify_count=0 if status == "nueva" else 1,
            now=now,
        )
        out.append(replace(n, title="[PRUEBA] " + n.title))
    return out


async def _deliver(session: AsyncSession, row: AlertState, status: str, now: datetime) -> bool:
    """Intenta entregar el aviso `status` de `row`. Si algún canal lo recibe,
    lo da por notificado (`last_notified_at`/`notify_count`, limpia
    `pending_notify`); si no, lo deja en `pending_notify` para que la
    próxima corrida lo reintente, y audita el fallo sólo la primera vez
    (no en cada reintento, para no inundar `audit_log`)."""
    delivered = await notify.send(_row_notification(row, status, now))
    if delivered:
        row.pending_notify = None
        if status != "resuelta":
            row.last_notified_at = now
            row.notify_count += 1
        return True
    if row.pending_notify is None:
        await audit.record(
            session,
            operator="system",
            role="system",
            action="alert.rule.notify_failed",
            result="error",
            tenant_id=row.tenant_id,
            target=row.target,
            detail={"rule": row.rule, "summary": row.summary, "status": status},
        )
    row.pending_notify = status
    return False


async def _retry_resolved_pending(session: AsyncSession, rule: str, now: datetime) -> None:
    """Reintenta los "resuelta" que no se entregaron (esas filas ya no están
    entre las abiertas, así que el ciclo normal de `_sync_rule` no las ve)."""
    rows = (
        (
            await session.execute(
                select(AlertState).where(
                    AlertState.rule == rule,
                    AlertState.resolved_at.is_not(None),
                    AlertState.pending_notify == "resuelta",
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await _deliver(session, row, "resuelta", now)


async def _audit_rule(session: AsyncSession, action: str, row: AlertState, **extra: object) -> None:
    await audit.record(
        session,
        operator="system",
        role="system",
        action=action,
        result="ok",
        tenant_id=row.tenant_id,
        target=row.target,
        detail={"rule": row.rule, "summary": row.summary, **extra},
    )


async def _sync_rule(
    session: AsyncSession,
    rule: str,
    problems: list[Problem],
    now: datetime,
    *,
    renotify: bool = True,
    confirm_runs: int = 1,
    resolve_runs: int = 1,
) -> dict:
    """Cruza los problemas activos AHORA contra las filas `AlertState`
    abiertas de esta regla: abre las nuevas, recuerda las que siguen
    activas hace mucho, avisa si empeoraron y resuelve las que ya no
    aparecen. Una sola transacción por regla. `renotify=False` para reglas
    informativas (ej. avisos del fabricante) que se notifican una sola vez
    al abrir y al resolver.

    Debounce (docs/specs/alert-delivery.md, req. 3): un problema nuevo queda
    como candidato (`confirmed_at` NULL, sin avisar ni auditar) hasta verse
    en `confirm_runs` corridas seguidas; si desaparece antes, se descarta.
    Una alerta confirmada se resuelve tras `resolve_runs` corridas limpias
    seguidas. Con 1 y 1 se comporta como antes del debounce.

    Un aviso que ningún canal entregó queda en `pending_notify` y se
    reintenta en cada corrida hasta entregarse."""
    renotify_after = timedelta(hours=get_settings().alerts_renotify_after_hours)

    await _retry_resolved_pending(session, rule, now)

    open_rows = (
        (
            await session.execute(
                select(AlertState).where(AlertState.rule == rule, AlertState.resolved_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    open_by_key = {(r.tenant_id, r.target): r for r in open_rows}
    current_keys: set[tuple[str | None, str]] = set()
    opened = renotified = escalated = resolved = 0

    for p in problems:
        key = (p.tenant_id, p.target)
        current_keys.add(key)
        row = open_by_key.get(key)
        if row is None:
            row = AlertState(
                rule=rule,
                tenant_id=p.tenant_id,
                target=p.target,
                summary=p.summary,
                detail=p.detail,
                opened_at=now,
                last_notified_at=now,
                notify_count=0,
                consecutive_hits=0,
                consecutive_misses=0,
            )
            session.add(row)

        row.consecutive_misses = 0
        if row.confirmed_at is None:
            # candidato: se confirma recién tras `confirm_runs` corridas seguidas
            row.consecutive_hits = (row.consecutive_hits or 0) + 1
            row.summary = p.summary
            row.detail = p.detail
            if row.consecutive_hits < confirm_runs:
                continue
            row.confirmed_at = now
            await _deliver(session, row, "nueva", now)
            await _audit_rule(session, "alert.rule.opened", row)
            opened += 1
            continue

        was_worse = worsened(row.detail, p.detail)
        row.summary = p.summary
        row.detail = p.detail
        if was_worse:
            # perdió más instancias (ej. 11/13 -> 5/13): se avisa ya, sin
            # esperar las horas de re-aviso (req. 4)
            if await _deliver(session, row, "empeoró", now):
                escalated += 1
            await _audit_rule(session, "alert.rule.escalated", row)
        elif row.pending_notify:
            # el aviso anterior (nueva / sigue activa / empeoró) no se
            # entregó: se reintenta tal cual, sin esperar las horas de re-aviso
            await _deliver(session, row, row.pending_notify, now)
        elif (
            renotify
            and now - aware(row.last_notified_at) >= renotify_after
            and await _deliver(session, row, "sigue activa", now)
        ):
            await _audit_rule(session, "alert.rule.renotified", row, notify_count=row.notify_count)
            renotified += 1

    for key, row in open_by_key.items():
        if key in current_keys:
            continue
        if row.confirmed_at is None:
            # candidato que desapareció antes de confirmarse: nunca se avisó
            # ni se auditó, se descarta sin rastro (req. 3)
            await session.delete(row)
            continue
        row.consecutive_misses = (row.consecutive_misses or 0) + 1
        if row.consecutive_misses < resolve_runs:
            continue
        row.resolved_at = now
        if row.pending_notify == "nueva":
            # se abrió y se resolvió sin que el aviso de apertura llegara a
            # entregarse: un "resuelta" suelto sería ruido
            row.pending_notify = None
        else:
            await _deliver(session, row, "resuelta", now)
        await _audit_rule(session, "alert.rule.resolved", row)
        resolved += 1

    await session.commit()
    result = {"opened": opened, "renotified": renotified, "resolved": resolved}
    if escalated:
        result["escalated"] = escalated
    return result


async def _update_seen_connected(
    tenant_id: str, snapshot: dict, now: datetime
) -> dict[tuple[str, str], datetime]:
    """Registra como vistas ahora las instancias conectadas del snapshot y
    devuelve {(componente, usuario): última vez vista conectada} del
    tenant, para descartar instancias fantasma en `component_down`
    (docs/specs/alert-delivery.md, req. 7). Un snapshot con error no
    registra nada."""
    async with SessionLocal() as session:
        for c in snapshot.get("components") or []:
            for inst in c.get("instances") or []:
                user = inst.get("user")
                if inst.get("connected") and user:
                    await session.merge(
                        ComponentInstanceSeen(
                            tenant_id=tenant_id,
                            component_id=str(c.get("id")),
                            instance_user=str(user),
                            last_connected_at=now,
                        )
                    )
        await session.commit()
        rows = (
            (
                await session.execute(
                    select(ComponentInstanceSeen).where(
                        ComponentInstanceSeen.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return {(r.component_id, r.instance_user): aware(r.last_connected_at) for r in rows}


async def check_alert_rules(ctx: dict) -> dict:
    """Job ARQ (cron, ver `app.jobs.worker.WorkerSettings`). `ctx` es el
    contexto estándar de ARQ (redis, job_id, ...), sin uso acá."""
    if not get_settings().alerts_enabled:
        return {"skipped": "alerts_enabled=False"}

    now = datetime.now(UTC)

    async with SessionLocal() as session:
        tenants = (
            (
                await session.execute(
                    select(Tenant).where(
                        Tenant.deployment == Deployment.privilege_cloud,
                        Tenant.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

    tenant_names = {t.id: t.name for t in tenants}
    stale_after = timedelta(days=get_settings().alerts_stale_instance_days)
    component_problems: list[Problem] = []
    for tenant in tenants:
        snapshot = await _snapshot_for_alerts(tenant)
        seen = await _update_seen_connected(tenant.id, snapshot, now)
        component_problems += component_down_problems(
            tenant.id,
            tenant.name,
            snapshot,
            now=now,
            stale_after=stale_after,
            seen_connected=seen,
        )

    async with SessionLocal() as session:
        pending_rows = (
            (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.status == "pending")
                )
            )
            .scalars()
            .all()
        )
    pending = [
        {
            "id": a.id,
            "tenant_id": a.tenant_id,
            "feature": a.feature,
            "target": a.target,
            "requested_by": a.requested_by,
            "created_at": aware(a.created_at),
            "tenant_name": tenant_names.get(a.tenant_id),
        }
        for a in pending_rows
    ]
    approval_problems = approval_pending_problems(
        pending,
        stale_after=timedelta(hours=get_settings().alerts_approval_stale_hours),
        now=now,
    )

    s = get_settings()
    async with SessionLocal() as session:
        component_result = await _sync_rule(
            session,
            RULE_COMPONENT_DOWN,
            component_problems,
            now,
            confirm_runs=s.alerts_confirm_runs,
            resolve_runs=s.alerts_resolve_runs,
        )
    async with SessionLocal() as session:
        approval_result = await _sync_rule(session, RULE_APPROVAL_PENDING, approval_problems, now)

    result = {"component_down": component_result, "approval_pending": approval_result}

    # status.cyberark.com: si no responde NO se toca la regla (resolver todo
    # por un fallo de red mandaría falsos "resuelta" a Teams).
    try:
        vendor_problems = vendor_status_problems(
            await fetch_summary(), get_settings().alerts_timezone
        )
    except VendorStatusError as e:
        logger.warning("check_alert_rules: vendor_status omitida (%s)", e)
        result["vendor_status"] = {"skipped": str(e)}
    else:
        async with SessionLocal() as session:
            result["vendor_status"] = await _sync_rule(
                session, RULE_VENDOR_STATUS, vendor_problems, now, renotify=False
            )
    global _last_check_at
    _last_check_at = now
    logger.info("check_alert_rules: %s", result)
    return result


# Momento de la última corrida de `check_alert_rules` en ESTE proceso del
# worker (el heartbeat corre en el mismo proceso). None tras un reinicio,
# hasta la primera corrida.
_last_check_at: datetime | None = None


async def alerts_heartbeat(ctx: dict) -> dict:
    """Job ARQ (cron cada hora, ver `app.jobs.worker.WorkerSettings`): a la
    hora local `PAM_ALERTS_HEARTBEAT_HOUR` manda a Teams un "PAM Web Console alertas:
    OK" corto con cuántas alertas hay activas. Si deja de llegar, el flujo
    de Teams o el worker están caídos (docs/specs/alert-delivery.md)."""
    s = get_settings()
    if not s.alerts_enabled or s.alerts_heartbeat_hour < 0:
        return {"skipped": "heartbeat desactivado"}

    now = datetime.now(UTC)
    local = now.astimezone(ZoneInfo(s.alerts_timezone))
    if local.hour != s.alerts_heartbeat_hour:
        return {"skipped": "no es la hora del heartbeat"}

    async with SessionLocal() as session:
        active = (
            await session.execute(
                select(func.count()).select_from(AlertState).where(AlertState.resolved_at.is_(None))
            )
        ).scalar_one()
        undelivered = (
            await session.execute(
                select(func.count())
                .select_from(AlertState)
                .where(AlertState.pending_notify.is_not(None))
            )
        ).scalar_one()

        facts = [("Alertas activas", str(active))]
        if undelivered:
            facts.append(("Avisos sin entregar (se reintentan)", str(undelivered)))
        if _last_check_at is not None:
            facts.append(("Última corrida del chequeo", f"hace {_ago(now - _last_check_at)}"))
        delivered = await notify.send(
            notify.Notification(
                title="✅ [PAM Web Console] Alertas: OK",
                severity="good",
                facts=facts,
                link=_ui_link(RULE_COMPONENT_DOWN),
                footer="Generado automáticamente por PAM Web Console.",
            )
        )
        await audit.record(
            session,
            operator="system",
            role="system",
            action="alert.heartbeat.sent",
            result="ok" if delivered else "error",
            detail={"active": active, "undelivered": undelivered},
        )
        await session.commit()
    return {"sent": delivered, "active": active, "undelivered": undelivered}
