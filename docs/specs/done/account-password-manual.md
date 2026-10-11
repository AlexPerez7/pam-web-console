# account-password-manual — fijar un valor de contraseña específico

- **Estado:** hecha (implementada 2026-09-14, pendiente probar contra un tenant real)
- **Roadmap:** `docs/PLAN.md §7` Fase 3 — extra a pedido de Alex
- **Fecha:** 2026-09-14

## Problema

Al hacer clic en "Change" en el PVWA, CyberArk ofrece 3 opciones — sólo una
estaba implementada en PAM Web Console:

1. **Cambiar sólo en la Vault** — cuando la contraseña real ya se cambió en
   el target por fuera de CyberArk (a mano, por otro proceso) y hay que
   sincronizar lo que la Vault tiene guardado.
2. **Especificar la contraseña para la próxima rotación automática del
   CPM** — el operador fija el valor, pero no pasa nada hasta el próximo
   ciclo de cambio del CPM.
3. **Cambiar ahora con el CPM** (✅ ya implementado como `account.change`)
   — el CPM genera un valor aleatorio según la política de la plataforma y
   lo aplica ya mismo en el target.

Faltaban 1 y 2 — casos reales de operación (ej. alguien cambió la
contraseña a mano en una emergencia y hay que avisarle a CyberArk).

## Alcance

**Entra:**
- `POST /tenants/{id}/accounts/{id}/change-in-vault` — "Change credentials
  in the Vault": `client.update_password_in_vault` →
  `POST /API/Accounts/{id}/Password/Update {"NewCredentials": "..."}`.
  Nunca llama al target.
- `POST /tenants/{id}/accounts/{id}/set-next-password` — "Set next
  password": `client.set_next_password` →
  `POST /API/Accounts/{id}/SetNextPassword {"NewCredentials": "...",
  "ChangeImmediately": false}`. No toca nada ahora; el CPM usa ese valor en
  su próximo cambio automático.
- Feature flags propias (`account.change.vault_only` /
  `account.change.next`), opt-in, apagadas por defecto.
- RBAC `tenant_admin+` — **más alto que `account.change`/`operator+`** (que
  dispara al CPM con un valor aleatorio que el operador nunca ve): acá el
  operador escribe/conoce un valor de contraseña real que va a quedar
  vigente. Mismo criterio que `account.reveal`.
- `reason` obligatoria. El valor de la contraseña nunca aparece en el
  diff/audit (mismo patrón que el secreto de `account.create`).
- Botón "Definir contraseña" en el detalle de cuenta (`AccountPasswordSection.tsx`),
  con un input de contraseña antes de la previsualización.

**Fuera:**
- `ChangeImmediately: true` en `SetNextPassword` (aplicar el valor
  especificado YA, en vez de esperar al próximo ciclo) — no fue parte del
  pedido; si hace falta más adelante, es un tercer modo del mismo
  endpoint, no uno nuevo.
- `ChangeEntireGroup` (aplicar a todo un grupo de cuentas vinculadas) — no
  pedido, v1 es cuenta por cuenta.

## Verificación de la API (importante)

Los endpoints exactos y sus cuerpos se confirmaron con DOS fuentes, no sólo
la documentación pública de CyberArk (que bloquea el fetch automatizado
usado para esta investigación):

1. **`Safe_Password_Reset.ps1`** (`PowerShell-Scripts`, script
   ya probado en producción de Alex) — usa exactamente
   `POST /API/Accounts/{id}/Password/Update` con `{"NewCredentials": "..."}"`
   para "pisar" credenciales sólo en la Vault, con el mismo comentario:
   "NO modifica la contraseña real en los servidores/dispositivos de
   destino... no hay Change Immediately".
2. **Código fuente de `Invoke-PASCPMOperation.ps1`** del módulo open-source
   `psPAS` (github.com/pspete/psPAS, wrapper mantenido activamente de esta
   misma API) — confirma los 3 `ParameterSetName` → URI:
   `Change` → `/Accounts/{id}/Change` (ya implementado),
   `Password/Update` → `/Accounts/{id}/Password/Update` (con
   `NewCredentials`, sin `ChangeImmediately`),
   `SetNextPassword` → `/Accounts/{id}/SetNextPassword` (con
   `NewCredentials` + `ChangeImmediately`).

## Requisitos

1. **Change-in-vault, dry-run.** No llama a CyberArk, no expone el valor en
   la respuesta.
2. **Change-in-vault, ejecución.** Con `reason` válida, llama a
   `update_password_in_vault(account_id, new_password)`. La respuesta y el
   audit NO contienen el valor.
3. **Set-next-password, ejecución.** Llama a
   `set_next_password(account_id, new_password)` con
   `ChangeImmediately=False`. Mismo criterio de no-exposición.
4. **RBAC insuficiente.** Un `approver` (no `tenant_admin+`) recibe `403`
   en ambos endpoints.
5. **Feature apagada.** `403` antes de tocar CyberArk.
6. **`new_password` vacío.** `422` (`Field(min_length=1)`).

## Diseño técnico

### Backend

- `app/cyberark/client.py`: `update_password_in_vault` / `set_next_password`
  — ambos `retry_safe=False` (evitar duplicar el evento nativo de audit
  ante un reintento).
- `app/domain/accounts.py`: `plan_password_vault_only` /
  `plan_password_set_next` — diff siempre `"(definida)"`, nunca el valor
  real; `plan_password_vault_only` agrega un warning explícito sobre el
  riesgo de desincronización con el target.
- `app/api/writes.py`: dos endpoints nuevos, mismo patrón `fetch`/`apply`
  + `writeop.run_write` que el resto de Fase 2/3, con
  `require_tenant_access(OperatorRole.tenant_admin)`.

### Frontend

- `src/components/AccountPasswordSection.tsx`: sección nueva en el
  detalle de cuenta (distinta de `AccountActions`, que sólo dispara
  Change/Verify/Reconcile sin input) — botones "Cambiar sólo en la Vault"
  / "Especificar próxima contraseña" → input de contraseña →
  `<DryRunDialog>`.

## Tareas

- [x] `app/cyberark/client.py`: `update_password_in_vault`,
      `set_next_password`.
- [x] `app/domain/accounts.py`: `plan_password_vault_only`,
      `plan_password_set_next`.
- [x] `app/core/features.py`: `account.change.vault_only`,
      `account.change.next`.
- [x] `app/api/writes.py`: `POST .../change-in-vault`,
      `POST .../set-next-password`.
- [x] `pages/Tenants.tsx`: las 2 features nuevas en `FEATURES`.
- [x] `src/components/AccountPasswordSection.tsx` + wiring en
      `DetailDrawer.tsx`.
- [x] Tests unit: dry-run, ejecución, RBAC, feature apagada, campo
      obligatorio, valor nunca expuesto.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [ ] Probar contra Globex antes de cerrar del todo — en particular
      confirmar `SetNextPassword` con `ChangeImmediately: false` (no hay
      forma de verificarlo sin un ciclo real de rotación del CPM).
