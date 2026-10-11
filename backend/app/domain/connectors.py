"""Filas de la página Setup > Connector management (funciones puras).

Arman la fila de la grilla a partir del conector crudo de Connector Management
(`GET /api/connectors`) y de los pools (`GET /api/connector-pools`). Ver
docs/specs/setup-connector-management.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Mismo criterio que `analyze_connectors` de ITR (app/itr/analysis.py).
STATUS_OK = frozenset({"Active", "Initializing", "Downloaded"})

_POOL_ID_KEYS = ("poolId", "connectorPoolId")
_POOL_MEMBER_KEYS = ("connectors", "connectorIds")


def _ms_to_iso(value: object) -> str | None:
    """Los timestamps de Connector Management vienen en milisegundos."""
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _metric(value: object) -> float | None:
    """El centinela -1 de usedCpu/usedMemory significa "sin datos"."""
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        return None
    return value


def pool_names(pools: list[dict]) -> dict[str, str]:
    """`poolId -> nombre` a partir de la lista cruda de pools."""
    out: dict[str, str] = {}
    for p in pools:
        pid = p.get("poolId") or p.get("id")
        if pid:
            out[str(pid)] = p.get("name") or str(pid)
    return out


def _pool_membership(pools: list[dict]) -> dict[str, str]:
    """`connectorId -> poolId` si el endpoint de pools trae sus miembros (el
    campo exacto no está confirmado: se prueba `connectors` y `connectorIds`,
    como ids sueltos o como objetos con `connectorId`/`id`)."""
    out: dict[str, str] = {}
    for p in pools:
        pid = p.get("poolId") or p.get("id")
        if not pid:
            continue
        for key in _POOL_MEMBER_KEYS:
            for m in p.get(key) or []:
                cid = m.get("connectorId") or m.get("id") if isinstance(m, dict) else m
                if cid:
                    out[str(cid)] = str(pid)
    return out


def _scalar(v: object) -> bool:
    return isinstance(v, str | int | float | bool) and v != ""


def _details(conn: dict) -> dict[str, str]:
    """Campos escalares del conector (host aplanado como `host.*`), para la
    pestaña "Detalles". Se muestran tal cual vienen de la API: así se ve
    cualquier campo que la fila no mapee (ej. el heartbeat)."""
    out: dict[str, str] = {}
    for k, v in conn.items():
        if k == "components":
            continue
        if k == "host" and isinstance(v, dict):
            out.update({f"host.{hk}": str(hv) for hk, hv in v.items() if _scalar(hv)})
        elif _scalar(v):
            out[k] = str(v)
    return out


def connector_row(conn: dict, names: dict[str, str], membership: dict[str, str]) -> dict:
    host = conn.get("host") or {}
    connector_id = conn.get("connectorId") or conn.get("id") or ""
    components = conn.get("components") or []
    active = sum(1 for c in components if c.get("componentStatus") in STATUS_OK)
    upgrades = sorted({v for c in components for v in (c.get("upgradeVersions") or [])})
    pool_id = next((conn[k] for k in _POOL_ID_KEYS if conn.get(k)), None) or membership.get(
        str(connector_id)
    )
    return {
        "connectorId": connector_id,
        "hostname": host.get("hostname") or connector_id,
        "ip": host.get("privateIp") or host.get("publicIp") or "",
        "os": host.get("os") or "",
        "platformType": conn.get("platformType") or "",
        "status": conn.get("connectorStatus") or "",
        "version": conn.get("version") or "",
        "componentsActive": active,
        "componentsTotal": len(components),
        "degraded": any(c.get("componentStatus") not in STATUS_OK for c in components),
        "upgradeAvailable": bool(upgrades),
        "upgradeVersions": upgrades,
        # El "Last heartbeat"/"Last updated" del portal es `updatedAt` del
        # conector (confirmado contra el lab, 2026-10-07).
        "lastHeartbeat": _ms_to_iso(conn.get("updatedAt")),
        "poolId": str(pool_id) if pool_id else None,
        "pool": names.get(str(pool_id)) if pool_id else None,
        "details": _details(conn),
        "components": [
            {
                "name": c.get("componentName") or c.get("name") or "",
                "acronym": c.get("acronym") or "",
                "installedAt": _ms_to_iso(c.get("installedAt")),
                "status": c.get("componentStatus") or "",
                "version": c.get("version") or "",
                "usedCpu": _metric(c.get("usedCpu")),
                "usedMemory": _metric(c.get("usedMemory")),
                "updatedAt": _ms_to_iso(c.get("updatedAt")),
                "upgradeVersions": c.get("upgradeVersions") or [],
            }
            for c in components
        ],
    }


def connector_rows(connectors: list[dict], pools: list[dict]) -> list[dict]:
    names = pool_names(pools)
    membership = _pool_membership(pools)
    return [connector_row(c, names, membership) for c in connectors]


def pool_rows(pools: list[dict], connectors: list[dict]) -> list[dict]:
    """Un pool con los conectores que lo referencian (por el conector o por
    la lista de miembros del propio pool)."""
    names = pool_names(pools)
    rows = connector_rows(connectors, pools)
    out = []
    for p in pools:
        pid = str(p.get("poolId") or p.get("id") or "")
        members = [r for r in rows if r["poolId"] == pid]
        out.append(
            {
                "poolId": pid,
                "name": names.get(pid) or pid,
                "description": p.get("description") or "",
                "connectorCount": len(members),
                "connectors": [
                    {"connectorId": m["connectorId"], "hostname": m["hostname"]} for m in members
                ],
            }
        )
    return out


def sia_row(conn: dict) -> dict:
    # Nombres reales de SIA Connector API (`id`/`hostName`/`hostIp`),
    # confirmados contra Globex; ver `analyze_sia_connectors` de ITR.
    return {
        "connectorId": conn.get("id") or conn.get("connectorId") or "",
        "hostname": conn.get("hostName") or conn.get("hostname") or "",
        "ip": conn.get("hostIp") or conn.get("ipAddress") or "",
        "os": conn.get("os") or conn.get("operatingSystem") or "",
        "status": conn.get("status") or conn.get("connectorStatus") or "",
        "version": conn.get("version") or "",
        "isLatestVersion": conn.get("isLatestVersion"),
        "region": conn.get("region") or "",
        "poolName": conn.get("connectorPoolName") or conn.get("poolName") or "",
    }
