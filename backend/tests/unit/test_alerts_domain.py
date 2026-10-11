"""app.domain.alerts — reglas de alerta puras (Fase 5). Ver
docs/specs/done/alert-rules.md."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.alerts import (
    RULE_APPROVAL_PENDING,
    RULE_COMPONENT_DOWN,
    RULE_VENDOR_STATUS,
    approval_pending_problems,
    component_down_problems,
    severity_of,
    worsened,
)


def test_component_down_tenant_inalcanzable():
    problems = component_down_problems("koa", "Globex", {"error": "access_denied"})
    assert len(problems) == 1
    p = problems[0]
    assert p.rule == RULE_COMPONENT_DOWN
    assert p.tenant_id == "koa"
    assert p.target == "_tenant"
    assert "access_denied" in p.summary


def test_component_down_vault_desconectado():
    snapshot = {
        "vault": {"available": True, "connected": False, "ip": "10.0.0.1"},
        "components": [],
    }
    problems = component_down_problems("koa", "Globex", snapshot)
    assert len(problems) == 1
    assert problems[0].target == "Vault"
    assert "Vault" in problems[0].summary


def test_component_down_vault_no_disponible_no_es_problema():
    """`available: False` significa que PAM Web Console no obtuvo el dato (mismo
    criterio que la página System Health), no que el Vault esté caído —
    no debe generar alerta."""
    snapshot = {"vault": {"available": False}, "components": []}
    assert component_down_problems("koa", "Globex", snapshot) == []


def test_component_down_componente_sin_instancias_conectadas():
    snapshot = {
        "vault": {"available": True, "connected": True},
        "components": [
            {"id": "CPM", "name": "CPM", "available": True, "connected": 0, "total": 2},
            {"id": "PSM", "name": "PSM", "available": True, "connected": 1, "total": 2},
        ],
    }
    problems = component_down_problems("koa", "Globex", snapshot)
    assert len(problems) == 2
    cpm = next(p for p in problems if p.target == "CPM")
    psm = next(p for p in problems if p.target == "PSM")
    assert "0/2" in cpm.summary
    assert "1/2" in psm.summary


def test_component_down_degradado_parcial_tambien_es_problema():
    """Caso real: Initech con 11/13 PSM y 3/6 CPM conectados — ninguno
    llega a 0, pero ambos están degradados y deben alertar (2026-09-30,
    antes la regla sólo cubría la caída total)."""
    snapshot = {
        "vault": {"available": True, "connected": True},
        "components": [
            {
                "id": "PSM",
                "name": "PSM y PSM for SSH",
                "available": True,
                "connected": 11,
                "total": 13,
            },
            {"id": "CPM", "name": "CPM", "available": True, "connected": 3, "total": 6},
        ],
    }
    problems = component_down_problems("initech", "Initech", snapshot)
    assert len(problems) == 2
    psm = next(p for p in problems if p.target == "PSM")
    cpm = next(p for p in problems if p.target == "CPM")
    assert "11/13" in psm.summary
    assert "3/6" in cpm.summary


def test_component_down_componente_sin_instancias_totales_no_es_problema():
    """`total: 0` (nada monitoreado todavía) no es lo mismo que "todo
    caído" -- no debe generar alerta."""
    snapshot = {
        "vault": {"available": True, "connected": True},
        "components": [{"id": "AIM", "name": "AIM", "available": True, "connected": 0, "total": 0}],
    }
    assert component_down_problems("koa", "Globex", snapshot) == []


def test_component_down_todo_ok_sin_problemas():
    snapshot = {
        "vault": {"available": True, "connected": True},
        "components": [{"id": "CPM", "name": "CPM", "available": True, "connected": 2, "total": 2}],
    }
    assert component_down_problems("koa", "Globex", snapshot) == []


def test_approval_pending_vencida_genera_problema():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    pending = [
        {
            "id": "abc123",
            "tenant_id": "koa",
            "feature": "safe.delete",
            "target": "SAFE1",
            "requested_by": "alex",
            "created_at": now - timedelta(hours=5),
        }
    ]
    problems = approval_pending_problems(pending, stale_after=timedelta(hours=4), now=now)
    assert len(problems) == 1
    p = problems[0]
    assert p.rule == RULE_APPROVAL_PENDING
    assert p.tenant_id == "koa"
    assert p.target == "abc123"
    assert "5h" in p.summary
    assert "safe.delete" in p.summary
    assert "SAFE1" in p.summary
    assert "alex" in p.summary


def test_approval_pending_reciente_no_genera_problema():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    pending = [
        {
            "id": "abc123",
            "tenant_id": "koa",
            "feature": "safe.delete",
            "target": "SAFE1",
            "requested_by": "alex",
            "created_at": now - timedelta(hours=1),
        }
    ]
    assert approval_pending_problems(pending, stale_after=timedelta(hours=4), now=now) == []


def test_approval_pending_lista_vacia():
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    assert approval_pending_problems([], stale_after=timedelta(hours=4), now=now) == []


def test_format_window_convierte_a_hora_de_chile():
    from app.domain.alerts import format_window

    # octubre: Chile en horario de verano (UTC-3)
    assert (
        format_window("2026-10-04T07:30:00.000Z", "2026-10-04T10:30:00.000Z", "America/Santiago")
        == "dom 04-oct 04:30 a 07:30 (hora de Chile)"
    )
    # cruza de día: muestra fecha en ambos extremos
    assert (
        format_window("2026-10-04T02:00:00.000Z", "2026-10-04T04:00:00.000Z", "America/Santiago")
        == "sáb 03-oct 23:00 a dom 04-oct 01:00 (hora de Chile)"
    )
    # sin término
    assert format_window("2026-10-04T07:30:00Z", None, "America/Santiago").endswith(
        "04:30 (hora de Chile)"
    )
    assert format_window(None, None, "America/Santiago") == ""


def test_severidad_por_regla():
    assert severity_of(RULE_COMPONENT_DOWN, "_tenant", {"error": "x"}) == "critical"
    assert severity_of(RULE_COMPONENT_DOWN, "Vault", {"connected": False}) == "critical"
    assert severity_of(RULE_COMPONENT_DOWN, "PSM", {"connected": 0, "total": 13}) == "critical"
    assert severity_of(RULE_COMPONENT_DOWN, "PSM", {"connected": 11, "total": 13}) == "warning"
    assert severity_of(RULE_APPROVAL_PENDING, "abc", {}) == "warning"
    assert severity_of(RULE_VENDOR_STATUS, "n1", {"type": "incident"}) == "critical"
    assert severity_of(RULE_VENDOR_STATUS, "n1", {"type": "planned"}) == "info"


def test_problem_expone_su_severidad():
    (p,) = component_down_problems("koa", "Globex", {"error": "access_denied"})
    assert p.severity == "critical"


def test_worsened_solo_si_bajan_las_instancias_conectadas():
    assert worsened({"connected": 11, "total": 13}, {"connected": 5, "total": 13})
    assert not worsened({"connected": 5}, {"connected": 5})
    assert not worsened({"connected": 5}, {"connected": 9})
    assert not worsened({"connected": 11}, {"error": "x"})
    assert not worsened(None, {"connected": 1})


# --- instancias fantasma (docs/specs/alert-delivery.md, req. 7) ---

_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
_WEEK = timedelta(days=7)


def _ts(days_ago: float) -> int:
    return int((_NOW - timedelta(days=days_ago)).timestamp())


def _pvwa(*instances: dict) -> dict:
    return {
        "vault": {"available": True, "connected": True},
        "components": [
            {
                "id": "PVWA",
                "name": "Web Portal",
                "available": True,
                "connected": sum(1 for i in instances if i["connected"]),
                "total": len(instances),
                "instances": list(instances),
            }
        ],
    }


_OK = {"user": "PVWAAppUser1", "connected": True, "last_logon": _ts(3)}
# el registro de reserva que trae cada tenant: versión 0.0.0.0, sin login nunca
_RESERVA = {"user": "PVWAAppUser", "connected": False, "last_logon": None}


def test_component_down_ignora_la_instancia_de_reserva():
    """Caso real (Initech, Acme, Umbrella, lab): PVWA 1/2 por el
    `PVWAAppUser` que nunca se conectó -- no es una caída."""
    snapshot = _pvwa(_OK, _RESERVA)
    assert (
        component_down_problems("initech", "Initech", snapshot, now=_NOW, stale_after=_WEEK) == []
    )


def test_component_down_ignora_servidor_retirado_hace_meses():
    retirado = {"user": "PVWAAppUser", "connected": False, "last_logon": _ts(1400)}
    snapshot = _pvwa(_OK, retirado)
    assert component_down_problems("koa", "Globex", snapshot, now=_NOW, stale_after=_WEEK) == []


def test_component_down_cuenta_la_que_se_logueo_hace_poco():
    caida = {"user": "PSMApp_CON02", "connected": False, "last_logon": _ts(0.5)}
    (p,) = component_down_problems(
        "lab", "Lab", _pvwa(_OK, caida, _RESERVA), now=_NOW, stale_after=_WEEK
    )
    assert "1/2" in p.summary
    assert p.detail["connected"] == 1
    assert p.detail["total"] == 2
    assert p.detail["ignored_instances"] == ["PVWAAppUser"]


def test_component_down_cuenta_la_que_pam_vio_conectada_hace_poco():
    """`last_logon` es el último inicio de sesión, no el último contacto:
    una instancia que llevaba semanas conectada y se cae tiene un
    `last_logon` viejo, pero PAM Web Console la vio conectada en la corrida anterior."""
    larga = {"user": "PSMApp_X", "connected": False, "last_logon": _ts(40)}
    seen = {("PVWA", "PSMApp_X"): _NOW - timedelta(minutes=15)}
    (p,) = component_down_problems(
        "koa", "Globex", _pvwa(_OK, larga), now=_NOW, stale_after=_WEEK, seen_connected=seen
    )
    assert "1/2" in p.summary
    assert p.detail["ignored_instances"] == []


def test_component_down_todas_fantasma_no_es_problema():
    snapshot = _pvwa(_RESERVA)
    assert component_down_problems("koa", "Globex", snapshot, now=_NOW, stale_after=_WEEK) == []


def test_component_down_last_logon_en_milisegundos():
    caida = {"user": "X", "connected": False, "last_logon": _ts(1) * 1000}
    (p,) = component_down_problems("koa", "K", _pvwa(_OK, caida), now=_NOW, stale_after=_WEEK)
    assert "1/2" in p.summary
