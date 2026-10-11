import contextlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.api.alero import router as alero_router
from app.api.approvals import router as approvals_router
from app.api.audit import router as audit_router
from app.api.audit import tenant_router as tenant_audit_router
from app.api.auth import router as auth_router
from app.api.backups import platform_router as platform_backups_router
from app.api.backups import router as backups_router
from app.api.campaigns import router as campaigns_router
from app.api.compliance import router as compliance_router
from app.api.credentials_backup import router as credentials_backup_router
from app.api.dashboards import router as dashboards_router
from app.api.discovery import router as discovery_router
from app.api.exports import router as exports_router
from app.api.health import router as health_router
from app.api.identity import router as identity_router
from app.api.inventory import router as inventory_router
from app.api.itr import router as itr_router
from app.api.knowledge_base import router as knowledge_base_router
from app.api.operators import router as operators_router
from app.api.recordings import router as recordings_router
from app.api.reports import catalog_router as reports_catalog_router
from app.api.reports import router as reports_router
from app.api.search import router as search_router
from app.api.setup import router as setup_router
from app.api.sia import router as sia_router
from app.api.system_health import router as system_health_router
from app.api.tenants import router as tenants_router
from app.api.troubleshooting import router as troubleshooting_router
from app.api.vendor_status import router as vendor_status_router
from app.api.writes import router as writes_router
from app.config import get_settings
from app.core import cache, read_rate_limit, security
from app.core.context import request_id_var
from app.core.logging import configure_logging

_access_log = logging.getLogger("pam.access")

# X-Request-ID viene del cliente y termina en el audit_log y en los logs:
# sólo se acepta si es un id corto y sin caracteres raros (evita inyectar
# texto arbitrario o gigante); si no, se genera uno propio.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # otros hooks de startup/shutdown (pool de redis, secret store, etc.) van acá
    yield


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Request-id por request: se toma del header `X-Request-ID` o se genera.
    Queda en el ContextVar (lo lee el logging y `audit.record`), se devuelve en
    la respuesta, y se emite una linea de acceso. Best-effort: el logging nunca
    tumba un request."""
    incoming = request.headers.get("x-request-id") or ""
    rid = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        with contextlib.suppress(Exception):  # el log jamas rompe la respuesta
            _access_log.info(
                "%s %s -> %s (%sms)",
                request.method,
                request.url.path,
                status,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": duration_ms,
                },
            )
        request_id_var.reset(token)


async def read_rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Rate-limit general de lecturas (GET) por operador, para proteger al
    service account COMPARTIDO de cada tenant de un cliente descontrolado.
    No reemplaza el chequeo de auth real (`current_operator`) -- si el token
    falla acá, simplemente no se aplica el límite y el request sigue su
    curso normal (401 lo levanta el endpoint). Ver
    docs/specs/done/api-read-rate-limit.md."""
    auth_header = request.headers.get("authorization", "")
    claims: tuple[int, int] | None = None
    if auth_header.lower().startswith("bearer "):
        with contextlib.suppress(ValueError):
            claims = security.read_token(auth_header[7:])
            # reusado por current_operator -- evita decodificar el JWT dos
            # veces (ver docs/specs/done/api-read-rate-limit.md).
            request.state.auth_token_claims = claims

    if claims is not None and not read_rate_limit.is_exempt(request.method, request.url.path):
        operator_id, _ = claims
        key, limit = read_rate_limit.rate_limit_key(operator_id, request.url.path)
        window = get_settings().read_rate_window_seconds
        n = await cache.incr_with_ttl(key, window)
        if n is not None and n > limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "demasiadas lecturas; espere unos segundos"},
                headers={"Retry-After": str(window)},
            )

    return await call_next(request)


def create_app() -> FastAPI:
    # /docs, /redoc y /openapi.json exponen sin auth el mapa completo de la
    # API (paths, params, shapes de request/response de operaciones como
    # account.delete o account.reveal) -- aceptable en dev, pero ahora que
    # PAM Web Console también se sirve directo en la LAN (ver docs/security.md), fuera
    # de dev queda deshabilitado por completo, no sólo detrás del proxy SSH.
    docs_enabled = get_settings().environment == "dev"
    app = FastAPI(
        title="PAM Web Console — PAM Web Console",
        version="0.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.middleware("http")(request_context)
    app.middleware("http")(read_rate_limit_middleware)
    app.include_router(health_router, tags=["meta"])
    app.include_router(auth_router)
    app.include_router(operators_router)
    app.include_router(tenants_router)
    app.include_router(inventory_router)
    app.include_router(discovery_router)
    app.include_router(identity_router)
    app.include_router(alero_router)
    app.include_router(setup_router)
    app.include_router(sia_router)
    app.include_router(recordings_router)
    app.include_router(dashboards_router)
    app.include_router(exports_router)
    app.include_router(search_router)
    app.include_router(system_health_router)
    app.include_router(vendor_status_router)
    app.include_router(writes_router)
    app.include_router(approvals_router)
    app.include_router(backups_router)
    app.include_router(platform_backups_router)
    app.include_router(troubleshooting_router)
    app.include_router(campaigns_router)
    app.include_router(compliance_router)
    app.include_router(knowledge_base_router)
    app.include_router(credentials_backup_router)
    app.include_router(itr_router)
    app.include_router(reports_catalog_router)
    app.include_router(reports_router)
    app.include_router(audit_router)
    app.include_router(tenant_audit_router)
    return app


app = create_app()
