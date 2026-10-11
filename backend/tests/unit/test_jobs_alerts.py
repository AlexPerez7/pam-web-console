"""app.jobs.alerts — motor de reglas de alerta (Fase 5, worker ARQ). Ver
docs/specs/done/alert-rules.md."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.jobs.alerts as jobs_alerts
from app.core.secrets import SecretStoreError
from app.models import AlertState, ApprovalRequest, AuditLog, Base, Deployment, Tenant
from app.vendor_status import VendorStatusError

SNAPSHOTS: dict[str, dict] = {}
VENDOR: dict = {}  # lo que devuelve fetch_summary; una excepción = status.cyberark.com caído
SECRETS = {"globex": {"client_id": "x"}, "acme": {"client_id": "x"}}
DELIVERY = {"ok": True}  # lo que devuelve notify.send; False = ningún canal entregó


class FakeClient:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.closed = False

    async def authenticate(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


def _ok_snapshot() -> dict:
    return {"vault": {"available": True, "connected": True}, "components": []}


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(jobs_alerts, "SessionLocal", maker)

    SNAPSHOTS.clear()
    SNAPSHOTS["globex"] = _ok_snapshot()
    VENDOR.clear()
    VENDOR.update({"active_notices": [], "upcoming_notices": []})

    async def fake_fetch_summary() -> dict:
        if "error" in VENDOR:
            raise VendorStatusError(VENDOR["error"])
        return VENDOR

    monkeypatch.setattr(jobs_alerts, "fetch_summary", fake_fetch_summary)

    def fake_get_secret(ref: str) -> dict:
        if ref not in SECRETS:
            raise SecretStoreError(f"no hay secreto para '{ref}'")
        return SECRETS[ref]

    async def fake_compute_snapshot(client: FakeClient) -> dict:
        return SNAPSHOTS.get(client.tenant_id, _ok_snapshot())

    sent: list[tuple[str, str]] = []  # (título, cuerpo en texto plano)
    notifications: list = []

    DELIVERY["ok"] = True

    async def fake_send(n) -> bool:
        sent.append((n.title, jobs_alerts.notify.render_text(n)))
        notifications.append(n)
        return DELIVERY["ok"]

    monkeypatch.setattr(jobs_alerts, "get_secret", fake_get_secret)
    monkeypatch.setattr(
        jobs_alerts, "build_client", lambda tenant, secret, **kw: FakeClient(tenant.id)
    )
    monkeypatch.setattr(jobs_alerts, "_compute_snapshot", fake_compute_snapshot)
    monkeypatch.setattr(jobs_alerts.notify, "send", fake_send)
    # Los tests de abajo asumen sin debounce (se abre y resuelve en una
    # corrida); los del debounce lo prenden con `_with_settings`.
    _with_settings(monkeypatch, alerts_confirm_runs=1, alerts_resolve_runs=1)

    async with maker() as session:
        session.add(
            Tenant(
                id="globex",
                name="Globex",
                client="K",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://x",
                secret_ref="globex",
            )
        )
        await session.commit()

    return {"maker": maker, "sent": sent, "notifications": notifications}


async def test_componente_caido_abre_una_alerta_y_notifica(env):
    SNAPSHOTS["globex"] = {
        "vault": {"available": True, "connected": False},
        "components": [],
    }
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 1, "renotified": 0, "resolved": 0}
    assert len(env["sent"]) == 1
    assert "Vault" in env["sent"][0][0]

    async with env["maker"]() as session:
        rows = (await session.execute(select(AlertState))).scalars().all()
        assert len(rows) == 1
        assert rows[0].rule == "component_down"
        assert rows[0].tenant_id == "globex"
        assert rows[0].target == "Vault"
        assert rows[0].resolved_at is None

        logs = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "alert.rule.opened")))
            .scalars()
            .all()
        )
        assert len(logs) == 1
        assert logs[0].operator == "system"


async def test_segunda_corrida_sin_cambios_no_vuelve_a_notificar(env):
    SNAPSHOTS["globex"] = {"vault": {"available": True, "connected": False}, "components": []}
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 1

    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 0, "renotified": 0, "resolved": 0}
    assert len(env["sent"]) == 1  # sigue en 1: no se re-notificó


async def test_alerta_resuelta_cuando_el_componente_vuelve(env):
    SNAPSHOTS["globex"] = {"vault": {"available": True, "connected": False}, "components": []}
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 1

    SNAPSHOTS["globex"] = _ok_snapshot()
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 0, "renotified": 0, "resolved": 1}
    assert len(env["sent"]) == 2
    assert "Resuelta" in env["sent"][1][0]

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.resolved_at is not None


async def test_alerta_que_sigue_activa_mucho_tiempo_reenvia(env):
    SNAPSHOTS["globex"] = {"vault": {"available": True, "connected": False}, "components": []}
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 1

    # simula que pasó más de PAM_ALERTS_RENOTIFY_AFTER_HOURS (default 24h)
    # desde el último aviso, sin que el problema se haya resuelto
    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        row.last_notified_at = datetime.now(UTC) - timedelta(hours=25)
        await session.commit()

    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 0, "renotified": 1, "resolved": 0}
    assert len(env["sent"]) == 2
    assert "Sigue activa" in env["sent"][1][0]

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.notify_count == 2
        assert row.resolved_at is None


async def test_tenant_inalcanzable_genera_problema_con_target_tenant(env):
    SNAPSHOTS["globex"] = {"error": "access_denied"}
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["opened"] == 1

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.target == "_tenant"
        assert "access_denied" in row.summary


async def test_secret_store_caido_se_trata_como_tenant_inalcanzable(env, monkeypatch):
    def fail_secret(ref: str) -> dict:
        raise SecretStoreError("boom")

    monkeypatch.setattr(jobs_alerts, "get_secret", fail_secret)
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["opened"] == 1

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.target == "_tenant"
        assert "secret store" in row.summary


async def test_aprobacion_pendiente_vieja_genera_alerta(env):
    now = datetime.now(UTC)
    async with env["maker"]() as session:
        session.add(
            ApprovalRequest(
                id="approval1",
                tenant_id="globex",
                feature="safe.delete",
                target="SAFE1",
                method="DELETE",
                path="/tenants/globex/safes/SAFE1",
                changes_json={},
                body_json={},
                plan_digest="x",
                diff_json=[],
                warnings_json=[],
                requested_by_id=1,
                requested_by="alex",
                request_reason="motivo",
                status="pending",
                created_at=now - timedelta(hours=10),
                expires_at=now + timedelta(hours=14),
            )
        )
        await session.commit()

    result = await jobs_alerts.check_alert_rules({})
    assert result["approval_pending"] == {"opened": 1, "renotified": 0, "resolved": 0}

    async with env["maker"]() as session:
        rows = (
            (await session.execute(select(AlertState).where(AlertState.rule == "approval_pending")))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].target == "approval1"
        assert "safe.delete" in rows[0].summary


async def test_aprobacion_resuelta_cuando_deja_de_estar_pending(env):
    now = datetime.now(UTC)
    async with env["maker"]() as session:
        session.add(
            ApprovalRequest(
                id="approval1",
                tenant_id="globex",
                feature="safe.delete",
                target="SAFE1",
                method="DELETE",
                path="/tenants/globex/safes/SAFE1",
                changes_json={},
                body_json={},
                plan_digest="x",
                diff_json=[],
                warnings_json=[],
                requested_by_id=1,
                requested_by="alex",
                request_reason="motivo",
                status="pending",
                created_at=now - timedelta(hours=10),
                expires_at=now + timedelta(hours=14),
            )
        )
        await session.commit()

    await jobs_alerts.check_alert_rules({})

    async with env["maker"]() as session:
        approval = await session.get(ApprovalRequest, "approval1")
        approval.status = "approved"
        await session.commit()

    result = await jobs_alerts.check_alert_rules({})
    assert result["approval_pending"] == {"opened": 0, "renotified": 0, "resolved": 1}


async def test_alerts_enabled_false_no_hace_nada(env, monkeypatch):
    monkeypatch.setattr(
        jobs_alerts,
        "get_settings",
        lambda: type(
            "S",
            (),
            {"alerts_enabled": False},
        )(),
    )
    result = await jobs_alerts.check_alert_rules({})
    assert result == {"skipped": "alerts_enabled=False"}
    assert env["sent"] == []


def _notice(id_: str = "n1", type_: str = "incident", timeline_state: str = "current") -> dict:
    return {
        "id": id_,
        "type": type_,
        "state": "investigating",
        "timeline_state": timeline_state,
        "subject": "Degradación en Privilege Cloud",
        "url": "https://status.cyberark.com/notices/n1",
        "begins_at": None,
        "ends_at": None,
        "began_at": "2026-10-01T12:00:00Z",
        "ended_at": None,
        "latest_update": "Estamos investigando",
        "updated_at": None,
    }


async def test_aviso_del_fabricante_notifica_una_vez_y_no_recuerda(env):
    VENDOR["active_notices"] = [_notice()]
    result = await jobs_alerts.check_alert_rules({})
    assert result["vendor_status"] == {"opened": 1, "renotified": 0, "resolved": 0}
    assert len(env["sent"]) == 1
    assert "Incidente: Degradación en Privilege Cloud" in env["sent"][0][0]
    assert env["sent"][0][0].startswith("🔴 [PAM Web Console] Alerta:")
    assert "Degradación" not in env["sent"][0][1]  # el resumen no se repite en el cuerpo
    assert "https://status.cyberark.com/notices/n1" in env["sent"][0][1]
    assert "Tenant:" not in env["sent"][0][1]

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.tenant_id is None
        row.last_notified_at = datetime.now(UTC) - timedelta(hours=25)
        await session.commit()

    result = await jobs_alerts.check_alert_rules({})
    assert result["vendor_status"] == {"opened": 0, "renotified": 0, "resolved": 0}
    assert len(env["sent"]) == 1  # sin re-aviso aunque pasen las horas


async def test_mantenimiento_programado_y_resolucion(env):
    VENDOR["upcoming_notices"] = [_notice("m1", "planned", "future")]
    VENDOR["upcoming_notices"][0]["begins_at"] = "2026-10-05T03:00:00.000Z"
    VENDOR["upcoming_notices"][0]["ends_at"] = "2026-10-05T05:30:00.000Z"
    await jobs_alerts.check_alert_rules({})
    assert "Mantenimiento" in env["sent"][0][0]
    assert env["sent"][0][0].startswith("🛠️ ")
    # 03:00-05:30 UTC = 00:00-02:30 en Chile (UTC-3, horario de verano)
    assert "programado: lun 05-oct 00:00 a 02:30 (hora de Chile)" in env["sent"][0][0]

    VENDOR["upcoming_notices"] = []
    result = await jobs_alerts.check_alert_rules({})
    assert result["vendor_status"] == {"opened": 0, "renotified": 0, "resolved": 1}
    assert env["sent"][1][0].startswith("✅ [PAM Web Console] Resuelta:")


async def test_status_cyberark_caido_no_resuelve_avisos_abiertos(env):
    VENDOR["active_notices"] = [_notice()]
    await jobs_alerts.check_alert_rules({})

    VENDOR.clear()
    VENDOR["error"] = "timeout"
    result = await jobs_alerts.check_alert_rules({})
    assert "skipped" in result["vendor_status"]
    assert len(env["sent"]) == 1  # ningún falso "resuelta"

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.resolved_at is None


async def test_iconos_por_regla_y_severidad(env):
    SNAPSHOTS["globex"] = {
        "vault": {"available": True, "connected": False},
        "components": [
            {"id": "PSM", "name": "PSM", "available": True, "total": 13, "connected": 11}
        ],
    }
    await jobs_alerts.check_alert_rules({})
    subjects = {s for s, _ in env["sent"]}
    assert any(s.startswith("🔴 ") and "Vault" in s for s in subjects)  # caída total
    assert any(s.startswith("🟠 ") and "PSM" in s for s in subjects)  # degradación parcial

    # el cuerpo muestra nombre + id del tenant
    assert all("Tenant: Globex (globex)" in b for _, b in env["sent"])


async def test_aprobacion_pendiente_lleva_icono_y_link_si_hay_base_url(env, monkeypatch):
    class _S:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "alerts_ui_base_url":
                return "https://192.168.10.10/"
            return getattr(self._real, name)

    real = jobs_alerts.get_settings()
    monkeypatch.setattr(jobs_alerts, "get_settings", lambda: _S(real))

    async with env["maker"]() as session:
        session.add(
            ApprovalRequest(
                id="approval-icon",
                tenant_id="globex",
                feature="account.delete",
                target="acc-1",
                method="DELETE",
                path="/tenants/globex/accounts/acc-1",
                changes_json={},
                body_json={},
                plan_digest="x",
                diff_json=[],
                warnings_json=[],
                requested_by_id=1,
                requested_by="alex",
                request_reason="motivo",
                status="pending",
                created_at=datetime.now(UTC) - timedelta(hours=5),
                expires_at=datetime.now(UTC) + timedelta(hours=19),
            )
        )
        await session.commit()

    await jobs_alerts.check_alert_rules({})
    subject, body = next((s, b) for s, b in env["sent"] if "Aprobación pendiente" in s)
    assert subject.startswith("⏳ ")
    assert "https://192.168.10.10/approvals" in body
    assert "Tenant: Globex (globex)" in body


VAULT_DOWN = {"vault": {"available": True, "connected": False}, "components": []}


async def _actions(env, action: str) -> int:
    async with env["maker"]() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
        return len(rows.all())


async def test_aviso_no_entregado_queda_pendiente_y_se_reintenta(env):
    SNAPSHOTS["globex"] = VAULT_DOWN
    DELIVERY["ok"] = False
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["opened"] == 1

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.pending_notify == "nueva"
        assert row.notify_count == 0  # no cuenta como notificada
    assert await _actions(env, "alert.rule.notify_failed") == 1

    # sigue fallando: se reintenta pero no se vuelve a auditar el fallo
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 2
    assert await _actions(env, "alert.rule.notify_failed") == 1

    # el canal vuelve: se entrega el mismo aviso "nueva", sin abrir otra alerta
    DELIVERY["ok"] = True
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 0, "renotified": 0, "resolved": 0}
    assert len(env["sent"]) == 3
    assert env["sent"][2][0].startswith("🔴 [PAM Web Console] Alerta:")

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.pending_notify is None
        assert row.notify_count == 1
    assert await _actions(env, "alert.rule.opened") == 1

    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 3  # ya entregado: no vuelve a salir


async def test_alerta_resuelta_antes_de_entregar_la_apertura_no_manda_resuelta(env):
    SNAPSHOTS["globex"] = VAULT_DOWN
    DELIVERY["ok"] = False
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 1

    DELIVERY["ok"] = True
    SNAPSHOTS["globex"] = _ok_snapshot()
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["resolved"] == 1
    assert len(env["sent"]) == 1  # ningún "Resuelta" suelto

    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.resolved_at is not None
        assert row.pending_notify is None


async def test_resuelta_no_entregada_se_reintenta(env):
    SNAPSHOTS["globex"] = VAULT_DOWN
    await jobs_alerts.check_alert_rules({})

    SNAPSHOTS["globex"] = _ok_snapshot()
    DELIVERY["ok"] = False
    await jobs_alerts.check_alert_rules({})
    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.resolved_at is not None
        assert row.pending_notify == "resuelta"

    DELIVERY["ok"] = True
    await jobs_alerts.check_alert_rules({})
    assert "Resuelta" in env["sent"][-1][0]
    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.pending_notify is None


async def test_recordatorio_no_entregado_se_reintenta_sin_esperar_las_horas(env):
    SNAPSHOTS["globex"] = VAULT_DOWN
    await jobs_alerts.check_alert_rules({})
    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        row.last_notified_at = datetime.now(UTC) - timedelta(hours=25)
        await session.commit()

    DELIVERY["ok"] = False
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["renotified"] == 0  # no se entregó
    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.pending_notify == "sigue activa"
        assert row.notify_count == 1

    DELIVERY["ok"] = True
    await jobs_alerts.check_alert_rules({})  # reintento inmediato (15 min después, no 6 h)
    assert "Sigue activa" in env["sent"][-1][0]
    async with env["maker"]() as session:
        row = (await session.execute(select(AlertState))).scalars().one()
        assert row.pending_notify is None
        assert row.notify_count == 2


def _with_settings(monkeypatch, **overrides):
    real = jobs_alerts.get_settings()

    class _S:
        def __getattr__(self, name):
            return overrides[name] if name in overrides else getattr(real, name)

    monkeypatch.setattr(jobs_alerts, "get_settings", lambda: _S())


def _local_hour() -> int:
    from zoneinfo import ZoneInfo

    return datetime.now(UTC).astimezone(ZoneInfo("America/Santiago")).hour


async def test_heartbeat_manda_el_resumen_a_la_hora_configurada(env, monkeypatch):
    _with_settings(monkeypatch, alerts_heartbeat_hour=_local_hour())
    SNAPSHOTS["globex"] = VAULT_DOWN
    await jobs_alerts.check_alert_rules({})
    env["sent"].clear()

    result = await jobs_alerts.alerts_heartbeat({})
    assert result == {"sent": True, "active": 1, "undelivered": 0}
    subject, body = env["sent"][0]
    assert subject == "✅ [PAM Web Console] Alertas: OK"
    assert "Alertas activas: 1" in body
    assert "Última corrida del chequeo: hace 0 min" in body
    assert await _actions(env, "alert.heartbeat.sent") == 1


async def test_heartbeat_cuenta_los_avisos_sin_entregar(env, monkeypatch):
    _with_settings(monkeypatch, alerts_heartbeat_hour=_local_hour())
    SNAPSHOTS["globex"] = VAULT_DOWN
    DELIVERY["ok"] = False
    await jobs_alerts.check_alert_rules({})
    DELIVERY["ok"] = True

    result = await jobs_alerts.alerts_heartbeat({})
    assert result == {"sent": True, "active": 1, "undelivered": 1}
    assert "Avisos sin entregar" in env["sent"][-1][1]


async def test_heartbeat_fuera_de_hora_o_desactivado_no_manda(env, monkeypatch):
    _with_settings(monkeypatch, alerts_heartbeat_hour=(_local_hour() + 1) % 24)
    assert "skipped" in await jobs_alerts.alerts_heartbeat({})

    _with_settings(monkeypatch, alerts_heartbeat_hour=-1)
    assert "skipped" in await jobs_alerts.alerts_heartbeat({})
    assert env["sent"] == []


def test_sample_messages_cubre_los_tipos_y_se_marcan_como_prueba():
    msgs = jobs_alerts.sample_messages()
    assert len(msgs) == 6
    assert all(n.title.startswith("[PRUEBA] ") for n in msgs)
    iconos = {n.title.removeprefix("[PRUEBA] ").split(" ")[0] for n in msgs}
    assert iconos == {"🔴", "🟠", "⏳", "🛠️", "✅"}
    assert {n.severity for n in msgs} == {"critical", "warning", "info", "good"}
    worse = jobs_alerts.sample_messages("worse")[0]
    assert "Empeoró" in worse.title
    vendor = jobs_alerts.sample_messages("vendor")[0]
    assert vendor.link_label == "Ver en status.cyberark.com"


# --- Debounce, "empeoró" y severidad (docs/specs/alert-delivery.md) ---------


def _psm(connected: int, total: int = 13) -> dict:
    return {
        "vault": {"available": True, "connected": True},
        "components": [
            {"id": "PSM", "name": "PSM", "available": True, "total": total, "connected": connected}
        ],
    }


async def _rows(env) -> list[AlertState]:
    async with env["maker"]() as session:
        return list((await session.execute(select(AlertState))).scalars().all())


async def test_debounce_abre_recien_en_la_segunda_corrida(env, monkeypatch):
    _with_settings(monkeypatch, alerts_confirm_runs=2, alerts_resolve_runs=2)
    SNAPSHOTS["globex"] = VAULT_DOWN

    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 0, "renotified": 0, "resolved": 0}
    assert env["sent"] == []
    assert await _actions(env, "alert.rule.opened") == 0
    (row,) = await _rows(env)
    assert row.confirmed_at is None
    assert row.consecutive_hits == 1

    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["opened"] == 1
    assert len(env["sent"]) == 1
    assert await _actions(env, "alert.rule.opened") == 1
    (row,) = await _rows(env)
    assert row.confirmed_at is not None


async def test_debounce_candidato_que_desaparece_se_descarta_sin_rastro(env, monkeypatch):
    _with_settings(monkeypatch, alerts_confirm_runs=2, alerts_resolve_runs=2)
    SNAPSHOTS["globex"] = VAULT_DOWN
    await jobs_alerts.check_alert_rules({})

    SNAPSHOTS["globex"] = _ok_snapshot()
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"] == {"opened": 0, "renotified": 0, "resolved": 0}
    assert env["sent"] == []
    assert await _rows(env) == []
    assert await _actions(env, "alert.rule.resolved") == 0


async def test_debounce_resuelve_recien_tras_dos_corridas_limpias(env, monkeypatch):
    _with_settings(monkeypatch, alerts_confirm_runs=2, alerts_resolve_runs=2)
    SNAPSHOTS["globex"] = VAULT_DOWN
    await jobs_alerts.check_alert_rules({})
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 1

    SNAPSHOTS["globex"] = _ok_snapshot()
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["resolved"] == 0
    (row,) = await _rows(env)
    assert row.resolved_at is None
    assert row.consecutive_misses == 1

    # vuelve a fallar: el conteo de corridas limpias se reinicia
    SNAPSHOTS["globex"] = VAULT_DOWN
    await jobs_alerts.check_alert_rules({})
    (row,) = await _rows(env)
    assert row.consecutive_misses == 0

    SNAPSHOTS["globex"] = _ok_snapshot()
    await jobs_alerts.check_alert_rules({})
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["resolved"] == 1
    assert "Resuelta" in env["sent"][-1][0]
    assert len(env["sent"]) == 2  # alerta + resuelta, sin avisos por el vaivén


async def test_debounce_no_aplica_a_aprobaciones_pendientes(env, monkeypatch):
    _with_settings(monkeypatch, alerts_confirm_runs=2, alerts_resolve_runs=2)
    now = datetime.now(UTC)
    async with env["maker"]() as session:
        session.add(
            ApprovalRequest(
                id="approval-debounce",
                tenant_id="globex",
                feature="safe.delete",
                target="SAFE1",
                method="DELETE",
                path="/tenants/globex/safes/SAFE1",
                changes_json={},
                body_json={},
                plan_digest="x",
                diff_json=[],
                warnings_json=[],
                requested_by_id=1,
                requested_by="alex",
                request_reason="motivo",
                status="pending",
                created_at=now - timedelta(hours=10),
                expires_at=now + timedelta(hours=14),
            )
        )
        await session.commit()
    result = await jobs_alerts.check_alert_rules({})
    assert result["approval_pending"]["opened"] == 1


async def test_componente_que_empeora_avisa_y_audita(env):
    SNAPSHOTS["globex"] = _psm(11)
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 1

    SNAPSHOTS["globex"] = _psm(5)
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["escalated"] == 1
    assert len(env["sent"]) == 2
    assert "[PAM Web Console] Empeoró:" in env["sent"][1][0]
    assert "5/13" in env["sent"][1][0]
    assert await _actions(env, "alert.rule.escalated") == 1
    (row,) = await _rows(env)
    assert row.notify_count == 2

    # sigue igual de mal: no vuelve a avisar
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 2

    # mejora pero sigue degradado: tampoco es "empeoró"
    SNAPSHOTS["globex"] = _psm(9)
    await jobs_alerts.check_alert_rules({})
    assert len(env["sent"]) == 2


async def test_empeoro_no_entregado_se_reintenta(env):
    SNAPSHOTS["globex"] = _psm(11)
    await jobs_alerts.check_alert_rules({})

    DELIVERY["ok"] = False
    SNAPSHOTS["globex"] = _psm(3)
    await jobs_alerts.check_alert_rules({})
    (row,) = await _rows(env)
    assert row.pending_notify == "empeoró"

    DELIVERY["ok"] = True
    await jobs_alerts.check_alert_rules({})
    assert "Empeoró" in env["sent"][-1][0]
    (row,) = await _rows(env)
    assert row.pending_notify is None


async def test_severidad_y_datos_de_la_tarjeta(env):
    SNAPSHOTS["globex"] = {
        "vault": {"available": True, "connected": False},
        "components": [
            {"id": "PSM", "name": "PSM", "available": True, "total": 13, "connected": 11}
        ],
    }
    await jobs_alerts.check_alert_rules({})
    by_target = {("Vault" if "Vault" in n.title else "PSM"): n for n in env["notifications"]}
    assert by_target["Vault"].severity == "critical"
    assert by_target["PSM"].severity == "warning"
    facts = dict(by_target["PSM"].facts)
    assert facts["Tenant"] == "Globex (globex)"
    assert facts["Estado"] == "nueva"
    assert by_target["PSM"].link_label == "Abrir en PAM Web Console"

    SNAPSHOTS["globex"] = _ok_snapshot()
    await jobs_alerts.check_alert_rules({})
    resolved = [n for n in env["notifications"] if "Resuelta" in n.title]
    assert len(resolved) == 2
    assert all(n.severity == "good" for n in resolved)
    assert all("Abierta hace" in dict(n.facts) for n in resolved)


async def test_instancia_vista_conectada_alerta_al_caer_aunque_su_login_sea_viejo(env):
    """req. 7: la corrida registra las instancias conectadas; cuando una se
    cae, cuenta aunque su `last_logon` (inicio de sesión) sea de hace
    semanas. El `PVWAAppUser` de reserva nunca cuenta."""
    old = int((datetime.now(UTC) - timedelta(days=40)).timestamp())

    def snap(connected: bool) -> dict:
        instances = [
            {"user": "PSM1", "connected": True, "last_logon": old},
            {"user": "PSM2", "connected": connected, "last_logon": old},
            {"user": "PVWAAppUser", "connected": False, "last_logon": None},
        ]
        return {
            "vault": {"available": True, "connected": True},
            "components": [
                {
                    "id": "SessionManagement",
                    "name": "PSM",
                    "available": True,
                    "connected": sum(1 for i in instances if i["connected"]),
                    "total": 3,
                    "instances": instances,
                }
            ],
        }

    SNAPSHOTS["globex"] = snap(True)
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["opened"] == 0  # la de reserva no cuenta

    SNAPSHOTS["globex"] = snap(False)
    result = await jobs_alerts.check_alert_rules({})
    assert result["component_down"]["opened"] == 1
    assert "1/2" in env["sent"][-1][0]
