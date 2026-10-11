"""Helpers para los reportes que consultan la API Redrock de Identity."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

_MS_DATE = re.compile(r"/Date\((-?\d+)")

# Status de Identity que se consideran "no activo"
INACTIVE_STATUS = {"disabled", "suspended", "locked", "deactivated"}

# Códigos de SourceDs que CyberArk a veces no localiza bien
# (SourceDsLocalized/SourceDsType vuelven "Unknown" aunque el dato crudo esté)
# -- mismo mapeo que KNOWN_SOURCE_DS_CODES de app.itr.analysis (vendorizado,
# no se importa de ahí a propósito) e License_Candidates_Report.ps1.
# Sólo AAD (Microsoft Entra ID) lo necesitaba contra un tenant real; LDAP y
# Google Directory quedan sin confirmar.
KNOWN_SOURCE_DS_CODES = {
    "AAD": "Microsoft Entra ID",
    "FDS": "Federated",
}


def source_label(iu: dict) -> str | None:
    """Fuente/directorio de un usuario de Identity (`SELECT ...
    SourceDs, SourceDsLocalized, SourceDsType FROM Users`) -- prioriza
    `SourceDsLocalized`, cae a `KNOWN_SOURCE_DS_CODES[SourceDs]`, después al
    código crudo de `SourceDs`, y por último a `SourceDsType`."""
    localized = iu.get("SourceDsLocalized")
    raw_code = iu.get("SourceDs") or ""
    if localized and localized != "Unknown":
        return localized
    if raw_code in KNOWN_SOURCE_DS_CODES:
        return KNOWN_SOURCE_DS_CODES[raw_code]
    if raw_code:
        return raw_code
    return iu.get("SourceDsType") or None


def parse_redrock_epoch(value: Any) -> int | None:
    """`/Date(1700000000000+0000)/` o ISO -> epoch en segundos. None si vacío."""
    if not value:
        return None
    s = str(value)
    m = _MS_DATE.search(s)
    if m:
        return int(m.group(1)) // 1000
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def days_since(epoch: int | None) -> int | None:
    if epoch is None:
        return None
    return (datetime.now(UTC) - datetime.fromtimestamp(epoch, tz=UTC)).days
