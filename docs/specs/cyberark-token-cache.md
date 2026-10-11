# Caché del token OAuth2 de CyberArk por tenant

- **Estado:** hecha (pendiente medir en el server)
- **Roadmap:** rendimiento transversal (revisión de mejoras 2026-09-28)
- **Fecha:** 2026-09-28

## Problema
Cada request de PAM Web Console que toca un tenant (`tenant_client`, búsqueda
cross-tenant, System Health, jobs) hacía un login OAuth2 completo contra
`{identity}/oauth2/platformtoken` antes de la llamada real a CyberArk: una
ida y vuelta extra por request (y N por cada fan-out cross-tenant).

## Alcance
Entra: caché en memoria, por proceso, del bearer token de ISPSS. Fuera:
Self-Hosted (su `close()` hace Logoff y cierra la sesión del Vault — no se
cachea), Alero (auth propia, otro módulo), caché compartida entre procesos
(Redis — no hace falta con un solo proceso uvicorn + un worker).

## Requisitos
1. Dado un token cacheado vigente para las MISMAS credenciales (URL de
   Identity + client_id + hash del client_secret), cuando llega otro
   request al mismo tenant, entonces no se llama a `/oauth2/platformtoken`.
2. Credenciales distintas nunca comparten token (aislamiento por tenant);
   rotar el `client_secret` genera una clave nueva de caché.
3. TTL = mínimo entre `PAM_CYBERARK_TOKEN_CACHE_SECONDS` (600) y
   `expires_in` real − 60 s. `0` desactiva la caché.
4. Ante un 401 de CyberArk, se pide un token nuevo (`force=True`) y
   reemplaza al cacheado.

## Diseño técnico
- `app/cyberark/auth.py`: `_TOKEN_CACHE` de módulo, `ISPSSAuth(...,
  cache_max_seconds)`, `login(client, force=False)`.
- `CyberArkClient.authenticate(force=False)`; el re-auth ante 401 usa
  `force=True`.
- `build_client(..., token_cache_seconds)`; los 5 llamadores pasan
  `get_settings().cyberark_token_cache_seconds`. El cliente HTTP sigue
  siendo uno nuevo por request — sólo se reusa el string del token.
- Tests: `tests/unit/test_cyberark_token_cache.py` (httpx `MockTransport`).

## Tareas
- [x] Caché + invalidación ante 401
- [x] Tests
- [ ] Medir en el server la latencia de una página de inventario antes/después
