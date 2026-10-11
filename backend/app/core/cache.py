"""
Caché efímera sobre Redis para cálculos caros (dashboard por tenant,
System Health, etc.) y, desde 2026-09-18, opt-in para los datos crudos del
reporte ITR (`app.api.itr`, `ItrReportIn.use_cache`) — a diferencia del
resto de los usos, esa caché SÍ guarda metadata cruda de cuentas (nunca
secretos, `retrieve_password` no participa), pensada para iterar rápido
sobre cambios al template sin repegarle a CyberArk cada vez.

Tolerante a Redis caído: si no se puede conectar o falla una operación, se
loguea y se devuelve `None` / se ignora el `set` — el llamador recalcula.
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis

from app.config import get_settings

log = logging.getLogger("pam.cache")

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def get_json(key: str) -> dict | None:
    try:
        raw = await _redis().get(key)
    except Exception as e:  # Redis caído / red / etc. — degradar, no romper
        log.warning("cache get %s falló: %s", key, e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def set_json(key: str, value: dict, ttl: int) -> None:
    try:
        await _redis().set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as e:
        log.warning("cache set %s falló: %s", key, e)


async def get_int(key: str) -> int | None:
    """Valor entero de una key, o None si no existe o Redis no responde."""
    try:
        raw = await _redis().get(key)
    except Exception as e:
        log.warning("cache get_int %s falló: %s", key, e)
        return None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def incr_with_ttl(key: str, ttl: int) -> int | None:
    """Incrementa un contador; fija el TTL en el primer incremento. Devuelve el
    valor nuevo, o None si Redis no responde (el llamador degrada abierto)."""
    try:
        r = _redis()
        n = int(await r.incr(key))
        if n == 1:
            await r.expire(key, ttl)
        return n
    except Exception as e:
        log.warning("cache incr %s falló: %s", key, e)
        return None


async def delete(key: str) -> None:
    try:
        await _redis().delete(key)
    except Exception as e:
        log.warning("cache delete %s falló: %s", key, e)
