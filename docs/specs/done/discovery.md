# discovery

- **Estado:** hecha
- **Roadmap:** Fase 4, "Discovery integration"
- **Fecha:** 2026-09-22 (primer corte) / 2026-09-22 (reescritura contra la doc oficial)

## Problema

CyberArk Discovery detecta cuentas privilegiadas en la red del cliente que
todavía no están onboardeadas al Vault ("Discovered Accounts", visible en
el PVWA bajo Pending Accounts). PAM Web Console no tenía forma de listarlas ni de
onboardearlas sin salir a la consola nativa.

## Alcance

Sólo la API Gen2 de Discovery (Privilege Cloud/ISPSS — mismo alcance v1
que el resto de PAM Web Console). Quedan afuera de este corte (APIs reales,
documentadas, pero sin pedido concreto todavía):

- `POST /discovered-accounts/{id}/add` (alta manual de una cuenta
  descubierta, sin pasar por un scan).
- `PUT .../edit-properties` (editar `customProperties`/`tags` de una
  cuenta descubierta sin onboardearla).
- `DELETE /discovered-accounts` (borrar TODAS las descubiertas de una).
- `POST .../check-existence` (chequeo masivo de existencia, pensado para
  herramientas de ingesta, no para un operador).
- `PUT .../tags` (asociar/desasociar tags sueltos).
- Discovery scans (`discovery-scan-definitions.htm`) y Remediation rule
  sets (`account-discovery-rules.htm`) — configuración de CÓMO se
  descubren/remedían cuentas, no gestión de lo ya descubierto.

## Historia real (por qué se reescribió)

El primer corte (`fa4c3ef`, 2026-09-22) se armó sin confirmar la API
contra la documentación oficial:

- `GET /API/DiscoveredAccounts` (con `/PasswordVault`, estilo Gen1) como
  intento primario, con un fallback a `.../api/discovered-accounts` sin
  el prefix sólo si el primero devolvía 404. La API real (confirmada
  contra `docs.cyberark.com/manage/latest/en/content/discovery/
  discovery-apis/discovery-account-discovery.htm`) es directamente la
  segunda — Privilege Cloud/ISPSS no tiene un Gen1 de Discovery. Se sacó
  el intento Gen1 y el fallback.
- El parseo de la respuesta leía `res.get("value")`/`res.get("count")`
  — la forma real es `{"items": [...], "totalCount": N, "nextCursor",
  "prevCursor"}`. Con el parseo viejo, la grilla de Cuentas Descubiertas
  quedaba SIEMPRE vacía contra un tenant real (bug silencioso: el
  endpoint no fallaba, sólo devolvía listas vacías).
- El botón "Onboard" llamaba a `POST /tenants/{id}/writes/account.create`
  (ruta inexistente, 404 garantizado) con un body que tampoco coincidía
  con lo que espera ese endpoint (fix parcial en `7fb9939`, que lo hizo
  apuntar a `POST /tenants/{id}/accounts` — funcional, pero ese endpoint
  es el wizard de alta manual de `account.create`, no la API real de
  Discovery). La API real es `POST .../discovered-accounts/{id}/onboard`
  — un endpoint DEDICADO que CyberArk expone para exactamente este caso,
  con su propio contrato (`coreAttributes.{safeName,platformId}` +
  `secret`/`resetSecret`/`tags`/`additionalProperties` opcionales).
- El router declaraba su propio `prefix="/tenants/{tenant_id}"` Y
  `main.py` se lo volvía a pasar en `include_router()` — rompía el
  arranque entero (`ValueError: Duplicated param name`). Arreglado en
  `7fb9939`.

Esta revisión (2026-09-22, mismo día) reescribe la integración completa
contra la documentación oficial, agrega ignorar/restaurar/borrar/ver
actividad (API real, no implementada antes) y cubre todo con tests
unitarios (14 nuevos, `tests/unit/test_discovery.py`) — el primer corte
no tenía ninguno, por eso el bug del prefix duplicado llegó a `main`.

## Diseño técnico

**Backend.** `app/cyberark/client.py`: `CyberArkClient._discovery_url()`
devuelve el dominio base SIN `/PasswordVault` (mismo `pvwa_url`, sólo
cambia el path — a diferencia de Connector Management/SIA, acá no hace
falta una URL de tenant aparte). Métodos nuevos:
`list_discovered_accounts`, `get_discovered_account`,
`get_discovered_account_activities`, `onboard_discovered_account`,
`ignore_discovered_account`, `restore_discovered_account`,
`delete_discovered_account`.

`app/domain/discovery.py`: planes puros (`Plan.effect`, sin diff — mismo
patrón que `plan_cpm`/`plan_platform_import`) para onboard/ignore/
restore/delete. `plan_discovered_account_onboard` nunca lee `changes["secret"]`
— no llega al diff/audit, mismo criterio que `plan_account_create`.

`app/api/discovery.py`: `GET /discovered-accounts` y `GET
.../{id}/activities` son lecturas (`_run`, `viewer+`, auditadas). Onboard/
ignore/restore/delete son escrituras reales contra CyberArk → pasan por
`writeop.run_write` (reason + dry-run + rate-limit + audit before/after),
`operator+` (ninguna necesita confirmación reforzada: onboardear es
riesgo equivalente a `account.create`; ignorar/restaurar/borrar son
reversibles y no tocan cuentas ya onboardeadas). `changes` anidado en el
body de onboard (no campos sueltos) para poder manejarlo con el
`<DryRunDialog>` genérico del frontend, igual que `account.create`/
`safe.create`.

**Frontend.** `DiscoveredAccount` (en `lib/types.ts`) refleja la forma
real de la API — `identifiers`/`customProperties` son diccionarios, no
campos planos (`userName`/`address`/`osFamily` sueltos, como tenía el
primer corte). `pages/Discovery.tsx`: columnas armadas desde
`identifiers`/`customProperties`, badge de `lastActivityStatus`, menú de
acciones (Onboard visible sólo `operator+`; Ignorar/Restaurar/Borrar
detrás de un menú `···`, mismo criterio de "no saturar la fila" que el
resto de PAM Web Console). `DiscoveryOnboardDialog.tsx` reescrito para pegarle al
endpoint real vía `<DryRunDialog>` (preview con diff/effect real, razón
obligatoria, ya no salta directo a ejecutar). `DiscoveredAccountActivitiesDialog.tsx`
(nuevo) — feed de por qué una cuenta no se auto-remedió (rule set
matcheado, error, etc.), sólo lectura.

## Tareas

- [x] Confirmar la API real contra `docs.cyberark.com` (Discovery scans,
      Discovered accounts, Remediation rule sets, Return codes, Types of
      accounts).
- [x] `CyberArkClient`: métodos de Discovery contra el dominio base
      correcto.
- [x] `app/domain/discovery.py`: planes puros.
- [x] `app/api/discovery.py`: reescribir list (parseo `items`/
      `totalCount`), sumar activities/onboard/ignore/restore/delete vía
      `run_write`.
- [x] Frontend: `DiscoveredAccount` con la forma real, `Discovery.tsx`
      con columnas/acciones nuevas, `DiscoveryOnboardDialog.tsx` contra
      el endpoint real, `DiscoveredAccountActivitiesDialog.tsx`.
- [x] Tests unitarios (`tests/unit/test_discovery.py`, 14).
- [x] Backend (ruff+format+mypy+pytest) y frontend (lint+tsc+build)
      verdes.
- [ ] Probar contra un tenant real con Discovery configurado (ninguno de
      los 5 tenants de PAM Web Console lo tiene activo todavía).
