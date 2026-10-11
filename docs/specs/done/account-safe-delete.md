# account-safe-delete — Fase 3: borrar cuenta / safe + copia de seguridad

- **Estado:** aprobada
- **Roadmap:** `docs/PLAN.md §7` Fase 3 — "Borrar cuenta/safe"
- **Fecha:** 2026-09-12 (aprobada 2026-09-14)

## Problema

Hoy PAM Web Console no puede borrar nada — cuentas y safes obsoletas se siguen dando de
baja a mano en el PVWA. Es la operación más destructiva del roadmap (borra
un objeto que no se puede recuperar desde CyberArk una vez hecho) y por eso
va después de `safe.member.update/remove` y `safe.update`, que ya probaron
la maquinaria de Fase 3 (confirmación reforzada + RBAC `approver+`).

Pedido explícito de Alex: antes de borrar una **cuenta**, guardar una copia
de seguridad completa — metadata **y la contraseña vigente** — para poder
recrearla si el borrado fue un error. Sin esto, un `account.delete`
accidental es irreversible incluso con auditoría (el audit log registra que
se borró, pero no alcanza para reconstruir la cuenta).

## Alcance

**Entra:**
- `account.delete` — `DELETE /tenants/{id}/accounts/{account_id}`: genera
  una copia de seguridad (metadata + secreto cifrado) y borra la cuenta.
- `safe.delete` — `DELETE /tenants/{id}/safes/{safe_id}`: borra una safe
  **vacía** (0 cuentas). Si tiene cuentas, se rechaza (422) antes de tocar
  CyberArk — hay que borrarlas primero con `account.delete` (cada una queda
  respaldada individualmente).
- Copias de seguridad de cuenta: almacenamiento cifrado propio de PAM Web Console
  (nueva tabla), independiente del secret store de credenciales de tenant.
  - Listado por tenant (metadata, sin secreto).
  - "Revelar" el secreto de un backup puntual (acción separada, auditada,
    RBAC más alto).
  - "Restaurar" — recrear la cuenta a partir de un backup, reusando la
    maquinaria de `account.create` (Fase 2).
- Misma maquinaria de Fase 3: RBAC `approver+`, confirmación reforzada
  (`confirm` = nombre de la cuenta / safe), `dry-run` + diff.

**Fuera:**
- Borrar una safe que tiene cuentas en un solo paso ("cascada"). Requeriría
  respaldar N cuentas de forma atómica antes de tocar la safe — más riesgo,
  spec propia si hace falta más adelante.
- Copia de seguridad de **safes** (permisos/miembros) o de plataformas —
  esta spec es sólo el respaldo de cuentas que pide Alex.
- Purga/retención automática de backups viejos (no hay jobs ARQ en Fase 3).
- Restaurar una safe borrada (fuera de alcance porque `safe.delete` sólo
  aplica a safes ya vacías — no hay nada que perder salvo la safe en sí,
  que se puede recrear a mano con `safe.create`).

## Requisitos

1. **Borrar cuenta (dry-run).**
   Dado la cuenta `db-prod-01` en la safe `SQL-PROD`,
   Cuando el operador pide `DELETE .../accounts/42` con `dry_run=true`,
   Entonces el diff muestra la cuenta completa `from -> None`,
   `confirm_phrase: "db-prod-01"`, warning: "se guarda una copia de
   seguridad completa (incluida la contraseña actual) antes de borrar; sin
   ella la cuenta no se puede recuperar." Nada se toca en CyberArk ni en la
   tabla de backups.

2. **Ejecutar `account.delete`: backup antes que borrado.**
   Dado el dry-run anterior,
   Cuando el operador ejecuta con `reason` válida y `confirm: "db-prod-01"`,
   Entonces PAM Web Console, en este orden: (a) recupera la contraseña vigente
   (`POST /API/Accounts/42/Password/Retrieve`), (b) guarda un
   `AccountBackup` con la metadata completa de la cuenta + el secreto
   cifrado, (c) recién ahí borra la cuenta en CyberArk
   (`DELETE /API/Accounts/42`). Si (c) falla, el backup ya guardado en (b)
   **queda** (no se revierte) — es el orden seguro: nunca se borra sin
   backup, en el peor caso queda un backup de una cuenta que en realidad no
   se borró.

3. **Confirmación reforzada obligatoria.**
   Dado el dry-run anterior,
   Cuando el operador ejecuta con `confirm: "DB-PROD-01"` (case distinto) o
   vacío,
   Entonces `422` "la confirmación no coincide", no se recupera contraseña
   ni se toca CyberArk.

4. **RBAC: `operator` rechazado.**
   Dado un operador con rol `operator` (no `approver`),
   Cuando pide `DELETE .../accounts/42`,
   Entonces `403` antes de tocar CyberArk, igual que `safe.member.update`.

5. **Borrar safe vacía.**
   Dado la safe `TEMP-MIGRACION` con 0 cuentas,
   Cuando el operador pide `DELETE .../safes/TEMP-MIGRACION` con
   `confirm: "TEMP-MIGRACION"` y `reason` válida,
   Entonces se borra (`DELETE /API/Safes/{id}`), auditado.

6. **Borrar safe con cuentas: bloqueado.**
   Dado la safe `SQL-PROD` con 12 cuentas,
   Cuando el operador pide `DELETE .../safes/SQL-PROD` (dry-run o no),
   Entonces `422` "la safe tiene 12 cuenta(s); hay que borrarlas antes de
   borrar la safe" — no se genera ningún backup ni se toca CyberArk. El
   conteo sale de `GET /accounts?safe_name=SQL-PROD&limit=1` (usa el
   `total` que ya devuelve la paginación server-side, sin traer las 12
   filas).

7. **Listar backups del tenant.**
   Dado 3 cuentas borradas en `globex` en el último mes,
   Cuando un operador `tenant_admin+` pide `GET /tenants/globex/account-backups`,
   Entonces recibe `[{id, account_name, safe_name, user_name, address,
   platform_id, deleted_by, deleted_at}]` — **sin el secreto** ni su
   ciphertext.

8. **Revelar el secreto de un backup.**
   Dado el backup `#17` de la cuenta `db-prod-01`,
   Cuando un operador `tenant_admin+` pide
   `POST /tenants/globex/account-backups/17/reveal {reason: "..."}`,
   Entonces recibe `{"password": "..."}` (descifrado on-demand, nunca
   persistido en claro) y se audita `account.backup.reveal` con el
   `backup_id`, el operador y la razón — **sin el valor de la contraseña**
   en el detail del audit.

9. **Revelar: RBAC insuficiente.**
   Dado un operador `viewer`/`operator` (no `tenant_admin+`),
   Cuando pide `.../17/reveal`,
   Entonces `403`, auditado como `denied`.

10. **Restaurar una cuenta desde un backup.**
    Dado el backup `#17` (`db-prod-01`, safe `SQL-PROD`, plataforma
    `WinServerLocal`),
    Cuando un `approver+` pide `POST /tenants/globex/account-backups/17/restore`
    con `dry_run=true`,
    Entonces el diff es el mismo que un `account.create` con los campos del
    backup precargados (incluida la contraseña, ya descifrada para el
    payload — nunca aparece en el diff, mismo criterio que `account.create`
    hoy). Ejecutar sigue las reglas de `account.create` (RBAC `operator+`,
    sin `confirm_phrase`) **más** una confirmación reforzada propia de
    "restaurar" (`confirm` = `account_name` del backup) porque recrea algo
    que un `approver` decidió borrar.

11. **Restaurar: la feature `account.create` debe estar habilitada.**
    Dado un tenant sin `account.create` habilitado,
    Cuando se intenta restaurar un backup,
    Entonces `403` "la feature 'account.create' no está habilitada para
    este tenant" (mismo gating que crear una cuenta nueva).

12. **Cuenta ya borrada (idempotencia del 404).**
    Dado la cuenta `42` ya no existe,
    Cuando se pide `DELETE .../accounts/42` de nuevo,
    Entonces `404` en el `fetch`, sin generar un segundo backup.

## Diseño técnico

### Cliente CyberArk — `app/cyberark/client.py`

```python
async def retrieve_password(self, account_id: str, reason: str) -> str:
    """POST /API/Accounts/{id}/Password/Retrieve. Devuelve la contraseña en
    texto plano (la API responde un string JSON, no un objeto). `reason` se
    manda siempre — algunas safes lo exigen por política ("Require reason
    to retrieve"); si la safe no lo exige, CyberArk lo ignora."""

async def delete_account(self, account_id: str) -> None:
    """DELETE /API/Accounts/{id}."""

async def delete_safe(self, safe_id: str) -> None:
    """DELETE /API/Safes/{safeUrlId}."""
```

`retrieve_password` es la única llamada nueva con `retry_safe` en **False**
explícito aunque sea de lectura: un retry silencioso duplicaría el evento
de "Retrieve" en el audit nativo de CyberArk (y si el platform tiene
"Change password immediately after every Retrieve", podría disparar dos
rotaciones en vez de una). Nunca se loguea el valor devuelto (ni en logs de
la app ni en `detail` de audit).

### Modelo — `app/models/account_backup.py`

```python
class AccountBackup(Base, TimestampMixin):
    __tablename__ = "account_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[str] = mapped_column(String(64))       # id en CyberArk (ya no existe)
    account_name: Mapped[str] = mapped_column(String(300))
    safe_name: Mapped[str] = mapped_column(String(200))
    user_name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    platform_id: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON)          # snapshot completo de get_account
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    deleted_by: Mapped[str] = mapped_column(String(120))       # operator.username
    reason: Mapped[str] = mapped_column(Text)
```

`metadata_json` guarda el objeto de `client.get_account()` tal cual (para
poder reconstruir `account.create` en el restore) **menos** cualquier campo
de secreto — CyberArk no devuelve la contraseña en ese objeto, así que no
hace falta filtrar nada extra. Migración Alembic `0005_account_backups.py`.

### Cifrado — `app/core/backups.py` (nuevo)

Reusa el patrón de `app/core/secrets.py` (binario `age`, ya en la imagen)
pero con un **keypair propio de backups**, separado del que descifra las
credenciales de tenant — comprometer uno no compromete el otro:

```python
def encrypt_secret(plaintext: str) -> bytes:
    """age -r <PAM_BACKUPS_AGE_RECIPIENT> — sólo necesita la pública,
    no la privada. Puede correr en cualquier request de escritura."""

def decrypt_secret(ciphertext: bytes) -> str:
    """age --decrypt -i <PAM_BACKUPS_AGE_KEY> — sólo se llama desde el
    endpoint de reveal."""
```

Config nueva en `app/config.py` (prefijo `PAM_`): `backups_age_key`
(archivo privado, monta en `secrets/backups-age.key`, mismo trato que
`secrets_age_key`) y `backups_age_recipient` (la pública — **no es
secreta**, puede ir en `.env`/compose). Se generan una vez con
`age-keygen` en el server, documentado en `secrets/README.md`.

### Dominio

`app/domain/accounts.py`:
```python
def build_account_delete_plan(current: dict, _changes: dict) -> Plan:
    """Diff = la cuenta completa (campos visibles) from->None. effect =
    'Se borra la cuenta {name}; se guarda una copia de seguridad completa
    (incluida la contraseña) antes de borrar.' confirm_phrase = current['name'].
    """
```

`app/domain/safes.py`:
```python
def build_safe_delete_plan(current: dict, _changes: dict) -> Plan:
    """`current` = {"safe": {...}} (ya validado vacío por el fetch — ver
    abajo). diff = la safe from->None. confirm_phrase = safeName."""
```

### Endpoints — `app/api/writes.py`

```
DELETE /tenants/{id}/accounts/{account_id}
Body: { dry_run, reason, confirm }
```
RBAC `_approver`. `fetch` = `client.get_account(account_id)` (guarda en
closure `fetched["account"]`, igual patrón que `safe_update`). `apply`:
1. `secret = await client.retrieve_password(account_id, reason)`
2. `await save_account_backup(session, tenant_id, fetched["account"], secret, op.username, reason)` (commitea su propia fila — independiente del commit de audit de `run_write`, ver "Decisiones abiertas" #3)
3. `await client.delete_account(account_id)`
4. devuelve `{"deleted": True, "id": account_id}`

```
DELETE /tenants/{id}/safes/{safe_id}
Body: { dry_run, reason, confirm }
```
RBAC `_approver`. `fetch`:
```python
async def fetch(client: CyberArkClient) -> dict:
    safe = await client.get_safe(safe_id)
    _rows, total = await client.accounts_page(safe_name=safe["safeName"], limit=1, offset=0)
    if total > 0:
        raise HTTPException(422, f"la safe tiene {total} cuenta(s); bórrelas antes de borrar la safe")
    return {"safe": safe}
```
`apply` = `client.delete_safe(safe_id)`. Invalida `pam:safes:{tenant_id}`
igual que `safe_create`/`safe_update`.

### Backups — nuevo router `app/api/backups.py`

```
GET  /tenants/{id}/account-backups                → require_tenant_access(tenant_admin)
POST /tenants/{id}/account-backups/{bid}/reveal    → require_tenant_access(tenant_admin) + reason
POST /tenants/{id}/account-backups/{bid}/restore   → run_write con feature "account.create",
                                                      RBAC approver+, confirm_phrase = account_name
```
`reveal` no pasa por `run_write` (no escribe en CyberArk) pero sí exige
`reason` (mismo mínimo de 8 caracteres) y audita `account.backup.reveal`
con `result="ok"`/`detail={"backup_id", "reason"}` — **nunca** el secreto.
`restore` arma `changes` a partir de `metadata_json` del backup +
`decrypt_secret(secret_ciphertext)`, usando la contraseña del backup tal
cual (sin forzar un `Change` inmediato tras recrear la cuenta — el CPM la
rota en su próximo ciclo normal), y llama a
`run_write(..., feature="account.create", build_plan=plan_account_create,
apply=lambda c, _p: c.create_account(build_account_create_body(changes)))`
— reutiliza tal cual la Plan de `account.create` (Fase 2); la única
diferencia es un `confirm_phrase` extra (`confirm` = `account_name` del
backup) antes de ejecutar, chequeado en el propio endpoint (no en
`run_write`, porque `account.create` no usa `Plan.confirm_phrase` para no
romper Fase 2).

### Feature flags — `app/core/features.py` + `pages/Tenants.tsx`

`account.delete`, `safe.delete` en `KNOWN_FEATURES` **y** en la lista
`FEATURES` de `Tenants.tsx` en el mismo commit (gotcha ya documentado dos
veces en `docs/PLAN.md`). **No hay una feature `account.restore` separada**
— `restore` reusa el gating de `account.create` (requisito 11): recrea una
cuenta con la misma maquinaria que un alta, así que basta con que esa
feature ya esté habilitada.

### Frontend

- `DetailDrawer.tsx`: botón "Borrar cuenta" (cuentas) / "Borrar safe"
  (safes) — `<DryRunDialog method="DELETE">`, gateado por
  `enabled_features["account.delete"|"safe.delete"]` + `my_role ≥ approver`.
- `AccountBackups.tsx` (página nueva, selector de tenant): tabla de
  backups (`account_name`, `safe_name`, `deleted_by`, `deleted_at`) +
  acción "Revelar" (sólo visible si `my_role >= tenant_admin`; pide
  `reason`, muestra la contraseña en un campo de sólo lectura con botón
  copiar, nunca queda en el DOM tras cerrar el diálogo) + "Restaurar"
  (`<DryRunDialog method="POST">`, gateado por `account.create` +
  `approver+`).
- Entrada en el sidebar (`App.tsx`), visible sólo si el operador tiene
  `tenant_admin+` en al menos un tenant.

## Tareas

- [x] `app/config.py`: `backups_age_key`, `backups_age_recipient`.
- [x] `secrets/README.md`: cómo generar el keypair de backups (`age-keygen`)
      y montarlo en compose (paralelo a `age.key`).
- [x] `app/core/backups.py`: `encrypt_secret`/`decrypt_secret` +
      `save_account_backup`/`list_backups`/`get_backup`.
- [x] `app/models/account_backup.py` + Alembic `0005_account_backups.py`.
- [x] `app/cyberark/client.py`: `retrieve_password`, `delete_account`,
      `delete_safe`.
- [x] `app/domain/accounts.py`: `build_account_delete_plan`.
- [x] `app/domain/safes.py`: `build_safe_delete_plan`.
- [x] `app/core/features.py`: `account.delete`, `safe.delete` en
      `KNOWN_FEATURES` (restore reusa `account.create`, sin flag propia).
- [x] `app/api/writes.py`: `DELETE /accounts/{id}`, `DELETE /safes/{id}`.
- [x] `app/api/backups.py` (nuevo router): list / reveal / restore.
- [x] `pages/Tenants.tsx`: las 2 features nuevas en `FEATURES`.
- [x] Tests unit: plan de borrado (cuenta y safe), safe no vacía bloqueada,
      confirm mismatch, RBAC (approver vs operator, platform_admin vs
      tenant_admin en reveal), cifrado/descifrado de backups (roundtrip),
      no se audita el secreto en ningún `detail`, restore arma el mismo
      diff que `account.create`.
- [x] Frontend: botones de borrado en `DetailDrawer`, página
      `AccountBackups.tsx`, wiring de rutas y sidebar.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` (nuevo módulo `app/core/backups.py`, tabla
      `account_backups`, endpoints nuevos) + `docs/PLAN.md §7`.
- [x] Verificar que el service account tenga permiso "Retrieve accounts"
      en las safes de Globex antes de probar (puede no estar concedido —
      Fase 1/2 nunca lo necesitaron).
- [ ] Probar contra Globex: borrar una cuenta de prueba, confirmar que el
      backup tiene la contraseña correcta (comparar con un login real antes
      de borrar), revelar, restaurar, y borrar una safe vacía.

## Decisiones (cerradas 2026-09-14)

1. **RBAC de "Revelar": `tenant_admin+`.** Alternativa más laxa que
   `platform_admin` — cualquier admin del tenant puede revelar, sin
   necesitar rol global.
2. **Confirmación reforzada en `restore`: sí.** `confirm` = `account_name`
   del backup, chequeado a mano en el endpoint de `restore` antes de llamar
   a `run_write` (no vía `Plan.confirm_phrase`, para no tocar `account.create`
   de Fase 2). Recrea algo que un `approver` decidió borrar — mismo criterio
   de seguridad que el resto de Fase 3, aunque sea técnicamente una alta.
3. **Contraseña al restaurar: la del backup, sin forzar `Change`.** Se
   recrea la cuenta con la contraseña guardada y se deja que el CPM la rote
   en su próximo ciclo normal.

## Decisiones abiertas

1. **Retención de backups.** Sin purga automática en esta spec — quedan
   para siempre (o hasta que exista una política y un job ARQ que la
   aplique). Puede ser mucho volumen con el tiempo; revisar si hace falta
   un TTL o un export-y-borrar manual.
2. **Permiso "Retrieve accounts" del service account.** Hoy el service
   account de PAM Web Console (heredado de ITR) es de sólo lectura + las escrituras de
   Fase 2/3 ya habilitadas; "Retrieve" es un permiso nuevo por safe que
   puede no estar concedido en todos los clientes. Habilitar `account.delete`
   por tenant queda condicionado a verificarlo primero (mismo criterio que
   cualquier feature nueva: opt-in, probar en Globex/lab antes que en un
   cliente real).
