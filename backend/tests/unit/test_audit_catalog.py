"""app.core.audit_catalog — clasificación de `action` para la página
Auditoría (docs/specs/done/audit-page.md, Tandas 1 y 2)."""

from __future__ import annotations

import re
from pathlib import Path

from app.core import audit_catalog
from app.core.audit_catalog import CATEGORIES, KNOWN_ACTIONS, category, classify, label

_ACTION_LITERAL = re.compile(r'"([a-z][a-z0-9_]*(?:\.[a-z0-9_]+){1,3})"')

# Strings con la misma forma "palabra.palabra" que NO son una `action` del
# audit log (FK de SQLAlchemy, nombres de logger, headers de CSV, un
# nombre de archivo, un ejemplo en un docstring) — confirmadas a mano al
# armar este test; si aparece una nueva hay que revisar si es ruido o una
# action real antes de sumarla acá.
_KNOWN_NOISE = frozenset(
    {
        "account.read",  # ejemplo en el docstring de app/models/audit.py
        "archivo.zip",
        "campaigns.id",
        "general.active",
        "general.id",
        "general.name",
        "pam.access",
        "pam.cache",
        "pam.cyberark",
        "pam.notify",  # logger de app/core/notify.py
        "pam.vendor_status",  # logger de app/vendor_status.py
        "operators.id",
        "report.html.j2",
        "tenants.id",
        "tenants.json",
        "uvicorn.access",
        "uvicorn.error",
    }
)


def _app_dir() -> Path:
    return Path(audit_catalog.__file__).resolve().parents[1]


def _actions_in_source() -> set[str]:
    found: set[str] = set()
    for path in _app_dir().rglob("*.py"):
        if "__pycache__" in path.parts or "demo" in path.parts or path.name == "audit_catalog.py":
            # audit_catalog define las reglas; app/demo sólo tiene datos ficticios
            continue
        text = path.read_text(encoding="utf-8")
        found.update(_ACTION_LITERAL.findall(text))
    return found - _KNOWN_NOISE


def test_ninguna_accion_del_codigo_queda_sin_clasificar():
    """Si aparece una `action=` nueva en el código y no está en
    `KNOWN_ACTIONS`, este test falla — obliga a clasificarla a mano
    (agregarla al registro) en vez de que caiga en silencio en el default
    genérico de `classify`/`category`."""
    found = _actions_in_source()
    missing = found - KNOWN_ACTIONS
    assert not missing, f"acciones nuevas sin registrar en KNOWN_ACTIONS: {sorted(missing)}"


def test_toda_kind_y_categoria_es_valida():
    for action in KNOWN_ACTIONS:
        assert classify(action) in {"consulta", "cambio", "sensible", "acceso"}
        assert category(action) in CATEGORIES


def test_accion_desconocida_nunca_se_oculta():
    assert classify("foo.bar") == "cambio"
    assert category("foo.bar") == "Administración"


def test_lectura_simple_es_consulta():
    assert classify("account.list") == "consulta"
    assert classify("safe.search") == "consulta"
    assert classify("dashboard.view") == "consulta"
    assert classify("report.non-rotating-accounts") == "consulta"


def test_secretos_son_sensibles():
    assert classify("account.reveal") == "sensible"
    assert classify("account.reveal.bulk") == "sensible"
    assert classify("recording.play") == "sensible"
    assert classify("platform.backup.download") == "sensible"


def test_acceso_login_logout_y_operadores():
    assert classify("auth.login") == "acceso"
    assert classify("auth.login.lockout_degraded") == "acceso"
    assert classify("auth.logout") == "acceso"
    assert classify("auth.refresh.reuse") == "acceso"
    assert classify("operator.create") == "acceso"
    # el refresh normal (silencioso, sin anomalía) es ruido, no un evento
    # de acceso a destacar
    assert classify("auth.refresh") == "consulta"


def test_escritura_sin_regla_especial_es_cambio():
    assert classify("account.update") == "cambio"
    assert classify("safe.delete") == "cambio"
    assert classify("identity_role.member.add") == "cambio"


def test_categorias_representativas():
    assert category("account.list") == "Cuentas"
    assert category("account.reveal") == "Contraseñas y secretos"
    assert category("safe.member.add") == "Safes"
    assert category("platform.delete") == "Plataformas"
    assert category("recording.play") == "Sesiones y grabaciones"
    assert category("identity_user.list") == "Identity"
    assert category("alero_vendor.list") == "Alero"
    assert category("report.itr.generate") == "Reportes"
    assert category("auth.login") == "Acceso a PAM Web Console"
    assert category("operator.create") == "Administración"


def test_ninguna_accion_estatica_queda_sin_traducir():
    """Las `report.{name}` dinámicas caen en el genérico "Corrió el reporte
    '...'" a propósito (R6) — todo lo demás en `KNOWN_ACTIONS` necesita una
    traducción explícita en `_LABELS`, para que una acción nueva no quede
    mostrando el código crudo sin que se note."""
    sin_traducir = {a for a in KNOWN_ACTIONS if not a.startswith("report.") and label(a) == a}
    assert not sin_traducir, f"acciones sin traducción en _LABELS: {sorted(sin_traducir)}"


def test_label_de_reporte_dinamico_y_accion_conocida():
    assert label("account.reveal") == "Reveló la contraseña"
    assert label("report.non-rotating-accounts") == "Corrió el reporte 'non-rotating-accounts'"
    assert label("foo.bar") == "foo.bar"  # sin traducción: el código, nunca "undefined"
