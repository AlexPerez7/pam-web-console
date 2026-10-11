"""Tipos base de la sección de Reportes (docs/specs/reportes.md).

Cada reporte es una lectura masiva de un tenant que produce filas + columnas.
La lógica de cómputo vive en funciones puras (`compute_*`); `run()` sólo
orquesta las llamadas al cliente de CyberArk.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.cyberark import CyberArkClient

ColumnKind = Literal["text", "date", "bool", "number"]
# multiselect: varios valores de `options`, viajan en la query como una
# sola cadena separada por comas (`?motivos=A,B`) -- ver `multi_values()`.
# user: texto con autocompletado de usuarios (Identity + Vault) en el
# frontend -- viaja igual que un `text` (el nombre de usuario elegido).
ParamType = Literal["text", "number", "select", "multiselect", "user"]


@dataclass
class Column:
    key: str
    label: str
    kind: ColumnKind = "text"


@dataclass
class Param:
    key: str
    label: str
    type: ParamType = "text"
    required: bool = False
    options: list[str] | None = None
    help: str | None = None
    # etiqueta legible por valor de `options` (el valor es el que viaja en
    # la query; sin etiqueta, el frontend muestra el valor tal cual)
    option_labels: dict[str, str] | None = None


def multi_values(params: dict[str, Any], key: str, allowed: list[str]) -> set[str]:
    """Valores de un parámetro `multiselect` (`"A,B"` -> `{"A", "B"}`),
    descartando los que no estén en `allowed`. Vacío = ninguno elegido."""
    raw = str(params.get(key) or "")
    return {v.strip() for v in raw.split(",") if v.strip() in allowed}


@dataclass
class ReportResult:
    columns: list[Column]
    rows: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


Run = Callable[[CyberArkClient, dict[str, Any]], Awaitable[ReportResult]]


@dataclass
class Report:
    name: str  # slug: "safe-duplicates"
    label: str
    description: str
    run: Run
    # agrupa el catálogo en el frontend (Reportes): "Cuentas", "Safes",
    # "Identity y Licencias", "Infraestructura". Sin valor -> "Otros".
    category: str = ""
    params: list[Param] = field(default_factory=list)
    # heavy = necesita actividad por cuenta -> corre como job ARQ
    # (`app.jobs.reports.run_heavy_report`)
    heavy: bool = False
    # config del tenant (registro de PAM Web Console, no de CyberArk) que el reporte
    # necesita además del cliente -- ej. `Tenant.compliance_stale_days`.
    # Se suma a los params SÓLO al llamar a `run()` (no a la cache key ni
    # al audit), tanto en `app.api.reports._compute` como en el job.
    tenant_params: Callable[[Any], dict[str, Any]] | None = None
    # generador de Excel propio (resultado serializado + nombre del tenant ->
    # bytes del `.xlsx`); habilita `format=xlsx` en el export
    xlsx: Callable[[dict[str, Any], str], bytes] | None = None

    @property
    def formats(self) -> list[str]:
        return ["csv", "json", *(["xlsx"] if self.xlsx else [])]

    def run_params(self, tenant: Any, params: dict[str, Any]) -> dict[str, Any]:
        if self.tenant_params is None:
            return params
        return {**params, **self.tenant_params(tenant)}

    def catalog(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "category": self.category or "Otros",
            "heavy": self.heavy,
            "formats": self.formats,
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "type": p.type,
                    "required": p.required,
                    "options": p.options,
                    "help": p.help,
                    "option_labels": p.option_labels,
                }
                for p in self.params
            ],
        }


def result_to_dict(r: ReportResult) -> dict[str, Any]:
    return {
        "columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in r.columns],
        "rows": r.rows,
        "meta": r.meta,
    }
