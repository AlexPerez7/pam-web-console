#!/usr/bin/env python3
"""
CLI de administración de PAM Web Console. Correr dentro del contenedor:

    docker compose exec api python manage.py <comando>

Comandos:
    seed-tenants                       Upsert del registro de tenants
    verify-audit                       Valida la cadena de hashes del audit log
    check-secrets                      Prueba que el secret store se descifre
    set-tenant-secret <tenant> [--no-verify]
                                       Cambia el client_secret de UN tenant en
                                       el secret store (pide el valor sin
                                       mostrarlo), tras comprobar que Identity
                                       lo acepta. Necesita el secret store
                                       escribible: correr con
                                       `docker compose run --rm secrets-admin
                                       set-tenant-secret <tenant>`. api y
                                       worker lo toman solos, sin reiniciar.
    check-tenant-login <tenant>        Un solo login contra Identity con las
                                       credenciales guardadas del tenant
                                       (solo lectura, no imprime el secreto).
                                       Sirve para saber si la credencial
                                       vigente de PAM Web Console todavía funciona.
    create-operator <username> [rol]   Crea un operador (rol default: viewer)
                                       roles: viewer operator approver
                                              tenant_admin platform_admin
    reset-operator <username>          Nueva contraseña + limpia el TOTP
                                       (el operador re-enrola en el próximo login)
    set-active <username> <on|off>     Habilita / deshabilita un operador
    list-operators                     Lista los operadores
    demo-setup [username]              Sólo con PAM_DEMO_MODE=true: carga los
                                       tenants ficticios del seed y crea el
                                       primer operador (platform_admin,
                                       default "demo") si todavía no hay
                                       ninguno. Ver README, "Probar la demo".

    sync-accounts-now                  Corre a mano el job de sync nocturno
                                       de account_snapshots (todos los
                                       tenants) sin esperar al cron real del
                                       worker — mismo código que corre en
                                       app/jobs/inventory.py. Ver
                                       docs/specs/jobs-programados.md.

    check-alerts-now                   Corre a mano el motor de reglas de
                                       alerta (componente/tenant caído,
                                       aprobación pendiente) sin esperar al
                                       cron real del worker — mismo código
                                       que app/jobs/alerts.py. Muestra
                                       también las alertas abiertas y los
                                       avisos pendientes de entrega. Ver
                                       docs/specs/done/alert-rules.md.

    test-alert [tipo]                  Manda a Teams/email avisos de EJEMPLO
                                       (marcados [PRUEBA], datos inventados)
                                       para ver el formato sin esperar una
                                       caída real. tipo: component | partial
                                       | worse | approval | vendor |
                                       resolved | all (default all). No
                                       toca la DB ni ningún tenant.

    export-audit-since <id>            Vuelca a stdout (JSON Lines) las filas
                                       de audit_log con id > <id>. Correr en
                                       el standby para el fail-back.
    import-standby-audit <archivo>     Re-registra en ESTA primaria las filas
                                       exportadas con export-audit-since
                                       (nunca copia hash/prev_hash: cada fila
                                       se re-encadena de verdad al final de
                                       la cadena local). Correr en la primaria
                                       tras el fail-back.

Los comandos que cambian algo (operadores, secretos, tenants, fail-back)
quedan en el audit log a nombre del operador de PAM Web Console que los corre: lo
piden al empezar, o se pasa con `--operador <usuario>` (o la variable
PAM_OPERATOR) cuando no hay terminal interactiva (`exec -T`). Tiene que
ser un platform_admin activo.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
from datetime import datetime

import httpx
from sqlalchemy import or_, select

from app.config import get_settings
from app.core import notify, security
from app.core.audit import record as record_audit
from app.core.audit import verify_chain
from app.core.secrets import (
    SecretStoreError,
    clean_secret_input,
    get_secret,
    update_secret_field,
)
from app.core.secrets import available as secrets_available
from app.core.seed import seed_tenants
from app.cyberark.auth import ISPSSAuth
from app.cyberark.errors import AuthError
from app.db import SessionLocal, engine
from app.jobs.alerts import check_alert_rules, sample_messages
from app.jobs.inventory import sync_all_tenants_accounts
from app.models import AlertState, AuditLog, Operator, OperatorRole, Tenant

Actor = tuple[str, str]  # (username, rol) de quien corre el comando
_OPERATOR_FLAG: str | None = None  # `--operador <usuario>`, sacado de sys.argv en main()


def _pop_operator_flag() -> str | None:
    """Saca `--operador <usuario>` de sys.argv para que el parseo de cada
    comando no lo vea."""
    if "--operador" not in sys.argv:
        return None
    i = sys.argv.index("--operador")
    if i + 1 >= len(sys.argv):
        print("uso: --operador <usuario de PAM Web Console>")
        sys.exit(2)
    name = sys.argv[i + 1]
    del sys.argv[i : i + 2]
    return name


async def _lookup_operator(name: str | None) -> tuple[bool, Operator | None]:
    """(hay algún operador, el operador `name`). Cierra el pool al terminar:
    corre en su propio `asyncio.run`, y las conexiones de asyncpg no se
    pueden reusar desde el loop del comando que viene después."""
    try:
        async with SessionLocal() as session:
            anyone = (await session.execute(select(Operator.id).limit(1))).first() is not None
            op = None
            if name:
                op = (
                    await session.execute(select(Operator).where(Operator.username == name))
                ).scalar_one_or_none()
            return anyone, op
    finally:
        await engine.dispose()


def _cli_operator(*, bootstrap_ok: bool = False) -> Actor:
    """Operador de PAM Web Console que corre un comando que cambia algo, para
    atribuírselo en el audit log (CLAUDE.md, principio 2) en vez de a
    "system". Tiene que ser un platform_admin activo. Con `bootstrap_ok`,
    si todavía no hay ningún operador (primer arranque, `create-operator`)
    se registra como "primer-arranque".

    Es lo que declara quien tiene acceso SSH al server, no una
    autenticación: alcanza para saber quién hizo qué en un equipo chico."""
    if bootstrap_ok and not asyncio.run(_lookup_operator(None))[0]:
        return ("primer-arranque", "platform_admin")
    name = _OPERATOR_FLAG or os.environ.get("PAM_OPERATOR", "").strip()
    if not name:
        if not sys.stdin.isatty():
            print(
                "falta el operador: este comando queda en el audit log a su nombre. "
                "Agregue --operador <su usuario de PAM Web Console> (o córralo con -it)."
            )
            sys.exit(2)
        name = input("Su usuario de PAM Web Console (queda en el audit log): ").strip()
    _, op = asyncio.run(_lookup_operator(name))
    if op is None or not op.is_active:
        print(f"'{name}' no es un operador activo de PAM Web Console")
        sys.exit(1)
    if op.role != OperatorRole.platform_admin:
        print(f"'{name}' no es platform_admin: no puede correr este comando")
        sys.exit(1)
    return (op.username, op.role.value)


async def _seed_tenants(actor: Actor) -> None:
    async with SessionLocal() as session:
        touched = await seed_tenants(session)
        if touched:
            await record_audit(
                session,
                operator=actor[0],
                role=actor[1],
                action="tenant.seed",
                result="ok",
                detail={"tenants": touched, "via": "manage.py seed-tenants"},
            )
            await session.commit()
    print("tenants:", ", ".join(touched) if touched else "(sin cambios)")


async def _verify_audit() -> None:
    async with SessionLocal() as session:
        v = await verify_chain(session)
    if v.intact:
        print(f"audit log: cadena íntegra ({v.checked} registros, id {v.first_id}-{v.last_id})")
    else:
        print(f"audit log: CADENA ROTA a partir del id {v.first_broken_id}")
        sys.exit(1)


def _check_secrets() -> None:
    if secrets_available():
        print("secret store: OK (descifra)")
    else:
        print("secret store: NO disponible (falta el archivo o la clave age)")
        sys.exit(1)


async def _try_login(identity_url: str, client_id: str, client_secret: str) -> str | None:
    """Un solo login contra Identity con la credencial candidata. Devuelve el
    motivo del rechazo, o None si la acepta. Sin caché de tokens."""
    auth = ISPSSAuth(identity_url, client_id, client_secret)
    try:
        async with httpx.AsyncClient() as client:
            await auth.login(client)
    except AuthError as e:
        return str(e)
    except httpx.HTTPError as e:
        return f"no se pudo contactar a Identity: {e}"
    return None


async def _set_tenant_secret(tenant_id: str, actor: Actor, *, verify: bool) -> None:
    async with SessionLocal() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            print(f"no existe el tenant '{tenant_id}'")
            sys.exit(1)
        try:
            current = get_secret(tenant.secret_ref)
        except SecretStoreError as e:
            print(f"secret store: {e}")
            sys.exit(1)

        try:
            value = clean_secret_input(getpass.getpass(f"client_secret nuevo de {tenant_id}: "))
        except ValueError as e:
            print(f"valor inválido: {e}")
            sys.exit(1)

        verified = False
        if verify:
            if not tenant.identity_tenant_url:
                print("el tenant no tiene identity_tenant_url: se omite la verificación")
            else:
                print("verificando contra Identity (un solo intento)...")
                error = await _try_login(
                    tenant.identity_tenant_url, current.get("client_id", ""), value
                )
                if error:
                    print(f"Identity rechazó la credencial: {error}")
                    print("No se guardó nada. Revise el valor y que el usuario no esté bloqueado.")
                    sys.exit(1)
                verified = True

        try:
            prev = update_secret_field(tenant.secret_ref, "client_secret", value)
        except SecretStoreError as e:
            print(f"no se pudo actualizar el secret store: {e}")
            sys.exit(1)

        await record_audit(
            session,
            operator=actor[0],
            role=actor[1],
            action="secrets.tenant_secret.update",
            result="ok",
            tenant_id=tenant.id,
            detail={
                "field": "client_secret",
                "verified": verified,
                "via": "manage.py set-tenant-secret",
            },
        )
        await session.commit()

    print(f"{tenant_id}: client_secret actualizado (largo {len(value)}, verificado: {verified}).")
    print(f"Copia previa (cifrada): {prev.name} — bórrela cuando confirme que todo anda.")
    print("api y worker toman el valor nuevo solos en la próxima consulta (sin reiniciar).")


async def _check_tenant_login(tenant_id: str) -> None:
    """Un solo login contra Identity con las credenciales que PAM Web Console tiene
    guardadas para el tenant. Solo lectura: no escribe nada ni imprime el
    secreto."""
    async with SessionLocal() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
    if tenant is None:
        print(f"no existe el tenant '{tenant_id}'")
        sys.exit(1)
    if not tenant.identity_tenant_url:
        print(f"{tenant_id}: no tiene identity_tenant_url")
        sys.exit(1)
    try:
        creds = get_secret(tenant.secret_ref)
    except SecretStoreError as e:
        print(f"secret store: {e}")
        sys.exit(1)
    print(f"{tenant_id}: {tenant.identity_tenant_url} como {creds.get('client_id', '?')}")
    error = await _try_login(
        tenant.identity_tenant_url, creds.get("client_id", ""), creds.get("client_secret", "")
    )
    if error:
        print(f"RECHAZADO: {error}")
        sys.exit(1)
    print("LOGIN OK")


async def _create_operator(username: str, role: str, password: str, actor: Actor) -> None:
    try:
        role_enum = OperatorRole(role)
    except ValueError:
        print(f"rol inválido: {role} (usar: {', '.join(r.value for r in OperatorRole)})")
        sys.exit(1)
    async with SessionLocal() as session:
        exists = (
            await session.execute(select(Operator).where(Operator.username == username))
        ).scalar_one_or_none()
        if exists is not None:
            print(f"ya existe un operador '{username}'")
            sys.exit(1)
        session.add(
            Operator(
                username=username,
                password_hash=security.hash_password(password),
                role=role_enum,
                is_active=True,
                totp_enrolled=False,
            )
        )
        await record_audit(
            session,
            operator=actor[0],
            role=actor[1],
            action="operator.create",
            result="ok",
            target=username,
            detail={"role": role, "via": "manage.py create-operator"},
        )
        await session.commit()
    print(f"creado: {username} (rol {role})")
    print("Primer login: POST /auth/login con username+password (sin totp_code)")
    print("-> devuelve el otpauth:// URI para cargar en el authenticator.")
    print("Después: POST /auth/login con username+password+totp_code -> sesión.")


async def _reset_operator(username: str, password: str, actor: Actor) -> None:
    async with SessionLocal() as session:
        op = (
            await session.execute(select(Operator).where(Operator.username == username))
        ).scalar_one_or_none()
        if op is None:
            print(f"no existe: {username}")
            sys.exit(1)
        op.password_hash = security.hash_password(password)
        op.totp_secret = None
        op.totp_enrolled = False
        op.totp_last_step = None
        op.token_version += 1  # corta cualquier sesión activa (docs/specs/done/login-hardening.md)
        await record_audit(
            session,
            operator=actor[0],
            role=actor[1],
            action="operator.reset_password",
            result="ok",
            target=username,
            detail={"totp_reset": True, "via": "manage.py reset-operator"},
        )
        await session.commit()
    print(f"{username}: contraseña nueva + TOTP limpio. Re-enrola en el próximo login.")


async def _set_active(username: str, active: bool, actor: Actor) -> None:
    async with SessionLocal() as session:
        op = (
            await session.execute(select(Operator).where(Operator.username == username))
        ).scalar_one_or_none()
        if op is None:
            print(f"no existe: {username}")
            sys.exit(1)
        op.is_active = active
        await record_audit(
            session,
            operator=actor[0],
            role=actor[1],
            action="operator.update",
            result="ok",
            target=username,
            detail={"is_active": active, "via": "manage.py set-active"},
        )
        await session.commit()
    print(f"{username}: {'activo' if active else 'DESHABILITADO'}")


async def _list_operators() -> None:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Operator).order_by(Operator.username))).scalars().all()
    for r in rows:
        flags = "activo" if r.is_active else "DESHABILITADO"
        totp = "totp:sí" if r.totp_enrolled else "totp:pendiente"
        print(f"  {r.username:20} {r.role.value:15} {flags:14} {totp}")
    if not rows:
        print("  (ninguno) — crear el primero con: create-operator <user> platform_admin")


async def _sync_accounts_now() -> None:
    result = await sync_all_tenants_accounts({})
    print(f"sync nocturno corrido a mano: {result['tenants']} tenants procesados")
    print("ver el detalle por tenant en 'verify-audit'/la página Audit (action=account.sync)")


async def _check_alerts_now() -> None:
    result = await check_alert_rules({})
    print(f"chequeo de alertas corrido a mano: {result}")
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AlertState)
                    .where(
                        or_(
                            AlertState.resolved_at.is_(None), AlertState.pending_notify.is_not(None)
                        )
                    )
                    .order_by(AlertState.opened_at)
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        print("sin alertas abiertas ni avisos pendientes de entrega")
    for row in rows:
        if row.resolved_at is None and row.confirmed_at is None:
            state = f"candidata ({row.consecutive_hits} corrida/s, aún sin avisar)"
        elif row.resolved_at is None:
            state = "abierta"
        else:
            state = "resuelta"
        pending = f" | aviso sin entregar: {row.pending_notify}" if row.pending_notify else ""
        print(f"  [{row.rule}] {state}: {row.summary}{pending}")
    print(
        "ver el detalle en la página Audit "
        "(action=alert.rule.opened/.renotified/.resolved/.notify_failed)"
    )


async def _test_alert(kind: str) -> None:
    messages = sample_messages(kind)
    failed = 0
    for message in messages:
        if not await notify.send(message):
            failed += 1
    print(f"{len(messages) - failed}/{len(messages)} aviso(s) de prueba entregados")
    if failed:
        print("alguno falló: revise PAM_ALERTS_TEAMS_WEBHOOK_URL y los logs del worker/api")


async def _export_audit_since(min_id: int) -> None:
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.id > min_id).order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    for r in rows:
        print(
            json.dumps(
                {
                    "original_id": r.id,
                    "original_hash": r.hash,
                    "ts": r.ts.isoformat(),
                    "operator": r.operator,
                    "role": r.role,
                    "tenant_id": r.tenant_id,
                    "action": r.action,
                    "target": r.target,
                    "detail": r.detail,
                    "dry_run": r.dry_run,
                    "result": r.result,
                    "request_id": r.request_id,
                    "source_ip": r.source_ip,
                },
                default=str,
            )
        )
    print(f"exportadas {len(rows)} filas (id > {min_id})", file=sys.stderr)


async def _import_standby_audit(path: str, actor: Actor) -> None:
    with open(path, encoding="utf-8") as f:
        lines = [line for line in (raw.strip() for raw in f) if line]
    if not lines:
        print("archivo vacío, nada para importar")
        return

    async with SessionLocal() as session:
        first = await record_audit(
            session,
            operator=actor[0],
            role=actor[1],
            action="failback.import.start",
            result="ok",
            detail={"source": "pam-standby", "rows": len(lines)},
        )
        await session.commit()
        print(f"marcador de inicio: audit_log id={first.id}")

        imported = 0
        for line in lines:
            row = json.loads(line)
            await record_audit(
                session,
                operator=row["operator"],
                role=row["role"],
                action=row["action"],
                result=row["result"],
                tenant_id=row.get("tenant_id"),
                target=row.get("target"),
                dry_run=row.get("dry_run", False),
                request_id=row.get("request_id"),
                source_ip=row.get("source_ip"),
                ts=datetime.fromisoformat(row["ts"]),
                detail={
                    **(row.get("detail") or {}),
                    "_failback_import": {
                        "from_host": "pam-standby",
                        "original_id": row["original_id"],
                        "original_hash": row["original_hash"],
                    },
                },
            )
            imported += 1
        await session.commit()

        last = await record_audit(
            session,
            operator=actor[0],
            role=actor[1],
            action="failback.import.end",
            result="ok",
            detail={"source": "pam-standby", "rows_imported": imported},
        )
        await session.commit()
        print(f"marcador de fin: audit_log id={last.id}")

    print(f"importadas {imported} filas de {path}")
    print("correr 'verify-audit' para confirmar que la cadena sigue íntegra")


def main() -> None:
    global _OPERATOR_FLAG
    _OPERATOR_FLAG = _pop_operator_flag()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed-tenants":
        asyncio.run(_seed_tenants(_cli_operator()))
    elif cmd == "verify-audit":
        asyncio.run(_verify_audit())
    elif cmd == "check-secrets":
        _check_secrets()
    elif cmd == "set-tenant-secret":
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        flags = {a for a in sys.argv[2:] if a.startswith("--")}
        if len(args) != 1 or not flags <= {"--no-verify"}:
            print("uso: set-tenant-secret <tenant> [--no-verify]")
            sys.exit(2)
        actor = _cli_operator()
        asyncio.run(_set_tenant_secret(args[0], actor, verify="--no-verify" not in flags))
    elif cmd == "check-tenant-login":
        if len(sys.argv) != 3:
            print("uso: check-tenant-login <tenant>")
            sys.exit(2)
        asyncio.run(_check_tenant_login(sys.argv[2]))
    elif cmd == "create-operator":
        if len(sys.argv) < 3:
            print("uso: create-operator <username> [rol]")
            sys.exit(2)
        username = sys.argv[2]
        role = sys.argv[3] if len(sys.argv) > 3 else "viewer"
        actor = _cli_operator(bootstrap_ok=True)
        pw1 = getpass.getpass("contraseña: ")
        pw2 = getpass.getpass("repetir: ")
        if pw1 != pw2:
            print("no coinciden")
            sys.exit(1)
        if len(pw1) < 12:
            print("mínimo 12 caracteres")
            sys.exit(1)
        asyncio.run(_create_operator(username, role, pw1, actor))
    elif cmd == "reset-operator":
        if len(sys.argv) < 3:
            print("uso: reset-operator <username>")
            sys.exit(2)
        actor = _cli_operator()
        pw1 = getpass.getpass("contraseña nueva: ")
        pw2 = getpass.getpass("repetir: ")
        if pw1 != pw2:
            print("no coinciden")
            sys.exit(1)
        if len(pw1) < 12:
            print("mínimo 12 caracteres")
            sys.exit(1)
        asyncio.run(_reset_operator(sys.argv[2], pw1, actor))
    elif cmd == "set-active":
        if len(sys.argv) < 4 or sys.argv[3] not in ("on", "off"):
            print("uso: set-active <username> <on|off>")
            sys.exit(2)
        actor = _cli_operator()
        asyncio.run(_set_active(sys.argv[2], sys.argv[3] == "on", actor))
    elif cmd == "list-operators":
        asyncio.run(_list_operators())
    elif cmd == "demo-setup":
        if not get_settings().demo_mode:
            print("demo-setup sólo corre con PAM_DEMO_MODE=true (tenants ficticios)")
            sys.exit(1)
        username = sys.argv[2] if len(sys.argv) > 2 else "demo"
        actor: Actor = ("demo-setup", "platform_admin")
        asyncio.run(_seed_tenants(actor))
        if asyncio.run(_lookup_operator(None))[0]:
            print("ya hay operadores: no se crea ninguno (use create-operator)")
            return
        pw1 = getpass.getpass(f"contraseña para '{username}' (mínimo 12): ")
        if len(pw1) < 12 or pw1 != getpass.getpass("repetir: "):
            print("no coinciden o tienen menos de 12 caracteres")
            sys.exit(1)
        asyncio.run(_create_operator(username, "platform_admin", pw1, actor))
    elif cmd == "sync-accounts-now":
        asyncio.run(_sync_accounts_now())
    elif cmd == "check-alerts-now":
        asyncio.run(_check_alerts_now())
    elif cmd == "test-alert":
        kind = sys.argv[2] if len(sys.argv) > 2 else "all"
        if kind not in ("all", "component", "partial", "worse", "approval", "vendor", "resolved"):
            print("uso: test-alert [all|component|partial|worse|approval|vendor|resolved]")
            sys.exit(2)
        asyncio.run(_test_alert(kind))
    elif cmd == "export-audit-since":
        if len(sys.argv) < 3 or not sys.argv[2].isdigit():
            print("uso: export-audit-since <id>")
            sys.exit(2)
        asyncio.run(_export_audit_since(int(sys.argv[2])))
    elif cmd == "import-standby-audit":
        if len(sys.argv) < 3:
            print("uso: import-standby-audit <archivo>")
            sys.exit(2)
        asyncio.run(_import_standby_audit(sys.argv[2], _cli_operator()))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
