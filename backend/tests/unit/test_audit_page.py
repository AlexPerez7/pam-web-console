"""GET /audit (+/summary, /operators) — Tandas 1 y 2 de
docs/specs/done/audit-page.md: señal sobre ruido (lecturas ocultas por
defecto), filtros nuevos, paginación por cursor, label/target_label."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.audit import router as audit_router
from app.core.deps import current_operator
from app.db import get_session
from app.models import ApprovalRequest, AuditLog, Operator, OperatorRole


def _row(
    *,
    action: str,
    result: str = "ok",
    tenant_id: str | None = "globex",
    operator: str = "alex",
    dry_run: bool = False,
    ts: datetime | None = None,
    target: str | None = None,
    detail: dict | None = None,
    request_id: str | None = None,
) -> AuditLog:
    return AuditLog(
        ts=ts or datetime.now(UTC),
        operator=operator,
        role="operator",
        tenant_id=tenant_id,
        action=action,
        target=target,
        detail=detail or {},
        dry_run=dry_run,
        result=result,
        request_id=request_id,
        source_ip=None,
        prev_hash=None,
        hash="x",
    )


@pytest_asyncio.fixture
async def client(session):
    op = Operator(
        username="alex",
        password_hash="x",
        role=OperatorRole.platform_admin,
        totp_enrolled=True,
        is_active=True,
    )
    session.add(op)
    await session.flush()

    app = FastAPI()
    app.include_router(audit_router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_operator] = lambda: op

    with TestClient(app) as c:
        yield c


async def test_oculta_lecturas_ok_por_defecto(session, client):
    session.add_all(
        [
            _row(action="account.list", result="ok"),
            _row(action="account.list", result="denied"),
            _row(action="account.update", result="ok"),
        ]
    )
    await session.commit()

    items = client.get("/audit").json()["items"]
    actions = [(i["action"], i["result"]) for i in items]

    assert ("account.list", "ok") not in actions  # consulta + ok -> ruido, se oculta
    assert ("account.list", "denied") in actions  # consulta pero no-ok -> se muestra
    assert ("account.update", "ok") in actions  # cambio -> se muestra siempre


async def test_include_reads_muestra_todo(session, client):
    session.add_all([_row(action="account.list", result="ok")])
    await session.commit()

    items = client.get("/audit?include_reads=true").json()["items"]
    assert any(i["action"] == "account.list" for i in items)


async def test_dry_run_oculto_salvo_include_dry_run(session, client):
    session.add_all([_row(action="account.update", result="ok", dry_run=True)])
    await session.commit()

    assert client.get("/audit").json()["items"] == []
    items = client.get("/audit?include_dry_run=true").json()["items"]
    assert len(items) == 1


async def test_cada_fila_trae_kind_y_categoria(session, client):
    session.add_all([_row(action="account.reveal", result="ok")])
    await session.commit()

    item = client.get("/audit?include_reads=true").json()["items"][0]
    assert item["kind"] == "sensible"
    assert item["category"] == "Contraseñas y secretos"
    assert item["label"] == "Reveló la contraseña"


async def test_target_label_usa_el_nombre_del_detail(session, client):
    session.add_all(
        [
            _row(action="safe.update", target="123_Safe", detail={"safeName": "GCP-Prod"}),
            _row(
                action="account.create",
                target="45_1",
                detail={"userName": "admin", "address": "10.0.0.5"},
            ),
            _row(action="account.get", target="46_1", detail={}),
        ]
    )
    await session.commit()

    items = {
        i["target"]: i["target_label"]
        for i in client.get("/audit?include_reads=true").json()["items"]
    }
    assert items["123_Safe"] == "GCP-Prod"
    assert items["45_1"] == "admin@10.0.0.5"
    assert items["46_1"] == "46_1"  # sin nombre legible en detail -> cae al ID


async def test_filtra_por_request_id_muestra_todo(session, client):
    session.add_all(
        [
            _row(action="account.get", result="ok", request_id="req-1"),  # oculta por defecto
            _row(action="account.update", result="ok", request_id="req-1", dry_run=True),
            _row(action="account.update", result="ok", request_id="req-1"),
            _row(action="account.update", result="ok", request_id="req-2"),
        ]
    )
    await session.commit()

    items = client.get("/audit?request_id=req-1").json()["items"]
    assert len(items) == 3
    assert all(i["request_id"] == "req-1" for i in items)


async def test_filtra_por_categoria(session, client):
    session.add_all(
        [
            _row(action="account.list", result="ok"),
            _row(action="safe.list", result="ok"),
        ]
    )
    await session.commit()

    items = client.get("/audit?include_reads=true&category=Safes").json()["items"]
    assert [i["action"] for i in items] == ["safe.list"]


async def test_categoria_desconocida_es_422(client):
    resp = client.get("/audit?category=No existe")
    assert resp.status_code == 422


async def test_paginacion_por_cursor(session, client):
    rows = [_row(action="account.update", result="ok") for _ in range(3)]
    session.add_all(rows)
    await session.commit()
    ids = sorted(r.id for r in rows)

    page1 = client.get("/audit?limit=2").json()
    assert [i["id"] for i in page1["items"]] == ids[::-1][:2]
    assert page1["next_before_id"] == ids[1]  # el id más chico de la página

    page2 = client.get(f"/audit?limit=2&before_id={page1['next_before_id']}").json()
    assert [i["id"] for i in page2["items"]] == [ids[0]]
    assert page2["next_before_id"] is None


async def test_filtro_operador_y_resultado(session, client):
    session.add_all(
        [
            _row(action="account.update", result="ok", operator="alex"),
            _row(action="account.update", result="error", operator="bob"),
        ]
    )
    await session.commit()

    items = client.get("/audit?operator=bob").json()["items"]
    assert [i["operator"] for i in items] == ["bob"]

    items = client.get("/audit?result=error").json()["items"]
    assert [i["result"] for i in items] == ["error"]


async def test_filtro_por_varias_acciones(session, client):
    session.add_all(
        [
            _row(action="account.reveal", result="ok"),
            _row(action="recording.play", result="ok"),
            _row(action="account.update", result="ok"),
        ]
    )
    await session.commit()

    items = client.get(
        "/audit?include_reads=true&action=account.reveal&action=recording.play"
    ).json()["items"]
    assert {i["action"] for i in items} == {"account.reveal", "recording.play"}


async def test_summary_cuenta_eventos_sensibles(session, client):
    now = datetime.now(UTC)
    session.add_all(
        [
            _row(action="account.reveal", result="ok", ts=now),
            _row(action="recording.play", result="ok", ts=now),
            _row(action="account.backup.reveal", result="ok", ts=now),
            _row(action="account.get", result="denied", ts=now),
            _row(action="account.update", result="error", ts=now),
            _row(action="account.move.rollback_failed", result="error", ts=now),
            _row(action="auth.login", result="denied", ts=now),
            # fuera del período por defecto (24h) -- no debe contar
            _row(action="account.reveal", result="ok", ts=now - timedelta(days=2)),
        ]
    )
    session.add(
        ApprovalRequest(
            id="apr-1",
            tenant_id="globex",
            feature="safe.delete",
            target="t",
            method="DELETE",
            path="/x",
            changes_json={},
            body_json={},
            plan_digest="d",
            diff_json=[],
            effect=None,
            warnings_json=[],
            requested_by_id=1,
            requested_by="alex",
            request_reason="motivo",
            status="pending",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    await session.commit()

    counts = client.get("/audit/summary").json()["counts"]
    assert counts["passwords_revealed"] == 1
    assert counts["recordings_downloaded"] == 1
    assert counts["credential_backups_accessed"] == 1
    assert counts["access_denied"] == 2  # account.get denegado + auth.login denegado
    assert counts["errors"] == 2
    assert counts["rollbacks_failed"] == 1
    assert counts["failed_logins"] == 1
    assert counts["pending_approvals"] == 1


async def test_operators_list_distinct_y_ordenada(session, client):
    session.add_all(
        [
            _row(action="account.list", operator="zeta"),
            _row(action="account.list", operator="alex"),
            _row(action="account.list", operator="alex"),
        ]
    )
    await session.commit()

    assert client.get("/audit/operators").json() == ["alex", "zeta"]


async def test_verify_ampliado_con_cadena_real(session, client):
    from app.core import audit

    # referencias fuertes: si se recolectan, verify_chain recarga de SQLite
    # y pierde el tzinfo del timestamp, cambiando el hash recalculado (ver
    # test_audit.py).
    a = await audit.record(
        session, operator="alex", role="operator", action="account.get", result="ok"
    )
    b = await audit.record(
        session, operator="alex", role="operator", action="account.update", result="ok"
    )
    await session.commit()

    body = client.get("/audit/verify").json()
    assert body["intact"] is True
    assert body["first_broken_id"] is None
    assert body["checked"] == 2
    assert (body["first_id"], body["last_id"]) == (a.id, b.id)
    assert body["verified_at"]


async def test_export_csv_trae_meta_y_filas(session, client):
    session.add_all(
        [
            _row(action="account.update", result="ok", tenant_id="globex"),
            _row(action="account.update", result="ok", tenant_id="initech-old"),
        ]
    )
    await session.commit()

    resp = client.get("/audit/export?tenant_id=globex&format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert ".csv" in resp.headers["content-disposition"]
    body = resp.text
    assert "Cadena de hashes" in body
    assert "Exportado: 1 registro" in body
    assert "globex" in body
    assert "initech-old" not in body

    # se audita el export
    audited = client.get("/audit?include_reads=true&action=audit.export").json()["items"]
    assert len(audited) == 1
    assert audited[0]["detail"]["filters"]["tenant_id"] == "globex"


async def test_export_json_respeta_los_mismos_filtros_que_recent(session, client):
    session.add_all(
        [
            _row(action="account.reveal", result="ok"),  # sensible: siempre visible
            _row(action="account.list", result="ok"),  # consulta ok: oculta por defecto
        ]
    )
    await session.commit()

    body = client.get("/audit/export?format=json").json()
    assert body["meta"]["exported_count"] == 1
    assert [i["action"] for i in body["items"]] == ["account.reveal"]
    assert "chain_intact" in body["meta"]
    assert "exported_id_range" in body["meta"]


async def test_export_tope_no_pagina(session, client):
    resp = client.get("/audit/export")
    assert resp.status_code == 200  # sin filas, no explota con id_range None
    assert "sin registros" in resp.text
