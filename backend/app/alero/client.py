"""
Cliente async de CyberArk Remote Access (Alero) -- acceso remoto de
terceros/proveedores, API propia (`api.alero.io`), separada de Privilege
Cloud/Identity. Auth: Private Key JWT Client Authentication (RFC 7523)
contra el token endpoint del discovery document del service account.

Puerto directo de `Alero-ApiClient.ps1` (Telemetry-Reporter), que ya
había resuelto el modelo de auth contra un tenant real (Globex) -- ver
ese archivo para el detalle de cómo se dedujo (iss/sub con el sufijo
".ExternalServiceAccount", aud = issuer del discovery y no el
token_endpoint, límite de "limit" en 100 aunque se pida más). En Python
no hace falta parsear el PEM a mano: `cryptography` (dependencia de
PyJWT para RS256) importa PKCS1 directo.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_PAGE_SIZE = 100  # la API capea "limit" en 100 aunque se pida más (visto empíricamente)
_ACTIVITIES_PAGE_SIZE = 500  # tope documentado de /v2-edge/activities, distinto del resto
_API_BASE = "https://api.alero.io"
# mismo default que AleroLookbackYears en License_Candidates_Report.ps1
_LAST_LOGIN_LOOKBACK_YEARS = 3
_LAST_LOGIN_CHUNK_MS = 30 * 24 * 3600_000  # ventanas de 30 días, ver get_vendor_last_login

# activityTypes es obligatorio en GET /v2-edge/activities (confirmado en el
# swagger completo) -- no hay un "todos" implícito, hay que mandar la
# enumeración entera para replicar el feed "Activities" del portal sin
# filtro. Lista completa del enum de `Activity.activityType`.
ALL_ACTIVITY_TYPES = [
    "ApplicationCreated",
    "ApplicationDeleted",
    "ApplicationUpdated",
    "ApplicationEnabled",
    "ApplicationDisabled",
    "ApplicationUserLogin",
    "ConnectorCreated",
    "ConnectorDeleted",
    "ConnectorInitializationExtended",
    "ConnectorInitialized",
    "ConnectorUpdated",
    "ConnectorLdapUpdated",
    "ConnectorLdapInitialized",
    "ConnectorLdapStopped",
    "GroupsCreated",
    "GroupsDeleted",
    "GroupsUpdated",
    "SettingsUpdated",
    "SiteCreated",
    "SiteDeleted",
    "SiteUpdated",
    "TenantAliasUpdated",
    "TenantCreated",
    "TenantLogin",
    "UserActivated",
    "UserDeactivated",
    "VendorActivated",
    "VendorDeactivated",
    "VendorUpdated",
    "UserDeleteFromTenant",
    "VendorDeleteFromTenant",
    "UserJoinTenant",
    "VendorJoinTenant",
    "UserCreated",
    "UserUpdated",
    "UserRoleChanged",
    "ApplicationVendorLogin",
    "AppCertificateCreated",
    "AppCertificateDeleted",
    "AppCertificateUpdated",
    "CompanyUserInvitationCreate",
    "VendorInvitationCreate",
    "ServiceAccountCreated",
    "ServiceAccountDeleted",
    "ServiceAccountActivated",
    "ServiceAccountDeactivated",
    "ApplicationLoginBlocked",
    "DirectAccessUserResponse",
    "DirectAccessConnectionDenied",
    "OfflineAccessUserViewedPassword",
    "IdaptiveVendorSync",
    "IdaptiveRoleSync",
    "CompanyInviterUpdated",
]


def as_list(value: object) -> list[str] | None:
    """`idaptiveRoles`/`groups` vienen como string separado por comas en la
    API real de Alero cuando hay varios (confirmado contra Globex,
    2026-09-21: nunca como lista) -- normaliza a `list[str] | None` sin
    importar qué forma mande la API. Compartido entre `app.api.alero` (las
    filas de vendors/usuarios) y `app.reports.license_candidates` (el
    enriquecimiento con Alero)."""
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class AleroError(Exception):
    """Base de los errores del cliente Alero."""


class AleroAuthError(AleroError):
    pass


class AleroNotFound(AleroError):
    pass


class AleroClient:
    def __init__(
        self,
        *,
        tenant_id: str,
        service_account_id: str,
        private_key_pem: str,
        discovery_uri: str,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._tenant_id = tenant_id
        self._service_account_id = service_account_id
        self._private_key_pem = private_key_pem
        self._discovery_uri = discovery_uri
        self._http = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )
        self._token: str | None = None
        self._token_endpoint: str | None = None
        self._issuer: str | None = None

    async def __aenter__(self) -> AleroClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def _client_identity(self) -> str:
        # Por doc de Alero: iss/sub deben ser "<TenantID>.<ServiceAccountID>
        # .ExternalServiceAccount", NO el service_account_id solo (401
        # invalid_client). El form no lleva client_id.
        return f"{self._tenant_id}.{self._service_account_id}.ExternalServiceAccount"

    async def _discover(self) -> None:
        if self._token_endpoint and self._issuer:
            return
        try:
            resp = await self._http.get(self._discovery_uri)
            resp.raise_for_status()
            doc = resp.json()
        except httpx.HTTPError as e:
            raise AleroAuthError(f"no se pudo leer el discovery document de Alero: {e}")
        self._token_endpoint = doc.get("token_endpoint")
        self._issuer = doc.get("issuer")
        if not self._token_endpoint or not self._issuer:
            raise AleroAuthError("el discovery document de Alero no trae token_endpoint/issuer")

    async def _authenticate(self) -> None:
        await self._discover()
        if not self._token_endpoint or not self._issuer:
            raise AleroAuthError("el discovery document de Alero no trae token_endpoint/issuer")
        token_endpoint = self._token_endpoint
        issuer = self._issuer
        now = int(time.time())
        claims = {
            "iss": self._client_identity,
            "sub": self._client_identity,
            "aud": issuer,  # el issuer del discovery, no el token_endpoint (ver docstring del módulo)
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 60,
        }
        try:
            assertion = jwt.encode(claims, self._private_key_pem, algorithm="RS256")
        except (ValueError, jwt.InvalidKeyError) as e:
            raise AleroAuthError(f"clave privada de Alero inválida: {e}")

        try:
            resp = await self._http.post(
                token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": assertion,
                },
            )
        except httpx.HTTPError as e:
            raise AleroAuthError(f"no se pudo contactar el token endpoint de Alero: {e}")

        if resp.status_code != 200:
            raise AleroAuthError(
                f"autenticación contra Alero falló: {resp.status_code} {resp.text[:200]}"
            )
        token = resp.json().get("access_token")
        if not token:
            raise AleroAuthError("la respuesta de Alero no contiene access_token")
        self._token = token

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._token is None:
            await self._authenticate()

        url = f"{_API_BASE}{path}"
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                resp = await self._http.get(
                    url, params=params, headers={"Authorization": f"Bearer {self._token}"}
                )
            except httpx.TransportError as e:
                last_err = e
                await asyncio.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 401 and attempt == 0:
                await self._authenticate()
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt < 3:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 404:
                raise AleroNotFound(f"GET {path} -> 404")
            if resp.status_code >= 400:
                raise AleroError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
            return resp.json()

        raise AleroError(f"GET {path} -> agotó reintentos ({last_err})")

    async def list_vendors(self) -> list[dict[str, Any]]:
        vendors: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = await self._get(
                "/v2-edge/vendors", params={"offset": offset, "limit": _PAGE_SIZE}
            )
            batch = data.get("vendors") or []
            vendors.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return vendors

    async def get_vendor_detail(self, vendor_id: str) -> dict[str, Any]:
        """Perfil completo de un vendor (`GET /v2-edge/vendors/{vendorId}`,
        confirmado en el swagger completo `/v3/api-docs`) -- trae bastante
        más que `list_vendors()`: nombre/apellido, teléfono, `applications`
        (pares siteId/applicationId, sin resolver -- ver `list_sites`/
        `list_site_applications`), `groups` con nombre y tipo (a diferencia
        de `idaptiveRoles` en la lista, que viene como string plano),
        fechas de acceso, quién invitó, etc."""
        try:
            return await self._get(f"/v2-edge/vendors/{vendor_id}")
        except AleroNotFound:
            raise AleroNotFound(f"vendor '{vendor_id}' no encontrado en Alero")

    async def list_users(self) -> list[dict[str, Any]]:
        """Usuarios de la app móvil de Remote Access (Identities > Users en
        el portal de Alero) -- distinto de `list_vendors()` (Identities >
        Vendors). Confirmado en el swagger completo (`/v3/api-docs`, no
        publicado en `/swagger-ui`): `GET /v2-edge/users/` (con barra final
        -- sin ella da 404), paginado igual que vendors."""
        users: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = await self._get(
                "/v2-edge/users/", params={"offset": offset, "limit": _PAGE_SIZE}
            )
            batch = data.get("users") or []
            users.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return users

    async def list_groups(self) -> list[dict[str, Any]]:
        """ "Identity roles" en el portal de Alero (Identities > Identity
        roles) -- en la API se llaman "groups", de dos tipos: `IdaptiveRole`
        (roles importados de CyberArk Identity, lo que muestra esa pantalla
        del portal) y `ProvisionedByAlero` (grupos nativos de Alero, que esa
        pantalla NO muestra). Devuelve ambos tipos sin filtrar -- el
        filtro a `IdaptiveRole` lo hace el caller (`app.api.alero`), para
        que el cliente quede genérico. Confirmado contra Globex real
        (2026-09-21): 96 `IdaptiveRole` + 89 `ProvisionedByAlero` = 185,
        coincide con el "96 roles" que muestra el portal en esa pantalla."""
        groups: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = await self._get(
                "/v2-edge/groups", params={"offset": offset, "limit": _PAGE_SIZE}
            )
            batch = data.get("groups") or []
            groups.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return groups

    async def get_user_detail(self, user_id: str) -> dict[str, Any]:
        """Perfil completo de un usuario (`GET /v2-edge/users/{userId}`) --
        superset modesto de `list_users()`: separa firstName/lastName (la
        lista trae `fullname` junto), suma teléfono/modelo/SO y el id de
        quien lo invitó."""
        try:
            return await self._get(f"/v2-edge/users/{user_id}")
        except AleroNotFound:
            raise AleroNotFound(f"user '{user_id}' no encontrado en Alero")

    async def list_sites(self) -> list[dict[str, Any]]:
        """Sites de Remote Access (Sites en el nav del portal) -- para
        resolver los `siteId` que trae `applications` en el detalle de un
        vendor a un nombre legible (`displayName`)."""
        sites: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = await self._get("/v2-edge/sites", params={"offset": offset, "limit": _PAGE_SIZE})
            batch = data.get("sites") or []
            sites.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return sites

    async def list_site_applications(self, site_id: str) -> list[dict[str, Any]]:
        """Aplicaciones de un site -- para resolver el `applicationId` que
        trae `applications` en el detalle de un vendor a un nombre legible
        (`name`)."""
        apps: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = await self._get(
                f"/v2-edge/sites/{site_id}/applications",
                params={"offset": offset, "limit": _PAGE_SIZE},
            )
            batch = data.get("applications") or []
            apps.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return apps

    async def list_activities(
        self,
        *,
        from_time_ms: int,
        to_time_ms: int,
        activity_types: list[str] | None = None,
        max_results: int = 500,
    ) -> list[dict[str, Any]]:
        """Feed "Activities" del portal (login, altas/bajas, cambios de
        config, etc.) -- `GET /v2-edge/activities`, `activityTypes` es
        obligatorio (se manda `ALL_ACTIVITY_TYPES` por default). `httpx`
        repite la clave para parámetros de query en lista
        (`activityTypes=A&activityTypes=B`), formato que la API acepta.
        `max_results` corta la paginación (no trae todo el historial de
        una, para no volver una corrida costosa si el rango es amplio)."""
        types = activity_types or ALL_ACTIVITY_TYPES
        activities: list[dict[str, Any]] = []
        offset = 0
        while len(activities) < max_results:
            data = await self._get(
                "/v2-edge/activities",
                params={
                    "activityTypes": types,
                    "fromTime": from_time_ms,
                    "toTime": to_time_ms,
                    "offset": offset,
                    "limit": _ACTIVITIES_PAGE_SIZE,
                },
            )
            batch = data.get("activities") or []
            activities.extend(batch)
            if len(batch) < _ACTIVITIES_PAGE_SIZE:
                break
            offset += _ACTIVITIES_PAGE_SIZE
        return activities[:max_results]

    async def _login_windows(self, lookback_years: int) -> AsyncIterator[list[dict[str, Any]]]:
        """Generador interno: eventos `ApplicationVendorLogin` en ventanas de
        30 días, de la más reciente a la más vieja, hasta `lookback_years`
        atrás -- compartido por `get_vendor_last_login` (corta en la
        primera ventana con match de UN vendor) y
        `get_all_vendor_last_logins` (recorre todo, para TODOS los
        vendors). Mismo troceo que `Get-AleroVendorLastLogins` de
        `Alero-ApiClient.ps1`: la API pagina `activities` hasta
        offset=10000 y corta, así que pedir todo el rango de lookback de
        una sola vez perdería justo los eventos más RECIENTES si hay más
        de 10000 en el rango completo."""
        chunk_end = int(time.time() * 1000)
        overall_from = chunk_end - lookback_years * 365 * 24 * 3600_000

        while chunk_end > overall_from:
            chunk_start = max(chunk_end - _LAST_LOGIN_CHUNK_MS, overall_from)
            try:
                yield await self.list_activities(
                    from_time_ms=chunk_start,
                    to_time_ms=chunk_end,
                    activity_types=["ApplicationVendorLogin"],
                    max_results=10_000,
                )
            except AleroError:
                # ventana puntual demasiado grande / falla transitoria --
                # best-effort, se sigue con la ventana anterior (mismo
                # criterio que el script de PowerShell: no descartar todo
                # el historial por una ventana puntual).
                yield []
            chunk_end = chunk_start

    async def get_vendor_last_login(
        self, username: str, *, lookback_years: int = _LAST_LOGIN_LOOKBACK_YEARS
    ) -> int | None:
        """Último login REAL de un vendor puntual, calculado desde el feed
        de `activities` (`ApplicationVendorLogin`) -- distinto del campo
        `lastLogin` que trae `GET /v2-edge/vendors/{id}` (nunca confirmado
        como confiable contra un tenant real, a diferencia de los demás
        campos de `VendorResponse` -- ver docs/specs/done/alero-vendors.md).
        Se corta apenas una ventana da algún match -- una ventana más vieja
        nunca puede tener un login más reciente que uno ya encontrado.
        `applicationLoginUsername` no es consistente entre eventos (a
        veces trae sufijo de dominio, a veces no) -- se compara por la
        parte local del username, igual que el script de PowerShell."""
        local_part = username.strip().lower().split("@")[0]
        if not local_part:
            return None

        async for activities in self._login_windows(lookback_years):
            best: int | None = None
            for a in activities:
                data = a.get("activityData") or {}
                raw_username = data.get("applicationLoginUsername")
                t = data.get("time")
                if not raw_username or not t:
                    continue
                if str(raw_username).lower().split("@")[0] != local_part:
                    continue
                if best is None or t > best:
                    best = t
            if best is not None:
                return best

        return None

    async def get_all_vendor_last_logins(
        self, *, lookback_years: int = _LAST_LOGIN_LOOKBACK_YEARS
    ) -> dict[str, int]:
        """Mapa `username (parte local) -> epoch ms del login MÁS RECIENTE`,
        para TODOS los vendors de una vez -- a diferencia de
        `get_vendor_last_login` (un vendor puntual, corta en la primera
        ventana con match), acá hay que recorrer TODO el rango de lookback
        sin cortar: cualquier ventana puede tener el login más reciente de
        un vendor que las ventanas más nuevas no mencionan. Puerto directo
        de `Get-AleroVendorLastLogins` de `Alero-ApiClient.ps1`; lo usa el
        enriquecimiento del reporte `license-candidates`
        (`app.reports.license_candidates`), que necesita el último login de
        todos los vendors ExtUser de una — llamar a `get_vendor_last_login`
        uno por uno sería demasiado costoso."""
        last_logins: dict[str, int] = {}
        async for activities in self._login_windows(lookback_years):
            for a in activities:
                data = a.get("activityData") or {}
                raw_username = data.get("applicationLoginUsername")
                t = data.get("time")
                if not raw_username or not t:
                    continue
                local_part = str(raw_username).lower().split("@")[0]
                if local_part not in last_logins or t > last_logins[local_part]:
                    last_logins[local_part] = t
        return last_logins
