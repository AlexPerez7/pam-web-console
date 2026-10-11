"""Reglas de alerta (Fase 5, docs/specs/done/alert-rules.md) — funciones puras
que evalúan un estado ya calculado y devuelven los "problemas" activos en
este momento, sin tocar la DB ni enviar nada. `app/jobs/alerts.py` cruza
esto contra `AlertState` para abrir/recordar/resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

RULE_COMPONENT_DOWN = "component_down"
RULE_APPROVAL_PENDING = "approval_pending"
RULE_VENDOR_STATUS = "vendor_status"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


def severity_of(rule: str, target: str, detail: dict | None) -> str:
    """Severidad de un problema (docs/specs/alert-delivery.md, req. 5):
    `critical` = Vault desconectado, componente 0/N, tenant inalcanzable o
    incidente del fabricante; `warning` = componente parcial o aprobación
    pendiente; `info` = mantenimiento del fabricante. Se calcula a partir de
    lo mismo que guarda `AlertState` para poder recalcularla en los re-avisos
    sin persistirla aparte."""
    d = detail or {}
    if rule == RULE_VENDOR_STATUS:
        return SEVERITY_CRITICAL if d.get("type") == "incident" else SEVERITY_INFO
    if rule == RULE_COMPONENT_DOWN:
        if target in ("_tenant", "Vault"):
            return SEVERITY_CRITICAL
        return SEVERITY_WARNING if d.get("connected") else SEVERITY_CRITICAL
    return SEVERITY_WARNING


def worsened(old: dict | None, new: dict | None) -> bool:
    """True si un componente degradado perdió más instancias conectadas
    (ej. 11/13 -> 5/13). Sólo aplica a detalles con `connected` numérico;
    el resto de las reglas nunca "empeora" (req. 4)."""
    before, after = (old or {}).get("connected"), (new or {}).get("connected")
    if not isinstance(before, int) or not isinstance(after, int):
        return False
    return after < before


@dataclass(frozen=True)
class Problem:
    rule: str
    # None en las reglas globales (no de un tenant puntual), ej. vendor_status
    tenant_id: str | None
    target: str
    summary: str
    detail: dict

    @property
    def severity(self) -> str:
        return severity_of(self.rule, self.target, self.detail)


def _epoch_to_dt(value: object) -> datetime | None:
    """`last_logon` de CyberArk: epoch en segundos (a veces en ms o como
    texto). None si no viene o no se entiende."""
    try:
        n = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 1e12:  # milisegundos
        n /= 1000
    return datetime.fromtimestamp(n, tz=UTC)


def _is_stale(
    component_id: str,
    inst: dict,
    now: datetime,
    stale_after: timedelta,
    seen_connected: dict[tuple[str, str], datetime],
) -> bool:
    """Instancia desconectada que se asume dada de baja: ni PAM Web Console la vio
    conectada ni CyberArk registra un login suyo en `stale_after`. Cubre el
    `PVWAAppUser` de reserva (versión 0.0.0.0, sin login nunca) que trae
    cada tenant y los servidores retirados que siguen registrados
    (docs/specs/alert-delivery.md, req. 7)."""
    if inst.get("connected"):
        return False
    last = _epoch_to_dt(inst.get("last_logon"))
    seen = seen_connected.get((component_id, str(inst.get("user") or "")))
    recent = [t for t in (last, seen) if t is not None and now - t < stale_after]
    return not recent


def component_down_problems(
    tenant_id: str,
    tenant_name: str,
    snapshot: dict,
    *,
    now: datetime | None = None,
    stale_after: timedelta | None = None,
    seen_connected: dict[tuple[str, str], datetime] | None = None,
) -> list[Problem]:
    """`snapshot` = el dict que devuelve
    `app.api.system_health._compute_snapshot` ({"vault", "components"}),
    o {"error": "..."} si no se pudo ni siquiera autenticar contra el
    tenant (mismo shape que `_snapshot_for_fanout`).

    Con `now` y `stale_after`, las instancias desconectadas fantasma
    (`_is_stale`) no cuentan: se descuentan del total y se listan en
    `detail["ignored_instances"]`. `seen_connected` = {(componente,
    usuario): última vez que PAM Web Console la vio conectada}. Sin `now` cuenta
    todas, como antes de la req. 7."""
    if "error" in snapshot:
        return [
            Problem(
                rule=RULE_COMPONENT_DOWN,
                tenant_id=tenant_id,
                target="_tenant",
                summary=f"{tenant_name}: no se pudo contactar al tenant ({snapshot['error']})",
                detail={"error": snapshot["error"], "tenant_name": tenant_name},
            )
        ]

    problems: list[Problem] = []
    vault = snapshot.get("vault") or {}
    if vault.get("available") and not vault.get("connected"):
        problems.append(
            Problem(
                rule=RULE_COMPONENT_DOWN,
                tenant_id=tenant_id,
                target="Vault",
                summary=f"{tenant_name}: Vault desconectado",
                detail={**vault, "tenant_name": tenant_name},
            )
        )
    for c in snapshot.get("components") or []:
        total = c.get("total") or 0
        connected = c.get("connected") or 0
        ignored: list[str] = []
        instances = c.get("instances")
        if now is not None and stale_after is not None and isinstance(instances, list):
            cid = str(c.get("id"))
            active = [
                i
                for i in instances
                if not _is_stale(cid, i, now, stale_after, seen_connected or {})
            ]
            ignored = [
                str(i.get("user") or i.get("ip") or "?") for i in instances if i not in active
            ]
            total = len(active)
            connected = sum(1 for i in active if i.get("connected"))
        if c.get("available") and total > 0 and connected < total:
            summary = (
                f"{tenant_name}: {c.get('name')} sin instancias conectadas (0/{total})"
                if connected == 0
                else f"{tenant_name}: {c.get('name')} con instancias desconectadas "
                f"({connected}/{total})"
            )
            problems.append(
                Problem(
                    rule=RULE_COMPONENT_DOWN,
                    tenant_id=tenant_id,
                    target=str(c.get("id")),
                    summary=summary,
                    detail={
                        **c,
                        "connected": connected,
                        "total": total,
                        "ignored_instances": ignored,
                        "tenant_name": tenant_name,
                    },
                )
            )
    return problems


_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fmt_moment(dt: datetime) -> str:
    return f"{_DIAS[dt.weekday()]} {dt.day:02d}-{_MESES[dt.month - 1]} {dt:%H:%M}"


def format_window(begins: str | None, ends: str | None, tz_name: str) -> str:
    """ "sáb 04-oct 03:30 a 06:30 (hora de Chile)" a partir de los ISO-8601
    UTC de status.cyberark.com. Sin `ends` muestra sólo el inicio."""
    tz = ZoneInfo(tz_name)
    start = _parse_utc(begins)
    if start is None:
        return ""
    start = start.astimezone(tz)
    label = "hora de Chile" if tz_name == "America/Santiago" else tz_name
    end = _parse_utc(ends)
    if end is None:
        return f"{_fmt_moment(start)} ({label})"
    end = end.astimezone(tz)
    end_txt = f"{end:%H:%M}" if end.date() == start.date() else _fmt_moment(end)
    return f"{_fmt_moment(start)} a {end_txt} ({label})"


def vendor_status_problems(summary: dict, tz_name: str = "America/Santiago") -> list[Problem]:
    """`summary` = el dict de `app.vendor_status.fetch_summary`. Un problema
    por cada notice en curso (incidente o mantenimiento) o programada del
    fabricante — lo mismo que muestra la campana de notificaciones."""
    problems: list[Problem] = []
    for n in [*summary.get("active_notices", []), *summary.get("upcoming_notices", [])]:
        kind = "Incidente" if n["type"] == "incident" else "Mantenimiento"
        if n.get("begins_at") and n["timeline_state"] == "future":
            when = "programado: " + format_window(n["begins_at"], n.get("ends_at"), tz_name)
        elif n.get("began_at") or n.get("begins_at"):
            when = "desde " + format_window(n.get("began_at") or n["begins_at"], None, tz_name)
        else:
            when = ""
        text = f"CyberArk - {kind}: {n['subject']}" + (f" ({when})" if when else "")
        problems.append(
            Problem(
                rule=RULE_VENDOR_STATUS,
                tenant_id=None,
                target=str(n["id"]),
                summary=text,
                detail=dict(n),
            )
        )
    return problems


def approval_pending_problems(
    pending: list[dict], *, stale_after: timedelta, now: datetime
) -> list[Problem]:
    """`pending` = filas de `ApprovalRequest` con `status="pending"`, como
    dicts `{id, tenant_id, feature, target, requested_by, created_at}`."""
    problems: list[Problem] = []
    for a in pending:
        age = now - a["created_at"]
        if age < stale_after:
            continue
        hours = int(age.total_seconds() // 3600)
        problems.append(
            Problem(
                rule=RULE_APPROVAL_PENDING,
                tenant_id=a["tenant_id"],
                target=str(a["id"]),
                summary=(
                    f"Aprobación pendiente hace {hours}h: {a['feature']} "
                    f"sobre {a['target']} (pidió {a['requested_by']})"
                ),
                detail={
                    "tenant_name": a.get("tenant_name"),
                    "feature": a["feature"],
                    "target": a["target"],
                    "requested_by": a["requested_by"],
                    "created_at": a["created_at"].isoformat(),
                },
            )
        )
    return problems
