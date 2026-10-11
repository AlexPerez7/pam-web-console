# account-credentials-backup — backup cifrado de TODAS las cuentas de un tenant

- **Estado:** hecha, probada contra Globex (2026-09-24)
- **Roadmap:** `docs/PLAN.md §7` Fase 3 — extra a pedido de cliente
- **Fecha:** 2026-09-14

## Problema

Un cliente pidió, antes de una limpieza masiva de cuentas, un backup
completo de **todas** las cuentas del tenant — usuario, address,
plataforma, safe y **contraseña** — para poder recrearlas a mano si hiciera
falta reponer alguna. Para cuentas de dominio, también necesitan las
**remote machines** (sin eso el "Add Account" no queda completo).

Esto es distinto del backup de `account.delete`
(`docs/specs/account-safe-delete.md`): ese es automático, por cuenta, sólo
al momento de borrarla, y queda dentro de la base de PAM Web Console. Esto es un
**export manual y ocasional de todo el tenant de una sola vez** — más
parecido en espíritu al script `Account_Secret_Backup.ps1` de
`PowerShell-Scripts`, cuya lógica (listar todo, recuperar el secreto
cuenta por cuenta, seguir aunque alguna falle, exportar cifrado) se
reutilizó acá.

## Alcance

**Entra:**
- `POST /tenants/{id}/credentials-backup/export`: lista TODAS las cuentas
  del tenant, recupera el secreto de cada una y devuelve un **ZIP
  protegido con contraseña** (AES real vía `pyzipper`, no el ZipCrypto
  débil del zip estándar) — pensado para entregárselo directo al
  cliente. La contraseña se genera al azar en cada export y viaja en el
  header `X-Backup-Password` de la misma respuesta; PAM Web Console nunca la guarda
  (ni DB, ni audit, ni logs) — se muestra una sola vez en la UI y hay que
  comunicarla por un canal separado del archivo. **Cambio 2026-09-24**
  (a pedido de Alex): el diseño original cifraba con el keypair `age` de
  `account.delete` (`PAM_BACKUPS_AGE_KEY`/`_RECIPIENT`), pero eso sólo
  se puede descifrar en el server — para el caso real de uso (entregarle
  el archivo al cliente) hacía falta un paso manual extra de
  descifrar-y-reempaquetar. El ZIP con contraseña al azar resuelve eso
  en un solo paso, sin tocar el keypair de `account.delete` (que sigue
  igual para los backups automáticos de cuentas borradas).
- Columnas: `accountId`, `accountName`, `userName`, `address`,
  `platformId`, `safeName`, `remoteMachines`, `secretType`, `secret`,
  `retrieveStatus` (OK/EMPTY/ERROR), `retrieveError`.
- Una cuenta que falla (sin permiso Retrieve, sin secreto guardado, error
  transitorio) NO aborta el resto — queda con su status y el backup sigue.
- RBAC `tenant_admin+` (mismo techo que `account.reveal`).
- `reason` obligatoria (mínimo 8 caracteres), auditada (nunca el secreto
  ni la contraseña del zip).
- Botón en la página **Reportes** (no en el catálogo de reportes — ver
  "Diseño técnico"), visible sólo con el rol.

**Fuera:**
- Jobs en background / progreso en vivo — el request es síncrono y puede
  tardar varios minutos; se aceptó ese trade-off porque es una acción
  ocasional (antes de una limpieza masiva), no algo que se corra seguido.
  Si en el futuro hace falta, hay que meter ARQ (mismo bloqueo que Batch 4
  de Reportes en `docs/PLAN.md`).
- Filtrar por safe/plataforma en esta v1 — siempre es el tenant completo.
- Restaurar automáticamente desde este backup (a diferencia del backup de
  `account.delete`, que sí tiene un endpoint de `restore` — acá el ZIP es
  para recrear a mano vía `account.create`).

## Requisitos

1. **Export OK.** Dado un tenant con 200 cuentas, cuando un
   `tenant_admin` pide el export con una `reason` válida, entonces recibe
   un `.zip` que abre con la contraseña del header `X-Backup-Password` de
   esa misma respuesta y tiene un CSV adentro con 200 filas + cabecera,
   cada una con su `secret`.
2. **Cuenta de dominio con remote machines.** Dada una cuenta con
   `remoteMachinesAccess.remoteMachines = "10.1.2.1;10.1.2.2"`, la columna
   `remoteMachines` del CSV trae ese valor tal cual.
3. **Una cuenta falla, el resto sigue.** Dado un tenant con 3 cuentas donde
   la cuenta `#3` no tiene permiso Retrieve en su safe, el CSV trae las 3
   filas — `#3` con `retrieveStatus=ERROR` y `secret` vacío, `#1`/`#2` con
   `OK` y su secreto.
4. **RBAC insuficiente.** Dado un `approver` (no `tenant_admin+`), `403`
   antes de llegar al handler.
5. **`reason` corta.** `422`, sin llamar a CyberArk.
6. **Auditoría sin secretos ni contraseña.** El audit de
   `account.reveal.bulk` trae `reason` + conteo por estado
   (`ok`/`empty`/`error`) — nunca ningún valor de contraseña de cuenta ni
   la contraseña del zip.

## Diseño técnico

### Backend — `app/api/credentials_backup.py` (router nuevo, fuera de `app.api.reports`)

El catálogo de Reportes (`app/reports/`) es siempre de sólo lectura, sin
secretos, rol `viewer`, con resultado cacheado en Redis — meter un reporte
que expone contraseñas ahí rompería esa garantía (y cachear 2000
contraseñas en Redis sería un desastre). Por eso esto vive en su propio
router, con el mismo nivel de RBAC/gating que `account.reveal`
(`docs/specs/account-reveal.md`), aunque la UI lo muestre dentro de la
página Reportes (pedido explícito de Alex).

`_retrieve(client, account, reason)`: nunca levanta — envuelve
`client.retrieve_password` y devuelve la fila con `_secret_status`
OK/EMPTY/ERROR. `asyncio.gather` sobre todas las cuentas — la
concurrencia real hacia CyberArk la limita el semáforo interno del cliente
(`PAM_CYBERARK_MAX_CONCURRENCY`, default 8), no hace falta uno propio acá.

El CSV se arma con `app.core.csv_export.to_csv` (mismo helper que
`app/api/exports.py`) y se empaqueta en un ZIP protegido con
`pyzipper.AESZipFile` (AES real) usando una contraseña al azar
(`secrets.token_urlsafe`) generada de nuevo en cada request — nunca se
guarda, viaja sólo en el header `X-Backup-Password` de la respuesta. No
usa el keypair `age` de `account.delete` (ver "Cambio 2026-09-24" más
arriba).

### nginx — `frontend/nginx.conf`

`proxy_read_timeout` de `/api/` subido de 120s a 600s (en ambos server
blocks, :80 y :443) — con miles de cuentas el request puede tardar varios
minutos y el resto de la API responde en milisegundos, así que subirlo no
afecta a nadie más.

### Frontend

- `src/components/CredentialsBackupSection.tsx`: caja con razón + botón
  "Descargar backup cifrado", visible en `Reports.tsx` cuando hay un
  tenant elegido **y** `my_role >= tenant_admin`. Usa `apiBlob`
  (extendido para aceptar `method`/`body`/`headers` — este último para
  leer `x-backup-password` de la respuesta) + `saveBlob`. La contraseña
  se muestra inline (readonly + botón copiar, mismo patrón que
  `RevealPasswordDialog`) con un botón "Ocultar" para sacarla de
  pantalla sin esperar a cerrar nada.

## Tareas

- [x] `app/api/credentials_backup.py`: endpoint + `_retrieve` + columnas
      + `_build_password_zip`.
- [x] `app/main.py`: registrar el router.
- [x] `frontend/nginx.conf`: `proxy_read_timeout` 600s.
- [x] `backend/requirements.txt`: `pyzipper==0.4.0`.
- [x] `src/lib/api.ts`: `apiBlob` acepta `method`/`body`/`headers`.
- [x] `src/components/CredentialsBackupSection.tsx` + wiring en
      `Reports.tsx`.
- [x] Tests unit: export OK (abre el zip con la contraseña del header, y
      confirma que una contraseña incorrecta falla), remoteMachines, una
      cuenta falla y el resto sigue, RBAC, reason corta, error al
      listar, auditoría sin secretos ni contraseña del zip.
- [x] `ruff` + `mypy` + `pytest` + `pip-audit` + `lint` + `tsc` + `build`
      verdes.
- [x] El permiso "Retrieve accounts" (el mismo que ya hacía falta para
      `account.delete`/`account.reveal`) confirmado que ya lo tenía el
      service account de Globex (2026-09-24).
- [x] Probado contra Globex con el formato ZIP (2026-09-24) — abre
      correctamente con la contraseña del header, confirmado por Alex.
