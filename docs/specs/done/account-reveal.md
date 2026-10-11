# account-reveal — ver/copiar la contraseña de una cuenta activa

- **Estado:** aprobada
- **Roadmap:** `docs/PLAN.md §6` (Accounts → "Recuperar secreto"), Fase 3
- **Fecha:** 2026-09-14

## Problema

El detalle de una cuenta en PAM Web Console no tiene forma de ver ni copiar la
contraseña vigente — la decisión original (`docs/PLAN.md §6`) fue dejarlo
"fuera de alcance, queda en PVWA". Alex pidió reconsiderarlo: quiere poder
revelarla también desde PAM Web Console, para no tener que saltar al PVWA para ese
caso puntual.

Esto es distinto del "Revelar" que ya existe para backups
(`docs/specs/account-safe-delete.md`): ese descifra un secreto **histórico**
guardado localmente de una cuenta ya borrada; este llama en vivo a
CyberArk (`Password/Retrieve`) y expone una credencial **real, en uso
ahora mismo** — mayor superficie de riesgo, así que va con el mismo techo
de RBAC que el reveal de backups.

## Alcance

**Entra:**
- `POST /tenants/{id}/accounts/{id}/reveal` — llama a
  `client.retrieve_password` (ya existía, portado para `account.delete`) y
  devuelve `{"password": "..."}`.
- Feature flag `account.reveal`, opt-in por tenant, **apagada por defecto**
  (igual criterio que toda escritura/exposición sensible de Fase 2+).
- RBAC `tenant_admin+` (no `approver`, no `operator`) — mismo nivel que
  "Revelar" de un backup.
- `reason` obligatoria (mínimo 8 caracteres), auditada.
- Botón "Ver contraseña" en el detalle de cuenta, gateado por la feature +
  el rol, mismo patrón visual que el reveal de backups (diálogo con razón
  → contraseña de sólo lectura + copiar, nunca queda en el DOM tras
  cerrar).

**Fuera:**
- Rate-limit / circuit breaker propios (no pasa por `writeop.run_write`:
  no hay "escritura" ni "dry-run" — revelar ES la acción completa).
- Forzar un `Change` después de revelar (igual criterio que el resto: no
  se automatiza la rotación como efecto colateral de una operación no
  relacionada).

## Requisitos

1. **Revelar con feature apagada.**
   Dado un tenant sin `account.reveal` habilitada,
   Cuando un `tenant_admin` pide `POST .../accounts/42/reveal`,
   Entonces `403`, auditado `denied` (`reason: feature_disabled`), sin
   llamar a CyberArk.

2. **RBAC insuficiente.**
   Dado un tenant CON `account.reveal` habilitada,
   Cuando un `approver` (no `tenant_admin+`) pide `POST .../reveal`,
   Entonces `403` (lo bloquea `require_tenant_access` antes de llegar al
   handler).

3. **Sin `reason`.**
   Dado un `tenant_admin` con la feature habilitada,
   Cuando pide `POST .../reveal` con `reason` vacía o de menos de 8
   caracteres,
   Entonces `422`, sin llamar a CyberArk.

4. **Revelar OK.**
   Dado lo anterior con una `reason` válida,
   Cuando se ejecuta,
   Entonces `client.retrieve_password(account_id, reason)` — devuelve
   `{"password": "..."}` y audita `account.reveal` (`result: ok`) con la
   `reason`, **nunca** el valor de la contraseña en `detail`.

5. **Cuenta inexistente.**
   Dado un `account_id` que no existe,
   Cuando se pide revelar,
   Entonces `404` (mapea `NotFound` del cliente CyberArk), auditado
   `result: error`.

## Diseño técnico

### Backend — `app/api/writes.py`

```python
@router.post("/accounts/{account_id}/reveal")
async def account_reveal(...) -> dict:
    ...
```

No usa `writeop.run_write` (no hay `dry_run`/`Plan`/diff que mostrar —
revelar es una sola llamada de lectura a CyberArk). Repite a mano el
gating de feature + `reason` + auditoría, mismo patrón que
`app.api.backups.account_backup_reveal`. RBAC vía
`require_tenant_access(OperatorRole.tenant_admin)`, dependencia nueva
`_tenant_admin` en `writes.py`.

Reusa `client.retrieve_password` (ya agregado para `account.delete`,
`retry_safe=False` explícito por el mismo motivo: evitar duplicar el
evento "Retrieve" en el audit nativo de CyberArk).

### Feature flag — `app/core/features.py` + `pages/Tenants.tsx`

`account.reveal` en `KNOWN_FEATURES` **y** en la lista `FEATURES` de
`Tenants.tsx`, en el mismo commit.

### Frontend

- `src/components/RevealPasswordDialog.tsx` — extraído del diálogo
  "Revelar" que ya existía sólo en `AccountBackups.tsx`, generalizado con
  props `path`/`title` para reusarlo en ambos casos (backup y cuenta
  activa).
- `src/components/AccountRevealSection.tsx` — botón "Ver contraseña" en
  `DetailDrawer` (cuentas), gateado por `enabled_features["account.reveal"]`
  + `my_role >= tenant_admin`.

## Tareas

- [x] `app/api/writes.py`: `POST /accounts/{id}/reveal` + `AccountRevealIn`
      + dependencia `_tenant_admin`.
- [x] `app/core/features.py`: `account.reveal` en `KNOWN_FEATURES`.
- [x] `pages/Tenants.tsx`: la feature nueva en `FEATURES`.
- [x] `src/components/RevealPasswordDialog.tsx` (compartido) +
      `AccountRevealSection.tsx`, wiring en `DetailDrawer`.
- [x] Tests unit: feature apagada, RBAC, reason corta, 404, reveal OK sin
      el secreto en el audit.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §6` (sacar "fuera de alcance").
- [ ] Probar contra Globex antes de cerrar (mismo permiso "Retrieve
      accounts" que ya hace falta para `account.delete`).
