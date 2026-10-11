# secrets/

Secret store de PAM Web Console — **puente de Fase 0-1** (después: OpenBao, ver
`docs/PLAN.md` §5.3). Nada de aquí se commitea salvo este README y el
`.example`.

Contenido en el server:

| Archivo | Qué es |
|---|---|
| `age.key` | clave privada `age` (chmod 600). **Nunca** sale del server. |
| `secrets.age` | JSON cifrado con las credenciales de los Service Users por tenant. |
| `secrets.example.json` | plantilla de la estructura del JSON (se commitea). |
| `backups-age.key` | clave privada `age` **propia de backups de cuentas** (Fase 3, `account.delete`, chmod 600). Separada de `age.key` a propósito — comprometer una no compromete la otra. |

Se montan read-only en el contenedor `api` en `/run/secrets/`.

## Rotar la contraseña de UN tenant

No use el procedimiento de alta de abajo para esto: arma el archivo desde
cero y dejaría sin credenciales a los demás tenants. Para cambiar el
`client_secret` de uno solo:

```bash
cd /opt/pam-web-console && docker compose run --rm secrets-admin set-tenant-secret <tenant>
```

Prueba el valor contra Identity antes de guardarlo y `api`/`worker` lo toman
solos, sin reiniciar.

## Alta de un tenant (archivo nuevo)

```bash
cd /opt/pam-web-console/secrets

# 1. generar la clave (una sola vez)
age-keygen -o age.key && chmod 600 age.key

# 2. armar el JSON en claro (copiar client_id / client_secret de los
#    config.json de ITR: clientes/<cliente>/config.json)
cp secrets.example.json secrets.plain.json
nano secrets.plain.json

# 3. cifrar con la clave pública (la muestra age-keygen, o:)
age -r "$(age-keygen -y age.key)" -o secrets.age secrets.plain.json

# 4. BORRAR el archivo en claro
shred -u secrets.plain.json

# 5. comprobar (api y worker detectan el archivo nuevo solos; no hace falta
#    reiniciar)
cd /opt/pam-web-console && docker compose exec api python manage.py check-secrets
```

## Keypair de backups (Fase 3, `account.delete`)

Cada `account.delete` guarda una copia cifrada de la cuenta (metadata + la
contraseña vigente) ANTES de borrarla en CyberArk. Usa un keypair `age`
propio, para no compartir exposición con `age.key` (ese descifra TODAS las
credenciales de tenant en cada arranque; este solo descifra backups
puntuales, bajo demanda, cuando un `tenant_admin+` pide "Revelar"). Se
genera una sola vez:

```bash
cd /opt/pam-web-console/secrets

# 1. generar el keypair
age-keygen -o backups-age.key && chmod 600 backups-age.key

# 2. la pública (recipient) NO es secreta — va en .env, no en secrets/
age-keygen -y backups-age.key
# copiar la salida (age1...) a PAM_BACKUPS_AGE_RECIPIENT en /opt/pam-web-console/.env

# 3. recargar
cd /opt/pam-web-console && docker compose restart api
```

Si `PAM_BACKUPS_AGE_RECIPIENT` queda vacío, `account.delete` falla al
ejecutar (`BackupCryptoError` al cifrar) — generar este keypair antes de
que alguien con rol `approver+` intente borrar una cuenta en un tenant
real.
