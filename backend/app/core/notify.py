"""Canales de notificación de las reglas de alerta (Fase 5,
docs/specs/done/alert-rules.md). `send()` despacha en paralelo a todos los
canales configurados — hoy Teams (webhook de un flujo de "Flujos de
trabajo"/Power Automate, plantilla "Enviar alertas de webhook a un chat",
apunta al chat "Alertas PAM Web Console") y email (SMTP, sin usar por decisión de
Alex: no se quiere meter el M365 corporativo del MSP). Pensado para
poder sumar otro canal sin tocar `app/jobs/alerts.py` (que sólo llama a
`send()`).

Canal sin configurar (`PAM_ALERTS_TEAMS_WEBHOOK_URL` / `PAM_ALERTS_SMTP_HOST`
vacíos, el default) -> ese canal no hace nada, silencioso. Mismo criterio
de degradación que `app.core.cache` ante Redis caído: el motor de reglas
sigue corriendo y auditando, sólo no llega la notificación."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

import httpx

from app.config import get_settings

log = logging.getLogger("pam.notify")


def _send_email_sync(subject: str, body: str) -> None:
    s = get_settings()
    to = [addr.strip() for addr in s.alerts_email_to.split(",") if addr.strip()]
    if not s.alerts_smtp_host or not to:
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.alerts_smtp_from or s.alerts_smtp_user or "pam@localhost"
    msg["To"] = ", ".join(to)
    msg.set_content(body)

    with smtplib.SMTP(s.alerts_smtp_host, s.alerts_smtp_port, timeout=10) as smtp:
        if s.alerts_smtp_use_tls:
            smtp.starttls()
        if s.alerts_smtp_user:
            smtp.login(s.alerts_smtp_user, s.alerts_smtp_password)
        smtp.send_message(msg)


async def _send_email(subject: str, body: str) -> bool | None:
    """True = entregado, False = falló, None = canal sin configurar."""
    s = get_settings()
    if not s.alerts_smtp_host or not s.alerts_email_to.strip():
        return None
    try:
        await asyncio.to_thread(_send_email_sync, subject, body)
    except Exception as e:  # smtplib puede lanzar variedad de excepciones distintas
        log.warning("alertas: fallo enviando email (%s): %s", subject, e)
        return False
    return True


# Estilo del `Container` de la tarjeta según la severidad (req. 6).
_CARD_STYLE = {
    "critical": "attention",
    "warning": "warning",
    "info": "accent",
    "good": "good",
}


@dataclass(frozen=True)
class Notification:
    """Un aviso, independiente del canal: Teams lo arma como Adaptive Card
    (`_adaptive_card`) y el email como texto plano (`render_text`), desde la
    misma estructura (docs/specs/alert-delivery.md, puntos 3 y 7)."""

    title: str
    # critical / warning / info / good (resuelta, heartbeat OK)
    severity: str = "warning"
    facts: list[tuple[str, str]] = field(default_factory=list)
    link: str = ""
    link_label: str = "Abrir en PAM Web Console"
    footer: str = ""


def render_text(n: Notification) -> str:
    """Cuerpo en texto plano (email y tests): un dato por línea, el link y
    el pie. El título va aparte (asunto del mail)."""
    lines = [f"{k}: {v}" for k, v in n.facts]
    if n.link:
        lines.append(f"{n.link_label}: {n.link}")
    if n.footer:
        lines += ["", f"-- {n.footer}"]
    return "\n".join(lines)


def _adaptive_card(n: Notification) -> dict:
    """La plantilla de Teams "Enviar alertas de webhook a un chat" exige
    que el POST sea una Adaptive Card válida (no un JSON plano cualquiera)
    — lo dice la propia pantalla de configuración del flujo.

    Título dentro de un `Container` coloreado por severidad, los datos en un
    `FactSet`, botón al lugar donde se atiende y el pie en texto chico."""
    header: list[dict] = [
        {
            "type": "TextBlock",
            "text": n.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    body: list[dict] = [
        {
            "type": "Container",
            "style": _CARD_STYLE.get(n.severity, "default"),
            "bleed": True,
            "items": header,
        }
    ]
    if n.facts:
        body.append(
            {
                "type": "FactSet",
                "facts": [{"title": k, "value": v} for k, v in n.facts],
            }
        )
    if n.footer:
        body.append(
            {
                "type": "TextBlock",
                "text": n.footer,
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
            }
        )
    card: dict = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    if n.link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": n.link_label, "url": n.link}]
    return card


def _redact_webhook(e: Exception) -> str:
    """Mensaje de error sin la URL del webhook: su `sig=` es la credencial
    del flujo (quien la tiene puede publicar en el chat) y httpx la incluye
    en el texto de un `HTTPStatusError`."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} del webhook de Teams"
    url = get_settings().alerts_teams_webhook_url
    msg = f"{type(e).__name__}: {e}"
    return msg.replace(url, "<webhook de Teams>") if url else msg


async def _send_teams(n: Notification) -> bool | None:
    """POST al webhook de un flujo de Teams ("Flujos de trabajo"/Power
    Automate, plantilla "Enviar alertas de webhook a un chat") — el
    cuerpo del request ES la Adaptive Card, Power Automate la postea tal
    cual en el chat configurado del lado de Teams.

    True = entregado, False = falló, None = canal sin configurar."""
    s = get_settings()
    if not s.alerts_teams_webhook_url:
        return None

    payload = _adaptive_card(n)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(s.alerts_teams_webhook_url, json=payload)
            resp.raise_for_status()
    except Exception as e:  # host caído, URL revocada, 4xx/5xx del flujo, etc.
        log.warning("alertas: fallo enviando a Teams (%s): %s", n.title, _redact_webhook(e))
        return False
    return True


async def send(n: Notification) -> bool:
    """Nunca lanza — cada canal degrada solo (sin configurar, o si falla
    el envío) sin afectar a los demás ni al job de alertas.

    Devuelve si el aviso quedó entregado: True si algún canal configurado
    lo entregó, o si no hay ningún canal configurado (no hay nada que
    reintentar); False si hay canales configurados y todos fallaron. Con
    False, el motor deja el aviso pendiente y lo reintenta en la próxima
    corrida (docs/specs/alert-delivery.md)."""
    results = await asyncio.gather(_send_email(n.title, render_text(n)), _send_teams(n))
    configured = [r for r in results if r is not None]
    return not configured or any(configured)
