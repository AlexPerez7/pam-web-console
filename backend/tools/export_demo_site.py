"""
Genera los datos de la demo estática (GitHub Pages) a partir de la API real.

Levanta la app FastAPI en proceso en modo demo (`PAM_DEMO_MODE=true`, SQLite
in-memory, caché en memoria, sin Redis ni Postgres), ejecuta algunas
escrituras de ejemplo para que el audit log tenga cambios además de
lecturas, recorre los endpoints GET que usa el frontend y guarda cada
respuesta en un JSON por tenant (+ uno global). El frontend compilado con
`VITE_DEMO=true` responde `/api/...` desde esos archivos
(`frontend/src/demo/mockApi.ts`).

Uso (desde backend/):

    python tools/export_demo_site.py ../frontend/public/demo-data
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

os.environ.update(
    {
        "PAM_DEMO_MODE": "true",
        "PAM_ENVIRONMENT": "dev",
        "PAM_SECRET_KEY": "demo-site-export-key-0123456789abcdef0123",
        "PAM_DUAL_APPROVAL_ENABLED": "false",
        "PAM_READ_RATE_MAX_PER_WINDOW": "10000000",
        "PAM_READ_RATE_GLOBAL_MAX_PER_WINDOW": "10000000",
        "PAM_WRITE_MAX_PER_WINDOW": "10000000",
        "PAM_LOG_LEVEL": "ERROR",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core import cache
from app.core.deps import current_operator
from app.core.seed import seed_tenants
from app.db import get_session
from app.main import create_app
from app.models import Base, Operator, OperatorRole

TENANTS = ["globex", "acme", "initech", "umbrella", "lab"]
COMPLIANCE_REASONS = ["PolicyFailures", "DisableByUser", "DisableByCPM", "Locked", "DisabledStale"]


# --- caché en memoria (la app real usa Redis) -------------------------------
_mem: dict[str, Any] = {}


async def _get_json(key: str) -> dict | None:
    return _mem.get(key)


async def _set_json(key: str, value: dict, ttl: int) -> None:
    _mem[key] = value


async def _get_int(key: str) -> int | None:
    return _mem.get(key)


async def _incr(key: str, ttl: int) -> int:
    _mem[key] = int(_mem.get(key, 0)) + 1
    return _mem[key]


async def _delete(key: str) -> None:
    _mem.pop(key, None)


cache.get_json = _get_json  # type: ignore[assignment]
cache.set_json = _set_json  # type: ignore[assignment]
cache.get_int = _get_int  # type: ignore[assignment]
cache.incr_with_ttl = _incr  # type: ignore[assignment]
cache.delete = _delete  # type: ignore[assignment]


def rows(body: Any, field: str) -> list:
    """Lista de una respuesta que puede ser un array suelto o `{field: [...]}`."""
    if isinstance(body, list):
        return body
    return (body or {}).get(field, []) if isinstance(body, dict) else []


def key(path: str, params: dict | None = None) -> str:
    """Misma normalización que `mockApi.ts`: path + query ordenada."""
    if not params:
        return path
    return f"{path}?{urlencode(sorted(params.items()))}"


class Exporter:
    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.files: dict[str, dict[str, Any]] = {t: {} for t in [*TENANTS, "global"]}
        self.errors = 0

    def get(
        self,
        path: str,
        params: dict | None = None,
        *,
        bucket: str = "global",
        store_as: str | None = None,
    ) -> Any:
        t0 = time.time()
        r = self.c.get(path, params=params)
        if time.time() - t0 > 2:
            print(f"  (lento: {time.time() - t0:.0f}s) {path}", flush=True)
        if r.status_code >= 400:
            self.errors += 1
            return None
        body = r.json()
        self.files[bucket][store_as or key(path, params)] = body
        return body

    def write(self, method: str, path: str, body: dict) -> None:
        r = self.c.request(method, path, json=body)
        if r.status_code >= 400:
            print(f"  (escritura de ejemplo falló) {method} {path}: {r.text[:120]}")


def build_client() -> TestClient:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def setup() -> Operator:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as s:
            await seed_tenants(s)
            op = Operator(
                username="demo",
                password_hash="-",  # noqa: S106 — operador ficticio, nunca hace login
                role=OperatorRole.platform_admin,
                is_active=True,
                totp_enrolled=True,
            )
            s.add(op)
            await s.commit()
            await s.refresh(op)
            return op

    op = asyncio.run(setup())
    app = create_app()

    async def session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_operator] = lambda: op
    return TestClient(app, raise_server_exceptions=False)


def sample_writes(ex: Exporter) -> None:
    """Escrituras reales contra el estado demo: dejan cambios en el audit log."""
    reason = "Demo: corrección pedida en el ticket OPS-1042"
    for t in ("globex", "acme"):
        accs = ex.c.get(f"/tenants/{t}/accounts", params={"limit": 3}).json()["accounts"]
        a = accs[0]
        for dry in (True, False):
            ex.write(
                "POST",
                f"/tenants/{t}/accounts/{a['id']}/update",
                {"changes": {"address": f"{a['address']}"}, "dry_run": dry, "reason": reason},
            )
        ids = [x["id"] for x in accs[1:3]]
        for dry in (True, False):
            ex.write(
                "POST",
                f"/tenants/{t}/accounts/bulk-cpm",
                {"action": "verify", "account_ids": ids, "dry_run": dry, "reason": reason},
            )


def export_tenant(ex: Exporter, t: str) -> None:
    p = f"/tenants/{t}"
    g = lambda path, params=None, **kw: ex.get(f"{p}{path}", params, bucket=t, **kw)  # noqa: E731

    for path in (
        "",
        "/compliance-settings",
        "/server",
        "/accounts/summary",
        "/safe-names",
        "/cpm-names",
        "/sessions",
        "/components",
        "/platforms",
        "/psm-servers",
        "/safes/defaults",
        "/users",
        "/groups",
        "/identity-users",
        "/identity-roles",
        "/alero-vendors",
        "/alero-users",
        "/alero-roles",
        "/alero-activities",
        "/setup/connectors",
        "/setup/connector-pools",
        "/setup/sia-connectors",
        "/sia/policies",
        "/sia/sessions",
        "/sia/strong-accounts",
        "/sia/settings",
        "/recordings",
        "/dashboard",
        "/system-health",
        "/account-backups",
        "/platform-backups",
        "/troubleshooting/account-errors",
        "/campaigns",
        "/compliance/exemptions",
        "/itr/infra-files",
        "/audit",
    ):
        g(path)

    # listas completas, guardadas sin query: mockApi pagina/filtra en el navegador
    accounts = g("/accounts", {"limit": 1000}, store_as=f"{p}/accounts")["accounts"]
    safes = g("/safes", {"limit": 1000}, store_as=f"{p}/safes")["safes"]
    g("/discovered-accounts", {"limit": 1000}, store_as=f"{p}/discovered-accounts")

    for reason in COMPLIANCE_REASONS:
        g("/compliance/accounts", {"reason": reason})

    for a in accounts:
        for sub in ("", "/activities", "/password-versions"):
            g(f"/accounts/{a['id']}{sub}")
        g(f"/compliance/accounts/{a['id']}/status")
    for s in safes:
        g(f"/safes/{s['safeUrlId']}/members")
    for pl in rows(g("/platforms"), "platforms"):
        pid = pl["general"]["id"]
        g(f"/platforms/{pid}/psm")
        g(f"/platforms/{pid}/config")
    for u in rows(g("/users"), "users"):
        g(f"/users/{u['id']}")
    for gr in rows(g("/groups"), "groups"):
        g(f"/groups/{gr['id']}/members")
    for u in rows(g("/identity-users"), "users"):
        g(f"/identity-users/{u['id']}")
    for r in rows(g("/identity-roles"), "roles"):
        g(f"/identity-roles/{r['id']}/members")
    for v in rows(g("/alero-vendors"), "vendors"):
        g(f"/alero-vendors/{v['id']}")
    for rec in rows(g("/recordings"), "recordings"):
        g(f"/recordings/{rec['SessionID']}/activities")
    for rep in rows(ex.files["global"].get("/reports"), "reports"):
        g(f"/reports/{rep['name']}")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-data")
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    ex = Exporter(build_client())

    for path in (
        "/auth/me",
        "/health",
        "/version",
        "/operators",
        "/tenants",
        "/system-health",
        "/approvals",
        "/approvals/count",
        "/knowledge-base",
        "/reports",
    ):
        ex.get(path)
    for art in rows(ex.files["global"].get("/knowledge-base"), "articles"):
        ex.get(f"/knowledge-base/{art['id']}")

    for t in TENANTS:
        export_tenant(ex, t)
        print(f"  {t}: {len(ex.files[t])} respuestas")

    # escrituras de ejemplo al final: son lo más reciente del audit log
    sample_writes(ex)

    # auditoría al final, para que incluya todo lo anterior
    items: list = []
    audit_params: dict[str, Any] = {
        "limit": 200,
        "include_reads": "true",
        "include_dry_run": "true",
    }
    while len(items) < 2000:
        page = ex.c.get("/audit", params=audit_params).json()
        items.extend(page["items"])
        if not page.get("next_before_id"):
            break
        audit_params["before_id"] = page["next_before_id"]
    ex.files["global"]["/audit"] = {"items": items, "next_before_id": None}

    for path, params in (
        ("/audit/summary", None),
        ("/audit/operators", None),
        ("/audit/verify", None),
    ):
        ex.get(path, params, store_as=path)

    for name, data in ex.files.items():
        (out / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    size = sum(f.stat().st_size for f in out.glob("*.json")) / 1e6
    print(
        f"listo en {time.time() - started:.0f}s: {size:.1f} MB en {out} ({ex.errors} GET con error)"
    )


if __name__ == "__main__":
    main()
