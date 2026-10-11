# Aprobación de un segundo operador (four-eyes)

- **Estado:** hecha, prueba en backlog (2026-10-07, decisión de Alex: el
  equipo PAM no busca usarla hoy; todos los operadores son `platform_admin`,
  que ejecuta directo, así que el flujo no se activa). El código queda;
  se prueba si aparece un operador que no sea `platform_admin`.
- **Roadmap:** Fase 3, principio no negociable 4 ("aprobación de un segundo
  operador para operaciones destructivas")
- **Fecha:** 2026-09-28

## Problema
Hoy las operaciones de alto riesgo (borrar cuenta/safe/plataforma, quitar
o editar miembro de safe, `safe.update`, `platform.psm.update`,
`platform.activate`) dependen de un solo operador: rol `approver+` +
confirmación reforzada + razón. El principio 4 de `CLAUDE.md` pide que un
SEGUNDO operador apruebe. `docs/PLAN.md` lo dejó como gap "mientras Alex sea
el único"; con más operadores en el equipo, ya es viable.

## Alcance
Entra (v1):
- Las operaciones que pasan por `writeop.run_write` y tienen
  `Plan.confirm_phrase` (las de Fase 3). Es un criterio que ya existe en el
  código: "requiere confirmación reforzada" = "requiere segundo operador".
- Cola de solicitudes pendientes, aprobar/rechazar/cancelar, vencimiento.

Queda afuera (v1), cada uno con su motivo:
- `account.move`/`account.move.bulk`: ya exigen `platform_admin`, que no
  necesita segundo operador (ver Decisiones).
- `account.reveal`/`account.reveal.bulk`/`recording.play` (no son
  escrituras; revelar ES la acción — un flujo de aprobación de acceso tipo
  "dual control" de CyberArk es otra spec).
- Notificaciones a Slack/Teams: spec aparte.

## Decisiones (Alex, 2026-09-28)
- **`platform_admin` (rol de plataforma) ejecuta directo**, sin segundo
  operador. Todos los demás roles dejan una solicitud pendiente.
- Como un `platform_admin` siempre puede aprobar, **no hay modo de respaldo**
  "un solo aprobador": la solicitud siempre se crea. Interruptor global
  `PAM_DUAL_APPROVAL_ENABLED` (default `true`).
- Vencimiento: 24 h (`PAM_APPROVAL_TTL_HOURS`).
- `account.move` queda fuera sin más: ya exige `platform_admin`.
- Aprueba cualquier operador distinto del solicitante con rol efectivo
  `approver+` en el tenant (incluye `platform_admin`).

## Requisitos
1. **Solicitar.** Dado un operador que NO es `platform_admin` y ejecuta
   (`dry_run=false`) una operación de Fase 3, cuando la confirmación y la
   razón son válidas, entonces NO se toca CyberArk: se crea una
   `ApprovalRequest` `pending` y se responde `200 {"pending_approval": true,
   "approval_id"}`. Audit: `<feature>` con `result="pending_approval"`. El
   dry-run previo devuelve `requires_approval: true` (la UI muestra
   "Solicitar aprobación").
2. **Aprobar = ejecutar.** Otro operador re-envía el mismo request con el
   header `X-PAM-Approval-Id`: `run_write` valida la solicitud, recalcula el
   plan contra el estado ACTUAL y, si el digest coincide, ejecuta. Audit:
   `<feature>` a nombre del aprobador con `detail.requested_by`,
   `request_reason`, `approved_by`, `approval_id`.
3. **Deriva.** Si el objeto cambió (el digest del plan no coincide) → 409 y
   la solicitud pasa a `stale`. En el dry-run del aprobador,
   `approval_matches: false` y la UI deshabilita "Aprobar".
4. **Autoaprobación prohibida** (403), ni siquiera para `platform_admin`.
5. **Mismo request.** Método, path, feature, target y `changes` deben ser
   idénticos a los solicitados (409 si no).
6. **Rechazar / cancelar.** Rechaza otro operador elegible, con motivo
   (≥ 8 caracteres); cancela sólo el solicitante. Auditados
   `approval.reject` / `approval.cancel`.
7. **Vencimiento.** Sin resolver a las 24 h → `expired`; aprobarla da 409.
8. **Visibilidad.** `GET /approvals` muestra las propias y las de los
   tenants donde el operador es `approver+`; `GET /approvals/count` = las
   que ESTE operador puede aprobar (badge del sidebar).

## Diseño técnico
- **Por qué "re-ejecutar" y no "guardar y ejecutar después":** `run_write`
  recibe closures (`fetch`/`build_plan`/`apply`) que no se pueden
  serializar. Se guarda el request original (método, path, body sin
  `dry_run`/`reason`/`confirm`) y el aprobador lo re-envía: corren de nuevo
  todos los chequeos (RBAC del endpoint, rate-limit, circuit breaker,
  confirmación) contra el estado actual. La aprobación es un gate más en
  `run_write` (`app/core/approvals.py`), sin tocar ninguna de las 12
  operaciones de Fase 3 ni sus modelos de body.
- **Modelo** `ApprovalRequest` (migración `0017`, `approval_requests`).
  `plan_digest` = sha256 de `diff`+`patch`+`effect` canónicos.
- **Router** `app/api/approvals.py`: `GET /approvals?status=pending|all`,
  `GET /approvals/count`, `GET /approvals/{id}`, `POST .../reject`,
  `POST .../cancel`.
- **Frontend:** `DryRunDialog` (props `approvalId`/`requestedBy`; botón
  "Solicitar aprobación" / "Aprobar y ejecutar"; aviso de deriva), página
  `/approvals` (espacio Operación, badge con el contador).
- **Tests:** `tests/unit/test_dual_approval.py` (12). El resto de la suite
  corre con `PAM_DUAL_APPROVAL_ENABLED=false` (`tests/conftest.py`): esos
  tests prueban cada operación, no la aprobación.

## Tareas
- [x] Decisiones
- [x] Modelo + migración `0017`
- [x] Gate en `run_write`
- [x] Router `approvals.py`
- [x] Frontend: `DryRunDialog`, página `/approvals`, badge
- [x] Tests
- [ ] Probar en el server con dos operadores reales contra el tenant lab
