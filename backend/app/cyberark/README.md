# app/cyberark — Cliente CyberArk unificado

Aísla al resto de PAM Web Console de las diferencias Privilege Cloud / Self-Hosted y de
las asperezas de la API REST de PVWA.

## Origen

Evolución de `cyberark_api.py` del proyecto `Telemetry-Reporter`
(ITR), que ya resuelve:

- Estrategias de auth pluggables: `ISPSSAuth` (OAuth2 client_credentials
  contra Identity, header `Bearer`) y `SelfHostedAuth` (logon
  usuario/contraseña contra el PVWA, token crudo sin `Bearer`, Logoff).
- Retry con backoff ante 429 y 5xx transitorios; re-auth ante 401.
- Paginación (offset/limit y cursor).
- Normalización de errores a excepciones tipadas.
- Preflight de versión (Self-Hosted) y degradación elegante cuando falta un
  endpoint o un permiso.

Ver `Telemetry-Reporter/docs/soporte-self-hosted.md` y su
`cyberark_api.py`.

## Qué agrega PAM Web Console

- Cobertura de **escritura** (Add/Update/Delete de accounts, safes, members,
  platforms, users; acciones de CPM; terminar sesiones; requests).
- Modo `dry_run`: valida y arma el diff sin ejecutar.
- Límite de concurrencia por tenant (un PVWA on-prem aguanta menos que el
  SaaS).
- Tests de contrato contra el tenant de laboratorio.
- Cliente async (httpx) para orquestar muchas llamadas en paralelo.
