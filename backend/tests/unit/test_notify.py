"""app.core.notify — canales de las reglas de alerta (Fase 5): webhook de
Teams (v1) y email/SMTP (alternativo, sin usar). Ver docs/specs/done/alert-rules.md."""

from __future__ import annotations

import json
from typing import ClassVar

import httpx
import pytest

import app.core.notify as notify_mod
from app.config import Settings


def _n(title: str, value: str, **kw) -> notify_mod.Notification:
    return notify_mod.Notification(title=title, facts=[("Dato", value)], **kw)


def _settings(**overrides) -> Settings:
    base = {
        "secret_key": "x" * 32,
        "alerts_teams_webhook_url": "",
        "alerts_smtp_host": "",
        "alerts_smtp_port": 587,
        "alerts_smtp_user": "",
        "alerts_smtp_password": "",
        "alerts_smtp_from": "",
        "alerts_smtp_use_tls": True,
        "alerts_email_to": "",
    }
    base.update(overrides)
    return Settings(**base)


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent = msg


async def test_send_sin_smtp_configurado_no_hace_nada(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(notify_mod, "get_settings", lambda: _settings())
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", FakeSMTP)

    await notify_mod.send(_n("asunto", "cuerpo"))

    assert FakeSMTP.instances == []  # nunca se conecta sin host/destinatarios


async def test_send_sin_destinatarios_no_hace_nada(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(alerts_smtp_host="smtp.x.com", alerts_email_to=""),
    )
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", FakeSMTP)

    await notify_mod.send(_n("asunto", "cuerpo"))

    assert FakeSMTP.instances == []


async def test_send_con_smtp_configurado_manda_el_mail(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(
            alerts_smtp_host="smtp.x.com",
            alerts_smtp_port=587,
            alerts_smtp_user="user",
            alerts_smtp_password="pw",
            alerts_smtp_from="pam@x.com",
            alerts_smtp_use_tls=True,
            alerts_email_to="a@x.com, b@x.com",
        ),
    )
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", FakeSMTP)

    await notify_mod.send(_n("[PAM Web Console] Alerta: X", "cuerpo del mail"))

    assert len(FakeSMTP.instances) == 1
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.x.com"
    assert smtp.started_tls is True
    assert smtp.logged_in == ("user", "pw")
    assert smtp.sent["Subject"] == "[PAM Web Console] Alerta: X"
    assert smtp.sent["From"] == "pam@x.com"
    assert smtp.sent["To"] == "a@x.com, b@x.com"
    assert "Dato: cuerpo del mail" in smtp.sent.get_content()


async def test_send_sin_tls_ni_login(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(
            alerts_smtp_host="smtp.x.com",
            alerts_smtp_use_tls=False,
            alerts_email_to="a@x.com",
        ),
    )
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", FakeSMTP)

    await notify_mod.send(_n("asunto", "cuerpo"))

    smtp = FakeSMTP.instances[0]
    assert smtp.started_tls is False
    assert smtp.logged_in is None


async def test_send_nunca_lanza_si_smtp_explota(monkeypatch):
    def boom(*a, **kw):
        raise OSError("conexión rechazada")

    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(alerts_smtp_host="smtp.x.com", alerts_email_to="a@x.com"),
    )
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", boom)

    await notify_mod.send(_n("asunto", "cuerpo"))  # no debe lanzar


class FakeTeamsTransport(httpx.AsyncBaseTransport):
    """Reemplaza el transporte real de httpx para no pegarle a una URL de
    verdad — captura el request y devuelve el status configurado."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, request=request)


@pytest.fixture
def fake_teams_transport(monkeypatch):
    transport = FakeTeamsTransport()
    real_async_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(notify_mod.httpx, "AsyncClient", _client)
    return transport


async def test_send_sin_webhook_teams_no_hace_nada(monkeypatch, fake_teams_transport):
    monkeypatch.setattr(notify_mod, "get_settings", lambda: _settings())

    await notify_mod.send(_n("asunto", "cuerpo"))

    assert fake_teams_transport.requests == []


async def test_send_con_webhook_teams_postea_adaptive_card(monkeypatch, fake_teams_transport):
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(alerts_teams_webhook_url="https://teams.example/webhook/abc"),
    )

    await notify_mod.send(_n("[PAM Web Console] Alerta: X", "cuerpo del mensaje"))

    assert len(fake_teams_transport.requests) == 1
    req = fake_teams_transport.requests[0]
    assert str(req.url) == "https://teams.example/webhook/abc"
    payload = json.loads(req.content)
    # la plantilla de Teams exige que el POST sea una Adaptive Card válida
    assert payload["type"] == "AdaptiveCard"
    header = payload["body"][0]
    assert header["type"] == "Container"
    assert header["items"][0]["text"] == "[PAM Web Console] Alerta: X"
    facts = next(b for b in payload["body"] if b["type"] == "FactSet")["facts"]
    assert {"title": "Dato", "value": "cuerpo del mensaje"} in facts


def test_tarjeta_color_por_severidad_link_y_pie():
    n = notify_mod.Notification(
        title="🔴 [PAM Web Console] Alerta: Vault desconectado",
        severity="critical",
        facts=[("Tenant", "Globex (globex)")],
        link="https://pam.lab.local/system-health",
        footer="Generado automáticamente por PAM Web Console.",
    )
    card = notify_mod._adaptive_card(n)
    assert card["body"][0]["style"] == "attention"
    assert card["actions"] == [
        {
            "type": "Action.OpenUrl",
            "title": "Abrir en PAM Web Console",
            "url": "https://pam.lab.local/system-health",
        }
    ]
    footer = card["body"][-1]
    assert footer["text"] == "Generado automáticamente por PAM Web Console."
    assert footer["isSubtle"] is True

    styles = {
        sev: notify_mod._adaptive_card(notify_mod.Notification(title="x", severity=sev))["body"][0][
            "style"
        ]
        for sev in ("critical", "warning", "info", "good")
    }
    assert styles == {
        "critical": "attention",
        "warning": "warning",
        "info": "accent",
        "good": "good",
    }
    # sin link no hay botón
    assert "actions" not in notify_mod._adaptive_card(notify_mod.Notification(title="x"))


def test_render_text_para_el_email():
    n = notify_mod.Notification(
        title="no va en el cuerpo",
        facts=[("Tenant", "Initech (initech)"), ("Estado", "nueva")],
        link="https://status.cyberark.com",
        link_label="Ver en status.cyberark.com",
        footer="Pie.",
    )
    assert notify_mod.render_text(n) == (
        "Tenant: Initech (initech)\nEstado: nueva\n"
        "Ver en status.cyberark.com: https://status.cyberark.com\n\n-- Pie."
    )


async def test_send_devuelve_true_sin_canales_configurados(monkeypatch):
    # nada que reintentar: no se deja el aviso pendiente para siempre
    monkeypatch.setattr(notify_mod, "get_settings", lambda: _settings())
    assert await notify_mod.send(_n("asunto", "cuerpo")) is True


async def test_send_devuelve_true_si_teams_entrega(monkeypatch, fake_teams_transport):
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(alerts_teams_webhook_url="https://teams.example/webhook/abc"),
    )
    assert await notify_mod.send(_n("asunto", "cuerpo")) is True


async def test_send_devuelve_false_si_todos_los_canales_configurados_fallan(monkeypatch):
    transport = FakeTeamsTransport(status_code=500)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        notify_mod.httpx,
        "AsyncClient",
        lambda *a, **kw: real_async_client(*a, transport=transport, **kw),
    )
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(alerts_teams_webhook_url="https://teams.example/webhook/abc"),
    )
    assert await notify_mod.send(_n("asunto", "cuerpo")) is False


async def test_send_devuelve_true_si_un_canal_entrega_aunque_otro_falle(
    monkeypatch, fake_teams_transport
):
    def boom(*a, **kw):
        raise OSError("conexión rechazada")

    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(
            alerts_teams_webhook_url="https://teams.example/webhook/abc",
            alerts_smtp_host="smtp.x.com",
            alerts_email_to="a@x.com",
        ),
    )
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", boom)
    assert await notify_mod.send(_n("asunto", "cuerpo")) is True


async def test_send_nunca_lanza_si_teams_devuelve_error(monkeypatch):
    transport = FakeTeamsTransport(status_code=500)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        notify_mod.httpx,
        "AsyncClient",
        lambda *a, **kw: real_async_client(*a, transport=transport, **kw),
    )
    monkeypatch.setattr(
        notify_mod,
        "get_settings",
        lambda: _settings(alerts_teams_webhook_url="https://teams.example/webhook/abc"),
    )

    await notify_mod.send(_n("asunto", "cuerpo"))  # no debe lanzar

    assert len(transport.requests) == 1


async def test_fallo_de_teams_no_loguea_la_url_del_webhook(
    monkeypatch, fake_teams_transport, caplog
):
    """Con el flujo apagado Teams responde 400 y httpx mete la URL completa
    (con su `sig=`, la credencial del flujo) en el texto del error: no debe
    llegar al log."""
    url = "https://teams.example/webhook/abc?sig=SECRETO"
    monkeypatch.setattr(notify_mod, "get_settings", lambda: _settings(alerts_teams_webhook_url=url))
    fake_teams_transport.status_code = 400

    assert await notify_mod.send(_n("[PAM Web Console] Alerta: X", "cuerpo")) is False
    assert "HTTP 400" in caplog.text
    assert "SECRETO" not in caplog.text
