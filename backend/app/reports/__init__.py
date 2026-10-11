"""Registro de reportes disponibles. Ver docs/specs/done/reportes.md."""

from __future__ import annotations

from app.reports.account_activity import report as _account_activity
from app.reports.account_inventory import report as _account_inventory
from app.reports.account_inventory import report_full as _account_inventory_full
from app.reports.base import Report
from app.reports.license_candidates import report as _license_candidates
from app.reports.non_rotating_accounts import report as _non_rotating_accounts
from app.reports.psm_balance import report as _psm_balance
from app.reports.rotation_failures_by_system import report as _rotation_failures_by_system
from app.reports.safe_duplicates import report as _safe_duplicates
from app.reports.safe_permissions import report as _safe_permissions
from app.reports.target_inactivity import report as _target_inactivity
from app.reports.user_access import report as _user_access

# identity-user-activity se dio de baja (2026-09-22): se fusionó dentro de
# license-candidates, que ya cruzaba Vault+Identity y ahora no filtra por
# tipo de usuario -- ver el docstring de license_candidates.py.
REGISTRY: dict[str, Report] = {
    r.name: r
    for r in (
        _safe_duplicates,
        _safe_permissions,
        _user_access,
        _account_inventory,
        _account_inventory_full,
        _non_rotating_accounts,
        _rotation_failures_by_system,
        _psm_balance,
        _license_candidates,
        _target_inactivity,
        _account_activity,
    )
}

__all__ = ["REGISTRY", "Report"]
