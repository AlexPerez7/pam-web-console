"""
Estrategias de autenticación para la API de PVWA. Portadas (async) de
`cyberark_api.py` de Telemetry-Reporter.

- ISPSSAuth  -> Privilege Cloud / ISPSS: OAuth2 client_credentials contra
  Identity. Header 'Authorization: Bearer <token>'.
- SelfHostedAuth -> PAM Self-Hosted: logon usuario/contraseña contra el PVWA.
  El token va CRUDO como Authorization (sin 'Bearer '). Parkeado en v1 pero
  se mantiene para no rehacerlo cuando se desparkee.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from typing import ClassVar

import httpx

from app.cyberark.errors import AuthError

# Caché en memoria (por proceso) de tokens OAuth2 de ISPSS. Antes cada request
# de PAM Web Console hacía un login completo contra Identity antes de pegarle a
# CyberArk. La clave incluye URL de Identity + client_id + hash del secret,
# así que cada tenant (credenciales propias) tiene su propia entrada y rotar
# el secret invalida la anterior sola -- el aislamiento por tenant se
# mantiene: nunca se comparte un token entre credenciales distintas. El
# cliente HTTP sigue siendo uno NUEVO por request. Ver
# docs/specs/cyberark-token-cache.md.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
# margen antes del vencimiento real para no usar un token a punto de expirar
_EXPIRY_MARGIN_SECONDS = 60


def clear_token_cache() -> None:
    _TOKEN_CACHE.clear()


class ISPSSAuth:
    def __init__(
        self,
        identity_tenant_url: str,
        client_id: str,
        client_secret: str,
        *,
        cache_max_seconds: int = 0,
    ):
        self.identity_tenant_url = identity_tenant_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        # 0 = sin caché (comportamiento original)
        self.cache_max_seconds = cache_max_seconds

    def _cache_key(self) -> str:
        secret_hash = hashlib.sha256(self.client_secret.encode()).hexdigest()
        return f"{self.identity_tenant_url}|{self.client_id}|{secret_hash}"

    async def login(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        """`force=True` ignora (y reemplaza) el token cacheado -- lo usa el
        re-auth ante un 401, por si el token se revocó antes de vencer."""
        key = self._cache_key()
        if self.cache_max_seconds > 0 and not force:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[1] > time.monotonic():
                client.headers["Authorization"] = f"Bearer {cached[0]}"
                return cached[0]
        _TOKEN_CACHE.pop(key, None)
        token, expires_in = await self._fetch_token(client)
        if self.cache_max_seconds > 0:
            ttl = min(self.cache_max_seconds, expires_in - _EXPIRY_MARGIN_SECONDS)
            if ttl > 0:
                _TOKEN_CACHE[key] = (token, time.monotonic() + ttl)
        client.headers["Authorization"] = f"Bearer {token}"
        return token

    async def _fetch_token(self, client: httpx.AsyncClient) -> tuple[str, int]:
        url = f"{self.identity_tenant_url}/oauth2/platformtoken"
        resp = await client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"ISPSS auth falló ({resp.status_code}): {resp.text[:300]}")
        try:
            body = resp.json()
        except ValueError:
            raise AuthError(f"respuesta de token no-JSON: {resp.text[:200]}")
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise AuthError(f"respuesta de token sin access_token: {resp.text[:200]}")
        try:
            expires_in = int(body.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        return token, expires_in

    async def refresh(self, client: httpx.AsyncClient) -> str:
        return await self.login(client, force=True)

    async def logout(self, client: httpx.AsyncClient) -> None:
        pass  # el token de ISPSS expira solo (y puede seguir cacheado)


class SelfHostedAuth:
    _SEG: ClassVar[dict[str, str]] = {
        "cyberark": "CyberArk",
        "ldap": "LDAP",
        "radius": "RADIUS",
        "windows": "Windows",
    }

    def __init__(
        self,
        pvwa_url: str,
        method: str,
        username: str,
        password: str,
        concurrent_session: bool = True,
    ):
        self.pvwa_url = pvwa_url.rstrip("/")
        self.method = (method or "cyberark").strip().lower()
        if self.method not in self._SEG:
            raise AuthError(f"auth_method '{method}' no soportado")
        self.username = username
        self.password = password
        self.concurrent_session = concurrent_session

    def _logon_url(self) -> str:
        return f"{self.pvwa_url}/PasswordVault/API/auth/{self._SEG[self.method]}/Logon"

    async def login(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        # sin caché: el logoff de close() invalida la sesión del Vault
        resp = await client.post(
            self._logon_url(),
            json={
                "username": self.username,
                "password": self.password,
                "concurrentSession": bool(self.concurrent_session),
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"Self-Hosted logon falló ({resp.status_code}): {resp.text[:300]}")
        try:
            token = resp.json()
        except ValueError:
            raise AuthError(f"respuesta de logon no-JSON: {resp.text[:200]}")
        if not isinstance(token, str) or not token:
            raise AuthError(f"respuesta de logon inesperada: {str(token)[:200]}")
        client.headers["Authorization"] = token
        return token

    async def refresh(self, client: httpx.AsyncClient) -> str:
        return await self.login(client)

    async def logout(self, client: httpx.AsyncClient) -> None:
        with contextlib.suppress(httpx.HTTPError):
            await client.post(f"{self.pvwa_url}/PasswordVault/API/auth/Logoff", timeout=15)
