"""Override de layout de ITR por cliente: `app/itr/clientes/<tenant_id>/templates/`
pisa sólo los .j2 que necesita; el resto cae en la plantilla base. Alcance
chico a propósito: el reporte de un tenant tiene que salir igual de completo
que el de cualquier otro (para ocultar una pestaña existe `excluded_tabs`)."""

from __future__ import annotations

from app.itr import report_builder


def test_tenant_sin_override_no_tiene_carpeta():
    assert report_builder.client_templates_dir_for("globex") is None
    assert report_builder.client_templates_dir_for("initech") is None
    assert report_builder.client_templates_dir_for("tenant-que-no-existe") is None


def test_acme_solo_pisa_el_header():
    env = report_builder._make_jinja_env(report_builder.client_templates_dir_for("acme"))

    header_tpl = env.get_template("partials/_header.html.j2")
    assert "clientes" in header_tpl.filename.replace("\\", "/")
    assert "acme" in header_tpl.filename.replace("\\", "/")

    # sin override propio -> cae en la base, como cualquier tenant sin layout
    report_tpl = env.get_template("report.html.j2")
    assert "clientes" not in report_tpl.filename.replace("\\", "/")


def test_sin_override_todo_cae_en_la_base():
    env = report_builder._make_jinja_env(report_builder.client_templates_dir_for("globex"))
    report_tpl = env.get_template("report.html.j2")
    assert "clientes" not in report_tpl.filename.replace("\\", "/")
