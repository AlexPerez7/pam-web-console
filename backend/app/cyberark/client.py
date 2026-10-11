"""
Cliente async de la API Gen2 de PVWA. Misma API para Privilege Cloud y PAM
Self-Hosted; cambia la estrategia de auth. Evolución de `cyberark_api.py` de
Telemetry-Reporter: retry con backoff (429/5xx), re-auth ante 401,
límite de concurrencia por tenant, paginación transparente.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.cyberark.auth import ISPSSAuth, SelfHostedAuth
from app.cyberark.errors import (
    Conflict,
    CyberArkError,
    Forbidden,
    NotFound,
    RateLimited,
    UpstreamError,
)

log = logging.getLogger("pam.cyberark")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def accounts_filter(safe_name: str | None, modified_after: int | None) -> str | None:
    """Arma el parámetro `filter` de Get Accounts (Gen2). Lo único que la API
    soporta filtrar server-side: `safeName eq` y `modificationTime gt`."""
    parts = []
    if safe_name:
        parts.append(f"safeName eq {safe_name}")
    if modified_after:
        parts.append(f"modificationTime gt {int(modified_after)}")
    return " AND ".join(parts) or None


_MAX_PAGES = 100  # tope de páginas en los listados de SIA (evita un loop infinito)


def isp_service_url(sia_connector_url: str, service: str) -> str:
    """Host de un servicio de la plataforma ISPSS (`dpa`, `uap`,
    `sessionmonitoring`) a partir de la URL de SIA del tenant
    (`https://{sub}.dpa.cyberark.cloud`). Igual que el SDK oficial:
    `https://{sub}.{servicio}.cyberark.cloud`. Si el host no tiene la forma
    esperada, se toma el primer label como subdominio."""
    host = urlparse(sia_connector_url).hostname or sia_connector_url
    labels = host.split(".")
    if len(labels) >= 3:
        labels[1] = service
        return "https://" + ".".join(labels)
    return f"https://{labels[0]}.{service}.cyberark.cloud"


def _items(data: Any, *keys: str) -> list:
    """Lista de una respuesta que puede venir como array suelto o envuelta
    en alguna de `keys`."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


class CyberArkClient:
    def __init__(
        self,
        pvwa_url: str,
        auth: ISPSSAuth | SelfHostedAuth,
        *,
        verify_ssl: bool = True,
        max_concurrency: int = 8,
        timeout: float = 60.0,
        connector_management_url: str | None = None,
        sia_connector_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base = pvwa_url.rstrip("/") + "/PasswordVault"
        # Connector Management / SIA Connector API: dominios propios,
        # fuera de /PasswordVault, mismo bearer token (ver
        # components_monitoring_summary/reporte ITR). `_request` acepta
        # una URL absoluta en `path` para pegarles sin tocar `self._base`.
        self.connector_management_url = (
            connector_management_url.rstrip("/") if connector_management_url else None
        )
        self.sia_connector_url = sia_connector_url.rstrip("/") if sia_connector_url else None
        self._auth = auth
        self._sem = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )
        self._auth_lock = asyncio.Lock()
        self._authed = False

    async def __aenter__(self) -> CyberArkClient:
        await self.authenticate()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def authenticate(self, *, force: bool = False) -> None:
        """`force=True` pide un token nuevo aunque haya uno cacheado (ver
        `ISPSSAuth.login`)."""
        async with self._auth_lock:
            await self._auth.login(self._http, force=force)
            self._authed = True

    async def close(self) -> None:
        try:
            await self._auth.logout(self._http)
        finally:
            await self._http.aclose()

    # ------------------------------------------------------------------ #
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        data: dict | None = None,
        max_retries: int = 4,
        retry_safe: bool = True,
        raw: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """`retry_safe=False` para operaciones NO idempotentes (POST de
        creación): un timeout o un 5xx podría haber ejecutado la operación,
        así que no se reintenta — se propaga el error y decide el llamador.
        El re-auth ante 401 (una vez) se mantiene: la request nunca llegó a
        procesarse. `headers` pisa (no reemplaza) los default del cliente
        (`Accept: application/json`) — necesario para endpoints binarios
        como `play_recording`, donde forzar JSON hace que CyberArk devuelva
        otra representación del recurso en vez del stream real. `data`
        manda un body `application/x-www-form-urlencoded` (httpx arma el
        header solo) en vez de JSON — algunos endpoints no documentados
        (`play_recording`) lo requieren."""
        url = path if path.startswith("http") else f"{self._base}{path}"
        last_err: Exception | None = None

        for attempt in range(max_retries):
            async with self._sem:
                try:
                    resp = await self._http.request(
                        method, url, params=params, json=json, data=data, headers=headers
                    )
                except httpx.TransportError as e:  # corte transitorio
                    last_err = e
                    if not retry_safe:
                        raise UpstreamError(
                            f"{method} {path} -> {type(e).__name__} (sin reintento; "
                            f"la operación pudo haberse ejecutado): {e}"
                        )
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

            if resp.status_code == 401 and attempt == 0 and self._authed:
                # el token (quizá cacheado) fue rechazado: pedir uno nuevo
                await self.authenticate(force=True)
                continue
            if retry_safe and resp.status_code in _RETRYABLE_STATUS and attempt < max_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 404:
                raise NotFound(f"{method} {path} -> 404")
            if resp.status_code == 403:
                raise Forbidden(f"{method} {path} -> 403: {resp.text[:200]}")
            if resp.status_code == 409:
                raise Conflict(f"{method} {path} -> 409: {resp.text[:200]}")
            if resp.status_code == 429:
                raise RateLimited(f"{method} {path} -> 429")
            if resp.status_code >= 500:
                raise UpstreamError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
            if resp.status_code >= 400:
                raise CyberArkError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")

            if raw:
                return resp.content
            if not resp.content:
                # 2xx con body vacío es raro para un GET de listado (incluso
                # sin resultados, la API normalmente devuelve {"value": [],
                # "count": 0}) -- se loguea para poder diagnosticar sin
                # tener que reproducirlo (ver docs/security.md /
                # investigación de lab, 2026-09-23). Sin datos de la
                # cuenta ni headers de auth.
                log.warning(
                    "%s %s -> %s con body vacío (content-type=%r, content-length=%r, location=%r)",
                    method,
                    path,
                    resp.status_code,
                    resp.headers.get("content-type"),
                    resp.headers.get("content-length"),
                    resp.headers.get("location"),
                )
                return None
            try:
                return resp.json()
            except ValueError:
                raise UpstreamError(f"{method} {path} -> respuesta no-JSON: {resp.text[:200]}")

        raise UpstreamError(f"{method} {path} -> agotó reintentos ({last_err})")

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        json: Any = None,
        *,
        params: dict | None = None,
        data: dict | None = None,
        retry_safe: bool = False,
        raw: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        # las creaciones NO son idempotentes -> por defecto sin reintento
        return await self._request(
            "POST",
            path,
            params=params,
            json=json,
            data=data,
            retry_safe=retry_safe,
            raw=raw,
            headers=headers,
        )

    async def patch(self, path: str, json: Any = None) -> Any:
        return await self._request("PATCH", path, json=json)  # idempotente

    async def put(self, path: str, json: Any = None) -> Any:
        return await self._request("PUT", path, json=json)  # idempotente

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)  # idempotente

    async def update_account(self, account_id: str, patch: list[dict]) -> dict:
        """PATCH /API/Accounts/{id} con un cuerpo JSON Patch (RFC 6902).
        Devuelve la cuenta actualizada."""
        data = await self.patch(f"/API/Accounts/{account_id}", json=patch)
        return data if isinstance(data, dict) else {}

    async def link_account(self, account_id: str, body: dict) -> dict:
        """POST /API/Accounts/{id}/LinkAccount — vincula una cuenta (logon /
        reconcile / otra) a la cuenta destino. Idempotente: re-linkear el
        mismo `extraPasswordIndex` reemplaza el vínculo -> `retry_safe=True`."""
        data = await self.post(
            f"/API/Accounts/{account_id}/LinkAccount", json=body, retry_safe=True
        )
        return data if isinstance(data, dict) else {}

    async def unlink_account(self, account_id: str, index: int) -> dict:
        """DELETE /API/Accounts/{id}/LinkAccount/{index} — quita un vínculo."""
        data = await self.delete(f"/API/Accounts/{account_id}/LinkAccount/{index}")
        return data if isinstance(data, dict) else {}

    async def _cpm_action(self, account_id: str, action: str) -> dict:
        """POST /API/Accounts/{id}/{Change|Verify|Reconcile} — encola una
        operación del CPM/SRS. Asíncrono: el 200 = pedido encolado. Sin
        reintento (un timeout no debe re-disparar el CPM)."""
        try:
            data = await self.post(f"/API/Accounts/{account_id}/{action}")
        except Conflict as e:
            raise Conflict(
                f"CyberArk rechazó '{action}' (Conflict) para la cuenta {account_id}. "
                "Suele deberse a que ya hay una rotación/verificación en curso para "
                "la cuenta, o a que la cuenta no está en estado de poder ejecutarla "
                f"(sin CPM, deshabilitada, plataforma sin esa acción). Detalle: {e}"
            )
        return data if isinstance(data, dict) else {}

    async def change_account(self, account_id: str) -> dict:
        return await self._cpm_action(account_id, "Change")

    async def verify_account(self, account_id: str) -> dict:
        return await self._cpm_action(account_id, "Verify")

    async def reconcile_account(self, account_id: str) -> dict:
        return await self._cpm_action(account_id, "Reconcile")

    async def get_all(
        self,
        path: str,
        *,
        params: dict | None = None,
        key: str = "value",
        page_size: int = 1000,
    ) -> list:
        """Pagina offset/limit hasta traer todo."""
        merged = dict(params or {})
        out: list = []
        offset = 0
        while True:
            merged.update({"offset": offset, "limit": page_size})
            data = await self.get(path, params=merged)
            batch = data.get(key, data if isinstance(data, list) else [])
            if not batch:
                break
            out.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return out

    # ---- lecturas mínimas para validar el pipe. Fase 1 las expande. ----
    async def server_info(self) -> Any:
        try:
            return await self.get("/API/Server")
        except CyberArkError:
            return await self.get("/WebServices/PIMServices.svc/Verify")

    async def list_accounts(self, search: str | None = None) -> list:
        params = {"search": search} if search else None
        return await self.get_all("/API/Accounts", params=params)

    async def accounts_page(
        self,
        *,
        search: str | None = None,
        sort: str | None = None,
        offset: int = 0,
        limit: int = 100,
        safe_name: str | None = None,
        modified_after: int | None = None,
    ) -> tuple[list, int]:
        """Una página de cuentas + el total real (campo `count` de la API).

        `safe_name` / `modified_after` se traducen al parámetro `filter` de
        Gen2 (`safeName eq` / `modificationTime gt`), lo único que la API
        soporta filtrar server-side.
        """
        params: dict = {"offset": offset, "limit": limit}
        if search:
            params["search"] = search
        if sort:
            params["sort"] = sort
        flt = accounts_filter(safe_name, modified_after)
        if flt:
            params["filter"] = flt
        data = await self.get("/API/Accounts", params=params)
        if not isinstance(data, dict):
            # Bug real (2026-09-23, disparado por el sync nocturno contra un
            # tenant con `_request` devolviendo `None` -- body vacío en una
            # respuesta 2xx): `.get()` sobre `None` tiraba `AttributeError`,
            # sin pasar por la jerarquía de `CyberArkError` que los
            # llamadores (sync on-demand, job nocturno) ya sabían manejar
            # por tenant. Convertirlo en `UpstreamError` lo vuelve
            # recuperable en vez de tumbar el proceso entero.
            raise UpstreamError(f"Get Accounts -> respuesta inesperada: {data!r}")
        return data.get("value", []), int(data.get("count", 0))

    async def safes_page(
        self,
        *,
        search: str | None = None,
        sort: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list, int]:
        params: dict = {"offset": offset, "limit": limit, "includeAccounts": "false"}
        if search:
            params["search"] = search
        if sort:
            params["sort"] = sort
        data = await self.get("/API/Safes", params=params)
        if not isinstance(data, dict):
            raise UpstreamError(f"Get Safes -> respuesta inesperada: {data!r}")
        return data.get("value", []), int(data.get("count", 0))

    async def get_account(self, account_id: str) -> dict:
        return await self.get(f"/API/Accounts/{account_id}")

    async def get_safe(self, safe_id: str) -> dict:
        """GET /API/Safes/{safeUrlId} — un Safe puntual (valida existencia +
        trae managingCPM). 404 si no existe."""
        data = await self.get(f"/API/Safes/{safe_id}")
        return data if isinstance(data, dict) else {}

    async def get_platform_details(self, platform_id: str) -> dict:
        """GET /API/Platforms/{platformId} — "Get Platform Details": la
        política de gestión de contraseñas del CPM (retries, complejidad,
        notificaciones…), NO el esquema de campos del "Add Account" de PVWA
        (verificado 2026-09-11 contra Globex — ver nota en
        app/domain/accounts.py). Se usa sólo para confirmar que la
        plataforma existe (404 si no)."""
        data = await self.get(f"/API/Platforms/{platform_id}")
        return data if isinstance(data, dict) else {}

    async def create_account(self, body: dict) -> dict:
        """POST /API/Accounts — alta de cuenta. NO idempotente (un timeout
        puede haber creado la cuenta igual) -> sin reintento."""
        data = await self.post("/API/Accounts", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def create_safe(self, body: dict) -> dict:
        """POST /API/Safes — alta de safe. NO idempotente -> sin reintento."""
        data = await self.post("/API/Safes", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def update_safe(self, safe_id: str, body: dict) -> dict:
        """PUT /API/Safes/{safeUrlId} — Update Safe (descripción, CPM,
        retención, OLAC). Idempotente."""
        data = await self.put(f"/API/Safes/{safe_id}", json=body)
        return data if isinstance(data, dict) else {}

    async def retrieve_password(
        self, account_id: str, reason: str, version: int | None = None
    ) -> str:
        """POST /API/Accounts/{id}/Password/Retrieve — devuelve la contraseña
        en texto plano (la API responde un string JSON, no un objeto).
        `reason` se manda siempre: algunas safes lo exigen por política
        ("Require reason to retrieve"); si la safe no lo exige, CyberArk lo
        ignora. `retry_safe=False` explícito aunque sea de lectura: un retry
        silencioso duplicaría el evento "Retrieve" en el audit nativo de
        CyberArk (y si el platform tiene "Change password immediately after
        every Retrieve", podría disparar dos rotaciones). El valor devuelto
        NUNCA se loguea (ni acá ni en `detail` de audit).

        `version`: si viene, pide una versión HISTÓRICA de la contraseña en
        vez de la vigente (mismo endpoint, verificado contra `psPAS`
        `Get-PASAccountPassword` — el parámetro real es `Version`,
        mayúscula). Ver docs/specs/done/account-password-versions.md."""
        body: dict = {"reason": reason}
        if version is not None:
            body["Version"] = version
        data = await self.post(
            f"/API/Accounts/{account_id}/Password/Retrieve",
            json=body,
            retry_safe=False,
        )
        return str(data) if data is not None else ""

    async def list_password_versions(self, account_id: str) -> list:
        """GET /API/Accounts/{id}/Secret/Versions?showTemporary=false —
        metadata de versiones históricas de la contraseña (fecha/hora/
        quién la modificó), SIN el valor. Verificado contra `psPAS`
        `Get-PASAccountPasswordVersion` (docs.cyberark.com bloqueó el
        fetch automatizado). `showTemporary=false` oculta las versiones
        temporales del CPM, mismo default que el toggle del PVWA. Ver
        docs/specs/done/account-password-versions.md."""
        data = await self.get(
            f"/API/Accounts/{account_id}/Secret/Versions",
            params={"showTemporary": "false"},
        )
        if isinstance(data, dict):
            versions = data.get("Versions", [])
            return versions if isinstance(versions, list) else []
        return []

    async def delete_account(self, account_id: str) -> None:
        """DELETE /API/Accounts/{id}."""
        await self.delete(f"/API/Accounts/{account_id}")

    async def update_password_in_vault(self, account_id: str, new_password: str) -> None:
        """POST /API/Accounts/{id}/Password/Update — "Change credentials in
        the Vault": actualiza SOLO el valor que CyberArk tiene guardado,
        nunca llama al target. Usar cuando la contraseña real ya se cambió
        por fuera de CyberArk y hay que sincronizar la Vault. `retry_safe`
        False: reintentar un timeout podría duplicar el evento nativo de
        audit "Update Password"."""
        await self.post(
            f"/API/Accounts/{account_id}/Password/Update",
            json={"NewCredentials": new_password},
            retry_safe=False,
        )

    async def set_next_password(self, account_id: str, new_password: str) -> None:
        """POST /API/Accounts/{id}/SetNextPassword — guarda el valor que el
        CPM va a usar en su PRÓXIMO cambio automático (`ChangeImmediately:
        false`); no toca nada ahora. `retry_safe=False` por el mismo
        motivo que `update_password_in_vault`."""
        await self.post(
            f"/API/Accounts/{account_id}/SetNextPassword",
            json={"NewCredentials": new_password, "ChangeImmediately": False},
            retry_safe=False,
        )

    async def delete_safe(self, safe_id: str) -> None:
        """DELETE /API/Safes/{safeUrlId}."""
        await self.delete(f"/API/Safes/{safe_id}")

    async def account_activities(self, account_id: str) -> list:
        data = await self.get(f"/API/Accounts/{account_id}/Activities")
        if isinstance(data, dict):
            rows = data.get("Activities") or data.get("value") or []
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def list_safes(self) -> list:
        return await self.get_all("/API/Safes", params={"includeAccounts": "false"})

    async def saved_filter_accounts(self, saved_filter: str) -> list:
        """Cuentas que matchean un savedFilter de Get Accounts (compliance)."""
        return await self.get_all("/API/Accounts", params={"savedFilter": saved_filter})

    async def component_details(self, component_id: str) -> list | None:
        """Instancias de un componente; None si no aplica (404) o sin permiso (403)."""
        try:
            data = await self.get(f"/API/ComponentsMonitoringDetails/{component_id}")
        except (NotFound, Forbidden):
            return None
        return data.get("ComponentsDetails", []) if isinstance(data, dict) else []

    async def components_monitoring_summary(self) -> dict | None:
        """GET /API/ComponentsMonitoringSummary — {"Components": [...],
        "Vaults": [...]}. None si no aplica (404) o sin permiso (403),
        mismo criterio que component_details. Verificado vía el código
        fuente de psPAS (Get-PASComponentSummary.ps1, docs.cyberark.com
        bloqueó el fetch automatizado) — sólo "Vaults" (Role/IP/IsLoggedOn)
        tiene campos confirmados; "Components" se devuelve tal cual, sin
        campos propios confirmados todavía."""
        try:
            data = await self.get("/API/ComponentsMonitoringSummary")
        except (NotFound, Forbidden):
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------ #
    # Licencias / Connector Management / SIA Connectors — portados de
    # Telemetry-Reporter (cyberark_api.py), confirmados contra un
    # tenant real (Globex). Alimentan el reporte ITR (ver
    # docs/specs/done/itr-report.md); ninguno se usa todavía en otra
    # parte de PAM Web Console.
    # ------------------------------------------------------------------ #
    async def get_license_report(self) -> dict:
        """GET /API/licenses/pcloud/ — mismo dominio que Accounts/
        Safes/Platforms, no hace falta `connector_management_url`/
        `sia_connector_url`."""
        data = await self.get("/API/licenses/pcloud/")
        return data if isinstance(data, dict) else {}

    async def list_connectors(self) -> list:
        """GET /api/connectors del servicio Connector Management —
        dominio propio (`connector_management_url`), mismo bearer token.
        Cada conector trae host/versión/estado + sus `components`
        (Management Agent/CPM/PSM/Password Manager)."""
        if not self.connector_management_url:
            raise CyberArkError("connector_management_url no está configurado para este tenant")
        data = await self.get(f"{self.connector_management_url}/api/connectors")
        if not isinstance(data, dict):
            return []
        return data.get("connectors") or data.get("value") or []

    async def list_connector_pools(self) -> list:
        """GET /api/connector-pools de Connector Management — los pools
        (`{"connectorPools": [{poolId, name, description, ...}]}`). Mismo
        dominio y bearer token que `list_connectors`."""
        if not self.connector_management_url:
            raise CyberArkError("connector_management_url no está configurado para este tenant")
        data = await self.get(f"{self.connector_management_url}/api/connector-pools")
        if not isinstance(data, dict):
            return []
        return data.get("connectorPools") or data.get("value") or []

    async def list_sia_connectors(self) -> list:
        """GET /api/connectors del servicio SIA Connector API
        (`sia_connector_url`) — la tabla "SIA Connectors" del portal,
        DISTINTA de Connector Management. Paginada por cursor
        (`continuationToken`, confirmado contra Globex — no se probó
        con más de una página, el tenant de referencia sólo tenía 2
        conectores). Un 403 acá normalmente significa que Secure
        Infrastructure Access no está licenciado en el tenant, o que el
        service account no tiene el rol "SIA Admin" en Identity
        Administration (separado de los roles de Privilege Cloud)."""
        if not self.sia_connector_url:
            raise CyberArkError("sia_connector_url no está configurado para este tenant")
        items: list = []
        continuation_token: str | None = None
        while True:
            params = {"continuationToken": continuation_token} if continuation_token else None
            data = await self.get(f"{self.sia_connector_url}/api/connectors", params=params)
            if not isinstance(data, dict):
                break
            items.extend(data.get("items") or [])
            continuation_token = data.get("continuationToken")
            if not continuation_token:
                break
        return items

    async def get_sia_connector_details(self, connector_id: str) -> dict | None:
        """GET /api/connectors/{id} de SIA — a diferencia de la lista
        (sólo trae `isLatestVersion`), este sí trae la versión objetivo
        de upgrade. Hay que pedirlo conector por conector. None si no se
        pudo consultar ESE conector puntual (se degrada, no corta el
        resto — ver `get_all_sia_connector_details`)."""
        if not self.sia_connector_url:
            raise CyberArkError("sia_connector_url no está configurado para este tenant")
        data = await self.get(f"{self.sia_connector_url}/api/connectors/{connector_id}")
        return data if isinstance(data, dict) else None

    async def get_all_sia_connector_details(
        self, connector_ids: list[str]
    ) -> dict[str, dict | None]:
        """`get_sia_connector_details` para varios conectores EN
        PARALELO (mismo semáforo de concurrencia que cualquier otra
        llamada del cliente) — None por id si ese conector puntual
        falló, no corta a los demás."""

        async def _one(cid: str) -> tuple[str, dict | None]:
            try:
                return cid, await self.get_sia_connector_details(cid)
            except CyberArkError:
                return cid, None

        pairs = await asyncio.gather(*(_one(cid) for cid in connector_ids))
        return dict(pairs)

    # ------------------------------------------------------------------ #
    # SIA (Secure Infrastructure Access), sólo lectura — ver
    # docs/specs/done/sia-read-only.md. Rutas tomadas del SDK oficial
    # `cyberark/ark-sdk-python` (rama main, revisado el 2026-10-06), SIN
    # confirmar todavía contra un tenant real: los parsers aceptan las
    # variantes razonables de cada respuesta. Cada servicio vive en su
    # propio host (`{sub}.{servicio}.cyberark.cloud`), mismo bearer token.
    # ------------------------------------------------------------------ #
    def isp_service_url(self, service: str) -> str:
        if not self.sia_connector_url:
            raise CyberArkError("sia_connector_url no está configurado para este tenant")
        return isp_service_url(self.sia_connector_url, service)

    async def list_uap_policies(self, target_category: str) -> list:
        """GET api/policies del servicio UAP (políticas unificadas),
        filtrado por categoría (`VM` | `DB`). Paginado por `nextToken`."""
        base = self.isp_service_url("uap")
        items: list = []
        next_token: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "limit": 50,
                "filter": f"(targetCategory eq '{target_category}')",
            }
            if next_token:
                params["nextToken"] = next_token
            data = await self.get(f"{base}/api/policies", params=params)
            if not isinstance(data, dict):
                break
            items.extend(data.get("results") or [])
            prev, next_token = next_token, data.get("nextToken")
            if not next_token or next_token == prev:
                break
        return items

    async def get_uap_policy(self, policy_id: str) -> dict:
        base = self.isp_service_url("uap")
        data = await self.get(f"{base}/api/policies/{quote(policy_id, safe='')}")
        return data if isinstance(data, dict) else {}

    async def list_sia_vm_policies(self) -> list:
        """GET api/access-policies (políticas de VM legado). Paginado por
        `offset` contra `totalCount`."""
        base = self.isp_service_url("dpa")
        items: list = []
        for _ in range(_MAX_PAGES):
            data = await self.get(f"{base}/api/access-policies", params={"offset": len(items)})
            page = (data.get("items") if isinstance(data, dict) else None) or []
            items.extend(page)
            total = data.get("totalCount") if isinstance(data, dict) else None
            if not page or not isinstance(total, int) or len(items) >= total:
                break
        return items

    async def list_sia_db_policies(self) -> list:
        """GET api/adb/access-policies (políticas de DB legado), sin paginar
        en el SDK."""
        base = self.isp_service_url("dpa")
        data = await self.get(f"{base}/api/adb/access-policies")
        return _items(data, "items")

    async def get_sia_policy(self, kind: str, policy_id: str) -> dict:
        """Detalle de una política legado; `kind` = `vm` | `db`."""
        base = self.isp_service_url("dpa")
        prefix = "api/adb/access-policies" if kind == "db" else "api/access-policies"
        data = await self.get(f"{base}/{prefix}/{quote(policy_id, safe='')}")
        return data if isinstance(data, dict) else {}

    async def list_sm_sessions(self, search: str | None, limit: int = 500) -> tuple[list, int]:
        """GET api/sessions de Session Monitoring (compartido entre servicios
        de la plataforma). Sin `search` devuelve las últimas 24 h. Paginado
        por `offset`; corta en `limit` filas. Devuelve (sesiones, total
        filtrado según CyberArk)."""
        base = self.isp_service_url("sessionmonitoring")
        items: list = []
        total = 0
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"offset": len(items)} if items else {}
            if search:
                params["search"] = search
            data = await self.get(f"{base}/api/sessions", params=params or None)
            if not isinstance(data, dict):
                break
            page = data.get("sessions") or []
            total = data.get("filteredCount") or total
            items.extend(page)
            if not page or not data.get("returnedCount") or len(items) >= limit:
                break
        return items[:limit], max(total, len(items))

    async def get_sm_session(self, session_id: str) -> dict:
        base = self.isp_service_url("sessionmonitoring")
        data = await self.get(f"{base}/api/sessions/{quote(session_id, safe='')}")
        return data if isinstance(data, dict) else {}

    async def list_sm_session_activities(self, session_id: str, limit: int = 1000) -> list:
        base = self.isp_service_url("sessionmonitoring")
        url = f"{base}/api/sessions/{quote(session_id, safe='')}/activities"
        items: list = []
        for _ in range(_MAX_PAGES):
            data = await self.get(url, params={"offset": len(items)} if items else None)
            page = (data.get("activities") if isinstance(data, dict) else None) or []
            items.extend(page)
            if not page or len(items) >= limit:
                break
        return items[:limit]

    async def list_sia_db_resources(self) -> list:
        """GET api/adb/resources — bases de datos registradas en SIA."""
        base = self.isp_service_url("dpa")
        return _items(await self.get(f"{base}/api/adb/resources"), "items")

    async def list_sia_target_sets(self) -> list:
        """GET api/targetsets — target sets de VM (dominio/sufijo/target)."""
        base = self.isp_service_url("dpa")
        return _items(await self.get(f"{base}/api/targetsets"), "target_sets", "targetSets")

    async def list_sia_db_secrets(self) -> list:
        """GET api/adb/secretsmgmt/secrets — METADATOS de los secretos de DB
        (el valor nunca viene en el listado; igual `app.domain.sia` filtra
        por lista blanca)."""
        base = self.isp_service_url("dpa")
        return _items(await self.get(f"{base}/api/adb/secretsmgmt/secrets"), "secrets")

    async def list_sia_vm_secrets(self) -> list:
        """GET api/secrets — secretos de VM (ProvisionerUser/PCloudAccount),
        sin el valor."""
        base = self.isp_service_url("dpa")
        data = await self.get(
            f"{base}/api/secrets", params={"secret_type": "ProvisionerUser,PCloudAccount"}
        )
        return _items(data, "secrets", "items")

    async def get_sia_pcloud_account(self, secret_id: str) -> dict | None:
        """Cuenta de Privilege Cloud a la que apunta una strong account
        `PCloudAccount`: la lista de `api/secrets` no la trae. Misma llamada
        que hace el portal al abrir la strong account (capturada en Globex
        el 2026-10-07): `GET api/secrets/actions/retrieve_pcloud_account_by_admin`
        → `secrets_results[0].pcloud_account = {safe, account_name,
        account_id, username}`. Sin contraseña; igual el dominio lee sólo
        esas claves. None si no vino o falló (se degrada, no corta)."""
        base = self.isp_service_url("dpa")
        try:
            data = await self.get(
                f"{base}/api/secrets/actions/retrieve_pcloud_account_by_admin",
                params={"secret_id": secret_id},
            )
        except CyberArkError:
            return None
        for r in _items(data, "secrets_results", "secretsResults"):
            acc = r.get("pcloud_account") or r.get("pcloudAccount") if isinstance(r, dict) else None
            if isinstance(acc, dict):
                return acc
        return None

    async def get_sia_settings(self) -> dict:
        """GET api/settings — configuración del servicio SIA del tenant."""
        base = self.isp_service_url("dpa")
        data = await self.get(f"{base}/api/settings")
        return data if isinstance(data, dict) else {}

    async def live_sessions(self) -> list:
        """Sesiones PSM en curso (Get Live Sessions)."""
        data = await self.get("/API/LiveSessions", params={"Limit": 1000})
        if isinstance(data, dict):
            rows = data.get("LiveSessions") or data.get("value") or []
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def get_live_session(self, session_id: str) -> dict:
        """GET /API/LiveSessions/{id} — una sesión PSM puntual. Verificado
        contra `Get-PASPSMSession` de `psPAS` (docs.cyberark.com bloqueó el
        fetch automatizado)."""
        data = await self.get(f"/API/LiveSessions/{session_id}")
        return data if isinstance(data, dict) else {}

    async def terminate_session(self, session_guid: str) -> None:
        """POST /api/LiveSessions/{id}/Terminate — corta la sesión; el
        usuario puede reconectarse. Verificado contra `Stop-PASPSMSession`
        de `psPAS`. **`session_guid` es el `SessionGuid`, NO el
        `SessionID`** — confirmado contra Globex (2026-09-24): con el
        `SessionID` (ej. `"46_1142147"`) CyberArk devuelve 400
        `CAWS00001E "Live Session value is invalid"`. El parámetro real
        de `Stop-PASPSMSession` en `psPAS` es `LiveSessionId` con alias
        `SessionGuid`, a diferencia de `get_live_session`/`live_sessions`
        que sí aceptan el `SessionID`. Idempotente (estado final =
        terminada)."""
        await self.post(f"/api/LiveSessions/{session_guid}/Terminate", retry_safe=True)

    async def list_recordings(
        self,
        from_time: int,
        to_time: int,
        *,
        safe: str | None = None,
        search: str | None = None,
        limit: int = 25,
    ) -> list:
        """GET /API/Recordings — grabaciones de sesiones PSM YA TERMINADAS
        (distinto de `live_sessions`). Verificado contra la doc oficial de
        CyberArk ("Get recordings", portal Privilege Cloud con Secrets
        Rotation) + `Get-PASPSMRecording` de `psPAS`. `from_time`/`to_time`
        (Unix time) siempre explícitos: esa doc no documenta un default de
        rango para esta operación puntual, a diferencia de "Get active
        sessions"."""
        params: dict[str, str | int] = {"FromTime": from_time, "ToTime": to_time, "Limit": limit}
        if safe:
            params["Safe"] = safe
        if search:
            params["Search"] = search
        data = await self.get("/API/Recordings", params=params)
        if isinstance(data, dict):
            rows = data.get("Recordings") or data.get("value") or []
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def get_recording_activities(self, session_id: str) -> list:
        """GET /API/Recordings/{id}/activities — eventos capturados durante
        la grabación (comandos, sucesos). `session_id` es el `SessionID` que
        ya trae cada fila de `list_recordings` — no un `RecordingID`
        separado (nombre real confirmado en la doc oficial de "Get
        recording details")."""
        data = await self.get(f"/API/Recordings/{session_id}/activities")
        if isinstance(data, dict):
            acts = data.get("Activities") or []
            return acts if isinstance(acts, list) else []
        return data if isinstance(data, list) else []

    async def play_recording(self, session_id: str) -> bytes:
        """POST /API/Recordings/{id}/Play — descarga el archivo de la
        grabación (bytes crudos, no JSON: "the result is the video
        recording data stream", doc oficial "Play recording"). Verificado
        también contra `Export-PASPSMRecording` de `psPAS`. `retry_safe=
        False`: un reintento duplicaría el evento "Play" en el audit nativo
        de CyberArk (mismo criterio que `retrieve_password`).

        `Accept: */*` explícito: el cliente manda `Accept: application/json`
        por default en TODAS las requests (ver `__init__`), incorrecto para
        un endpoint binario.

        `data={"isVideo": "true"}` (form-urlencoded, NO JSON): la doc
        oficial dice "Body parameters: None", pero es incompleta — según
        los headers reales de una llamada exitosa del PVWA (inspeccionados
        contra Globex, 2026-09-17), el request manda un body de 12 bytes
        `application/x-www-form-urlencoded` que calza exacto con
        `isVideo=true`. Sin este parámetro, CyberArk devuelve el
        componente de texto/keystrokes de la grabación en vez del video,
        aun cuando `VideoSize` > 0 en `list_recordings` — confirmado
        probando contra Globex (primer intento, sin `data`, trajo un
        `.Keystrokes.txt` de una grabación con `VideoSize` de varios MB).
        Sin confirmar todavía si existe un `isVideo=false` explícito para
        pedir el componente de texto a propósito — no hace falta para el
        alcance actual (sólo se descarga el video)."""
        data = await self.post(
            f"/API/Recordings/{session_id}/Play",
            retry_safe=False,
            raw=True,
            headers={"Accept": "*/*"},
            data={"isVideo": "true"},
        )
        return data if isinstance(data, bytes) else bytes(data or b"")

    async def safe_members(self, safe_id: str) -> list:
        """Miembros de un Safe puntual (paginado)."""
        return await self.get_all(f"/API/Safes/{safe_id}/Members/", key="value", page_size=25)

    async def list_platforms(self) -> list:
        data = await self.get("/API/Platforms")
        return data.get("Platforms", data.get("value", []))

    async def list_psm_servers(self) -> list:
        """PSM configurados en el tenant (Get PSM Servers)."""
        data = await self.get("/API/PSM/Servers")
        if isinstance(data, dict):
            rows = data.get("PSMServers") or data.get("value") or []
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def list_target_platforms(self) -> list:
        """GET /API/Platforms/Targets/ ("Get target platforms") — a
        diferencia de `list_platforms()`, trae el ID NUMÉRICO interno que
        exige la API de gestión de sesiones (`get_platform_psm`/
        `update_platform_psm`) y el `PrivilegedSessionManagement.PSMServerId`
        actual de cada plataforma en la misma respuesta."""
        data = await self.get("/API/Platforms/Targets/")
        if isinstance(data, dict):
            rows = data.get("Platforms") or data.get("value") or []
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def get_platform_psm(self, numeric_id: int | str) -> dict:
        """GET /API/Platforms/Targets/{id}/PrivilegedSessionManagement/ — el
        objeto COMPLETO de gestión de sesiones de la plataforma (PSMServerId,
        PSMServerName, PSMConnectors, ...)."""
        data = await self.get(f"/API/Platforms/Targets/{numeric_id}/PrivilegedSessionManagement/")
        return data if isinstance(data, dict) else {}

    async def update_platform_psm(self, numeric_id: int | str, body: dict) -> dict:
        """PUT /API/Platforms/Targets/{id}/PrivilegedSessionManagement/ —
        reemplaza el objeto COMPLETO (no hace merge). `body` debe ser el
        objeto actual completo con sólo PSMServerId/PSMServerName mutados —
        nunca construirlo desde cero: es un bug conocido de CyberArk que
        mandar sólo esos dos campos borra PSMConnectors/Connection
        Components. Ver docs/specs/platform-psm-migration.md."""
        data = await self.put(
            f"/API/Platforms/Targets/{numeric_id}/PrivilegedSessionManagement/", json=body
        )
        return data if isinstance(data, dict) else {}

    async def activate_target_platform(self, numeric_id: int | str) -> None:
        """POST /API/Platforms/Targets/{id}/Activate — endpoint no
        documentado públicamente (docs.cyberark.com bloquea el fetch
        automatizado), verificado contra `Enable-PASPlatform` del módulo
        open-source `psPAS`. Sin body. `retry_safe` default (idempotente:
        activar una plataforma ya activa es un no-op del lado de
        CyberArk)."""
        await self.post(f"/API/Platforms/Targets/{numeric_id}/Activate")

    async def deactivate_target_platform(self, numeric_id: int | str) -> None:
        """POST /API/Platforms/Targets/{id}/Deactivate — ver
        `activate_target_platform`, misma fuente (`Disable-PASPlatform`)."""
        await self.post(f"/API/Platforms/Targets/{numeric_id}/Deactivate")

    async def duplicate_target_platform(self, numeric_id: int | str, body: dict) -> dict:
        """POST /API/Platforms/Targets/{id}/Duplicate — crea una copia de la
        plataforma (`body`: `{name, description}`). Verificado contra
        `Copy-PASPlatform` de `psPAS`. NO idempotente (un timeout puede
        haber creado la copia igual) -> sin reintento."""
        data = await self.post(
            f"/API/Platforms/Targets/{numeric_id}/Duplicate", json=body, retry_safe=False
        )
        return data if isinstance(data, dict) else {}

    async def import_platform(self, file_bytes: bytes) -> dict:
        """POST /API/Platforms/Import — crea una plataforma NUEVA a partir
        de un paquete `.zip` (formato "Export Platform"). NO idempotente
        (un timeout puede haber creado la plataforma igual) -> sin
        reintento. **Ambigüedad sin resolver** (ver
        docs/specs/platform-import.md): `psPAS` arma el body con el
        archivo como array de bytes crudo, inusual para JSON — se manda
        primero en base64 (lo más común en APIs REST reales); si CyberArk
        lo rechaza, el error llega tal cual al llamador, sin reintentar
        con el otro formato."""
        body = {"ImportFile": base64.b64encode(file_bytes).decode("ascii")}
        data = await self.post("/API/Platforms/Import", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def delete_target_platform(self, numeric_id: int | str) -> None:
        """DELETE /API/Platforms/Targets/{id} — verificado contra
        `Remove-PASPlatform` de `psPAS`. Ver docs/specs/platform-lifecycle.md
        (guarda un backup del "Export Platform" ANTES de llamar acá)."""
        await self.delete(f"/API/Platforms/Targets/{numeric_id}")

    async def export_platform(self, platform_id: str) -> bytes:
        """POST /API/Platforms/{PlatformID}/Export?platformID={PlatformID} —
        usa el `PlatformID` STRING (a diferencia de activate/deactivate/
        duplicate/delete, que usan el ID numérico) — inconsistencia real de
        la API de CyberArk, verificada contra `Export-PASPlatform` de
        `psPAS`. Devuelve el paquete `.zip` como bytes crudos, no JSON."""
        data = await self.post(
            f"/API/Platforms/{platform_id}/Export",
            params={"platformID": platform_id},
            retry_safe=False,
            raw=True,
        )
        return data if isinstance(data, bytes) else bytes(data or b"")

    async def list_users(self, search: str | None = None) -> list:
        params: dict[str, str] = {"ExtendedDetails": "true"}
        if search:
            params["search"] = search
        data = await self.get("/API/Users", params=params)
        return data.get("Users", data.get("value", []))

    async def add_safe_member(self, safe_id: str, body: dict) -> dict:
        """POST /API/Safes/{safeUrlId}/Members — agrega un miembro. NO
        idempotente (un 2º POST del mismo miembro devuelve 409) -> sin
        reintento."""
        data = await self.post(f"/API/Safes/{safe_id}/Members", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def update_safe_member(self, safe_id: str, member_name: str, body: dict) -> dict:
        """PUT /API/Safes/{safeUrlId}/Members/{memberName} — permisos/
        vencimiento de un miembro existente. Idempotente. `member_name` se
        codifica (puede traer `\\` de un usuario de directorio o espacios,
        inválidos sin codificar en un segmento de path)."""
        path = f"/API/Safes/{safe_id}/Members/{quote(member_name, safe='')}"
        data = await self.put(path, json=body)
        return data if isinstance(data, dict) else {}

    async def remove_safe_member(self, safe_id: str, member_name: str) -> None:
        """DELETE /API/Safes/{safeUrlId}/Members/{memberName}. Idempotente."""
        path = f"/API/Safes/{safe_id}/Members/{quote(member_name, safe='')}"
        await self.delete(path)

    async def get_user(self, user_id: str) -> dict:
        """Un usuario del Vault puntual (Get User Details)."""
        data = await self.get(f"/API/Users/{user_id}")
        return data if isinstance(data, dict) else {}

    async def activate_user(self, user_id: str) -> dict:
        """POST /API/Users/{id}/activate — reactiva un usuario suspendido por
        intentos fallidos de login. Idempotente (estado final = activo)."""
        data = await self.post(f"/API/Users/{user_id}/activate", retry_safe=True)
        return data if isinstance(data, dict) else {}

    async def create_user(self, body: dict) -> dict:
        """POST /api/Users — alta de usuario del Vault. NO idempotente (un
        timeout puede haber creado el usuario igual) -> sin reintento. Ver
        docs/specs/vault-users-groups-crud.md (casing de campos verificado
        contra `New-PASUser` de `psPAS`, Gen2)."""
        data = await self.post("/api/Users", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def update_user(self, user_id: str, body: dict) -> dict:
        """PUT /api/Users/{id} — editar un usuario existente. Idempotente."""
        data = await self.put(f"/api/Users/{user_id}", json=body)
        return data if isinstance(data, dict) else {}

    async def delete_user(self, user_id: str) -> None:
        """DELETE /api/Users/{id}."""
        await self.delete(f"/api/Users/{user_id}")

    async def get_group(self, group_id: str) -> dict:
        """GET /API/UserGroups/{id} — objeto completo del grupo (incluye
        `members`, a diferencia de `user_group_members`, que sólo lo
        normaliza)."""
        data = await self.get(f"/API/UserGroups/{group_id}")
        return data if isinstance(data, dict) else {}

    async def create_group(self, body: dict) -> dict:
        """POST /API/UserGroups — alta de grupo. NO idempotente -> sin
        reintento."""
        data = await self.post("/API/UserGroups", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def update_group(self, group_id: str, body: dict) -> dict:
        """PUT /API/UserGroups/{id} — sólo renombra (`GroupName` es el único
        campo que "Update Group" acepta). Idempotente."""
        data = await self.put(f"/API/UserGroups/{group_id}", json=body)
        return data if isinstance(data, dict) else {}

    async def delete_group(self, group_id: str) -> None:
        """DELETE /API/UserGroups/{id}."""
        await self.delete(f"/API/UserGroups/{group_id}")

    async def add_group_member(self, group_id: str, body: dict) -> dict:
        """POST /API/UserGroups/{id}/Members — agrega un miembro. NO
        idempotente (un 2º POST del mismo miembro falla) -> sin reintento."""
        data = await self.post(f"/API/UserGroups/{group_id}/Members", json=body, retry_safe=False)
        return data if isinstance(data, dict) else {}

    async def remove_group_member(self, group_id: str, member: str) -> None:
        """DELETE /API/UserGroups/{id}/members/{member}. `member` se
        codifica (mismo motivo que `remove_safe_member`)."""
        await self.delete(f"/API/UserGroups/{group_id}/members/{quote(member, safe='')}")

    async def redrock_query(self, script: str) -> list[dict]:
        """Consulta la API Redrock de Identity (`POST {identity}/Redrock/query`).
        Devuelve las filas (`Result.Results[].Row`). Sólo lecturas (SELECT)."""
        idurl = getattr(self._auth, "identity_tenant_url", None)
        if not idurl:
            raise CyberArkError("el tenant no tiene Identity configurado (Redrock no disponible)")
        data = await self._request(
            "POST", f"{idurl.rstrip('/')}/Redrock/query", json={"Script": script}, retry_safe=True
        )
        if not isinstance(data, dict):
            return []
        if data.get("success") is False:
            raise CyberArkError(
                f"Redrock: {data.get('Message') or data.get('Exception') or 'la consulta falló'}"
            )
        results = (data.get("Result") or {}).get("Results") or []
        return [r["Row"] for r in results if isinstance(r, dict) and isinstance(r.get("Row"), dict)]

    async def get_identity_users_full(self, page_size: int = 200) -> list[dict]:
        """Todos los usuarios de Identity (tabla `Users` vía Redrock), con
        LastLogin/Status/Source — más campos que `GET /identity-users`
        (`app/api/identity.py`, sólo ID/Username/DisplayName/Email/Status),
        para alimentar `analyze_users` del reporte ITR. Paginado con
        LIMIT/OFFSET (SQL, no un parámetro de la API) — portado de
        `Telemetry-Reporter/cyberark_api.py`: sin esto, Redrock
        devuelve un subconjunto acotado por algún tope por defecto
        (confirmado contra un tenant real: sólo 75 de 185 usuarios sin la
        paginación)."""
        rows: list[dict] = []
        offset = 0
        while True:
            script = (
                "SELECT ID, Username, DisplayName, UserType, Status, StatusEnum, "  # noqa: S608
                "SourceDs, SourceDsLocalized, SourceDsType, LastLogin, LastInvite "
                f"FROM Users LIMIT {page_size} OFFSET {offset}"
            )
            page = await self.redrock_query(script)
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    def _identity_url(self) -> str:
        idurl = getattr(self._auth, "identity_tenant_url", None)
        if not idurl:
            raise CyberArkError("el tenant no tiene Identity configurado")
        return idurl.rstrip("/")

    async def identity_get_user_info(self, user_id: str) -> dict:
        """POST {identity}/UserMgmt/GetUserInfo?ID=<uuid> — atributos
        completos de un usuario de Identity. La doc de CyberArk la marca
        como "high CPU intensive": sólo se llama para el detalle de un
        usuario puntual, nunca para listar."""
        data = await self._request(
            "POST",
            f"{self._identity_url()}/UserMgmt/GetUserInfo",
            params={"ID": user_id},
            retry_safe=True,
        )
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (
                (data or {}).get("Message") or (data or {}).get("Exception") or "GetUserInfo falló"
            )
            raise CyberArkError(f"Identity GetUserInfo: {msg}")
        return data.get("Result") or {}

    async def identity_find_user(self, username: str) -> dict | None:
        """Un usuario de Identity por username exacto (sin distinguir
        mayúsculas), vía Redrock. `username` se sanea antes de entrar al SQL
        (Redrock no tiene binds). `None` si no existe."""
        term = "".join(c for c in username if c.isalnum() or c in "._-@").strip()
        if not term:
            return None
        rows = await self.redrock_query(
            "SELECT ID, Username, DisplayName FROM User "  # noqa: S608
            f"WHERE Username = '{term}' COLLATE NOCASE LIMIT 1"
        )
        return rows[0] if rows else None

    async def identity_user_roles(self, user_id: str) -> list[str]:
        """Nombres de los roles de Identity de un usuario —
        `POST {identity}/UserMgmt/GetUsersRolesAndAdministrativeRights?id=<uuid>`
        (el mismo endpoint que usa `Get-IDUserRole` del módulo
        `IdentityCommand`). Filas en `Result.Results[].Row`; el nombre viene
        en `Name` (se acepta `RoleName` por si acaso). Pendiente confirmar
        contra un tenant real."""
        data = await self._request(
            "POST",
            f"{self._identity_url()}/UserMgmt/GetUsersRolesAndAdministrativeRights",
            params={"id": user_id},
            retry_safe=True,
        )
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (
                (data or {}).get("Message")
                or (data or {}).get("Exception")
                or "GetUsersRolesAndAdministrativeRights falló"
            )
            raise CyberArkError(f"Identity roles del usuario: {msg}")
        results = (data.get("Result") or {}).get("Results") or []
        names: list[str] = []
        for r in results:
            row = r.get("Row") if isinstance(r, dict) else None
            if isinstance(row, dict):
                name = row.get("Name") or row.get("RoleName")
                if name:
                    names.append(str(name))
        return names

    async def identity_role_members(self, role_id: str) -> list[dict]:
        """Miembros de un rol de Identity —
        `POST {identity}/Roles/GetRoleMembers?name=<id del rol>` (el
        parámetro se llama `name` pero recibe el ID). Filas en
        `Result.Results[].Row` con `Guid`/`Name`/`Type` (User/Group/Role).
        Un rol dinámico (`RoleType=Script`) calcula su membresía con un
        script y puede devolver la lista vacía. Pendiente confirmar contra
        un tenant real."""
        data = await self._request(
            "POST",
            f"{self._identity_url()}/Roles/GetRoleMembers",
            params={"name": role_id},
            retry_safe=True,
        )
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (
                (data or {}).get("Message")
                or (data or {}).get("Exception")
                or "GetRoleMembers falló"
            )
            raise CyberArkError(f"Identity miembros del rol: {msg}")
        results = (data.get("Result") or {}).get("Results") or []
        return [r["Row"] for r in results if isinstance(r, dict) and isinstance(r.get("Row"), dict)]

    async def identity_role_add_members(
        self, role_id: str, *, users: list[str], roles: list[str]
    ) -> None:
        """Agrega miembros a un rol de Identity —
        `POST {identity}/Roles/UpdateRole` con `Name` = ID del rol y
        `Users.Add`/`Roles.Add` = IDs a sumar (el resto del rol no se toca).
        Agregar a alguien que ya es miembro no cambia nada ->
        `retry_safe=True`. Pendiente confirmar contra un tenant real."""
        body: dict[str, Any] = {"Name": role_id}
        if users:
            body["Users"] = {"Add": users}
        if roles:
            body["Roles"] = {"Add": roles}
        data = await self._request(
            "POST",
            f"{self._identity_url()}/Roles/UpdateRole",
            json=body,
            retry_safe=True,
        )
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (data or {}).get("Message") or (data or {}).get("Exception") or "UpdateRole falló"
            raise CyberArkError(f"Identity agregar miembros al rol: {msg}")

    async def identity_is_user_locked(self, user: str) -> bool:
        """POST {identity}/UserMgmt/IsUserCloudLocked?user=<nombre-o-uuid>."""
        data = await self._request(
            "POST",
            f"{self._identity_url()}/UserMgmt/IsUserCloudLocked",
            params={"user": user},
            retry_safe=True,
        )
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (
                (data or {}).get("Message")
                or (data or {}).get("Exception")
                or "IsUserCloudLocked falló"
            )
            raise CyberArkError(f"Identity IsUserCloudLocked: {msg}")
        return str(data.get("Result")).lower() == "true"

    async def identity_set_cloud_lock(self, user_id: str, lock: bool) -> None:
        """POST {identity}/UserMgmt/SetCloudLock?lockUser=<bool>&user=<uuid>
        — exige el ID (no el nombre), a diferencia de `IsUserCloudLocked`.
        Idempotente (el estado final es fijo) -> `retry_safe=True`."""
        data = await self._request(
            "POST",
            f"{self._identity_url()}/UserMgmt/SetCloudLock",
            params={"lockUser": str(lock).lower(), "user": user_id},
            retry_safe=True,
        )
        if isinstance(data, dict) and data.get("success") is False:
            msg = data.get("Message") or data.get("Exception") or "SetCloudLock falló"
            raise CyberArkError(f"Identity SetCloudLock: {msg}")

    async def create_identity_role(self, name: str, description: str | None = None) -> dict:
        """POST {identity}/Roles/StoreRole — crea un rol de Identity VACÍO
        (sin miembros ni permisos administrativos, fuera de alcance de este
        corte, ver `docs/specs/done/identity-roles-crud.md`). Endpoint/body
        confirmados contra el SDK oficial `ark-sdk-golang`
        (`services/identity/roles`, que llama exactamente a esta ruta con
        `{"Name": ..., "Description": ...}`) — `api-docs.cyberark.com`
        bloquea el fetch sin sesión logueada, mismo bloqueo que el resto de
        Identity. `retry_safe=False`: un timeout puede haber creado el rol,
        reintentar duplicaría."""
        body: dict[str, Any] = {"Name": name}
        if description:
            body["Description"] = description
        data = await self._request(
            "POST", f"{self._identity_url()}/Roles/StoreRole", json=body, retry_safe=False
        )
        if not isinstance(data, dict) or data.get("success") is False:
            msg = (data or {}).get("Message") or (data or {}).get("Exception") or "StoreRole falló"
            raise CyberArkError(f"Identity StoreRole: {msg}")
        result = data.get("Result") or {}
        return {"id": result.get("_RowKey"), "name": name}

    async def list_user_groups(self) -> list:
        """Grupos del Vault y de directorio (Get User Groups). Mismo permiso
        'Audit users' que /API/Users; responde sin paginar."""
        data = await self.get("/API/UserGroups")
        if isinstance(data, dict):
            rows = data.get("value") or data.get("Groups") or []
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def user_group_members(self, group_id: str) -> list[dict]:
        """Miembros de un grupo puntual (Get User Group). Normaliza el array
        `members` a {memberId, memberName, memberType}."""
        data = await self.get(f"/API/UserGroups/{group_id}")
        raw = data.get("members", []) if isinstance(data, dict) else []
        members: list[dict] = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            members.append(
                {
                    "memberId": m.get("id") or m.get("memberId") or m.get("userId"),
                    "memberName": (
                        m.get("username")
                        or m.get("userName")
                        or m.get("name")
                        or m.get("memberName")
                    ),
                    "memberType": m.get("memberType") or m.get("type"),
                }
            )
        return members

    def _discovery_url(self) -> str:
        """La API de Discovery (Gen2, Privilege Cloud/ISPSS) vive en el
        dominio base del tenant SIN el prefix `/PasswordVault` -- confirmado
        contra la doc oficial de CyberArk (`docs.cyberark.com/manage/latest/
        en/content/discovery/discovery-discoveredaccountsservice-*.htm`,
        ej. `https://<subdomain>.privilegecloud.cyberark.cloud/api/
        discovered-accounts/`), a diferencia de Connector Management/SIA que
        sí necesitan una URL de tenant aparte -- acá es el mismo `pvwa_url`,
        sólo cambia el path."""
        return self._base.removesuffix("/PasswordVault")

    async def list_discovered_accounts(
        self,
        *,
        search: str | None = None,
        search_on_all_fields: bool = False,
        offset: int = 0,
        limit: int = 50,
        sort: str | None = None,
        filter_: str | None = None,
        extended_details: bool = False,
    ) -> dict:
        """GET /api/discovered-accounts/ -- devuelve
        `{"items": [...], "totalCount": N, "nextCursor", "prevCursor"}`.
        Cada item trae `identifiers`/`customProperties` como diccionarios
        propios (no campos planos), `type`/`subType`/`isPrivileged` sueltos."""
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if search:
            params["search"] = search
        if search_on_all_fields:
            params["searchOnAllFields"] = "true"
        if sort:
            params["sort"] = sort
        if filter_:
            params["filter"] = filter_
        if extended_details:
            params["extendedDetails"] = "true"
        data = await self.get(f"{self._discovery_url()}/api/discovered-accounts/", params=params)
        return data if isinstance(data, dict) else {}

    async def get_discovered_account(
        self, discovered_account_id: str, *, extended_details: bool = False
    ) -> dict:
        params = {"extendedDetails": "true"} if extended_details else None
        data = await self.get(
            f"{self._discovery_url()}/api/discovered-accounts/{discovered_account_id}",
            params=params,
        )
        return data if isinstance(data, dict) else {}

    async def get_discovered_account_activities(self, discovered_account_id: str) -> list:
        data = await self.get(
            f"{self._discovery_url()}/api/discovered-accounts/{discovered_account_id}/activities"
        )
        if isinstance(data, dict):
            rows = data.get("items", [])
            return rows if isinstance(rows, list) else []
        return data if isinstance(data, list) else []

    async def onboard_discovered_account(
        self,
        discovered_account_id: str,
        *,
        safe_name: str,
        platform_id: str,
        additional_properties: dict | None = None,
        tags: list[str] | None = None,
        secret: str | None = None,
        reset_secret: bool = True,
    ) -> dict:
        """POST .../{id}/onboard -- endpoint REAL de Discovery (no el
        workaround de Fase 4 primer corte, que reusaba `account.create` con
        un body que no coincidía). Mueve la cuenta de la lista de
        descubiertas a las cuentas activas del Vault. `retry_safe=False`:
        crea una cuenta real, no idempotente."""
        body: dict[str, Any] = {
            "coreAttributes": {"safeName": safe_name, "platformId": platform_id},
            "resetSecret": reset_secret,
        }
        if additional_properties:
            body["additionalProperties"] = additional_properties
        if tags:
            body["tags"] = tags
        if secret:
            body["secret"] = secret
        data = await self.post(
            f"{self._discovery_url()}/api/discovered-accounts/{discovered_account_id}/onboard",
            json=body,
            retry_safe=False,
        )
        return data if isinstance(data, dict) else {}

    async def ignore_discovered_account(
        self, discovered_account_id: str, *, reason: str | None = None
    ) -> None:
        """POST .../{id}/ignore -- mueve la cuenta a la lista de ignoradas,
        no vuelve a aparecer en próximos scans. Idempotente (estado final
        fijo) -> `retry_safe=True`."""
        body = {"reason": reason} if reason else None
        await self.post(
            f"{self._discovery_url()}/api/discovered-accounts/{discovered_account_id}/ignore",
            json=body,
            retry_safe=True,
        )

    async def restore_discovered_account(self, discovered_account_id: str) -> None:
        """POST .../{id}/restore -- inverso de `ignore_discovered_account`."""
        await self.post(
            f"{self._discovery_url()}/api/discovered-accounts/{discovered_account_id}/restore",
            retry_safe=True,
        )

    async def delete_discovered_account(self, discovered_account_id: str) -> None:
        """DELETE .../{id} -- saca la cuenta de la lista de descubiertas.
        No afecta el próximo scan: puede volver a aparecer."""
        await self.delete(
            f"{self._discovery_url()}/api/discovered-accounts/{discovered_account_id}"
        )


def build_client(
    tenant: Any, secret: dict, *, max_concurrency: int = 8, token_cache_seconds: int = 0
) -> CyberArkClient:
    """
    tenant: instancia de app.models.Tenant
    secret: dict del secret store.
        privilege_cloud -> {"client_id": "...", "client_secret": "..."}
        self_hosted      -> {"username": "...", "password": "...", "auth_method": "cyberark"}
    """
    deployment = getattr(tenant.deployment, "value", tenant.deployment)

    if deployment == "self_hosted":
        auth: ISPSSAuth | SelfHostedAuth = SelfHostedAuth(
            pvwa_url=tenant.pvwa_url,
            method=secret.get("auth_method", "cyberark"),
            username=secret["username"],
            password=secret["password"],
        )
    else:
        auth = ISPSSAuth(
            identity_tenant_url=tenant.identity_tenant_url,
            client_id=secret["client_id"],
            client_secret=secret["client_secret"],
            cache_max_seconds=token_cache_seconds,
        )

    # modo demo (app/demo/): mismas llamadas, respondidas por un transport mock
    from app.demo import demo_transport, is_demo

    transport = demo_transport(tenant.id) if is_demo() else None

    return CyberArkClient(
        tenant.pvwa_url,
        auth,
        verify_ssl=tenant.verify_ssl,
        max_concurrency=max_concurrency,
        connector_management_url=tenant.connector_management_url,
        sia_connector_url=tenant.sia_connector_url,
        transport=transport,
    )
