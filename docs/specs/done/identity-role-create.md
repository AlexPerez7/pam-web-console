# Crear roles de Identity (botón "Agregar rol")

- **Estado:** hecha — implementada y probada contra el tenant de lab el 2026-10-02
- **Roadmap:** Identity, ítem "CRUD de `roles-api`" (quedó fuera del primer corte, ver `docs/plan/identity.md`)
- **Fecha:** 2026-10-02

## Problema

En Identity > Roles, PAM Web Console lista los roles y permite ver y agregar miembros
(`docs/specs/identity-role-members.md`), pero no crear uno nuevo: había que
entrar a Identity Administration > Roles > *Add Role* en el portal de
CyberArk. Alex lo pidió el 2026-10-02 al compararlo con el PVWA, con una
captura del diálogo *Add Role*: **Name** (obligatorio), **Description**,
**Organization** (sólo lectura, vacío) y **Role Type** (Static por defecto).

## Alcance

Entra (lo mismo que el diálogo del portal):
- Botón **Agregar rol** en la página Roles, con un diálogo: nombre,
  descripción, organización (sólo lectura, "—") y tipo de rol (sólo
  **Static**; *Dynamic* aparece deshabilitado).
- Crea un rol **estático y vacío**: los miembros se agregan después desde el
  panel del rol (`identity_role.member.add`, ya existente).
- `dry-run` + diff + razón obligatoria, auditado (`identity_role.create`).

Queda afuera (posibles specs propias):
- Roles **dinámicos** (`Script`).
- Derechos administrativos del rol (`AssignSuperRights`) y aplicaciones
  asignadas.
- Miembros iniciales al crear (el portal tampoco los pide en ese diálogo).
- Editar nombre/descripción, **quitar miembros** y **borrar** un rol.
- Roles ligados a una organización (la columna sale vacía en los tenants
  actuales).

## Requisitos

1. **Crear un rol.** Dado un operador `operator+` en el tenant, cuando
   completa el nombre (obligatorio) y la descripción (opcional) y confirma el
   dry-run con una razón (≥ 8 caracteres), entonces el rol aparece en la
   lista y el audit registra `identity_role.create` con el operador real. El
   nombre se guarda sin espacios al inicio/final.
2. **Nombre duplicado.** Dado un rol existente con ese nombre (sin distinguir
   mayúsculas), cuando se intenta crear otro, entonces el plan queda **sin
   efecto** con el aviso "ya existe un rol de Identity llamado '…'" y no se
   llama a CyberArk para crear (mismo criterio que "ya es miembro").
3. **Nombre en blanco** o sólo espacios: `422`.
4. **Aviso de nombre privilegiado.** Cuando el nombre sugiere administración
   (`admin`/`auditor`/`sysadmin`), el plan advierte que el rol se crea sin
   derechos ni miembros.
5. **Sin derechos por defecto.** Un rol creado por PAM Web Console no recibe derechos
   administrativos ni aplicaciones; el diff y el efecto lo aclaran.
6. **Rol insuficiente.** Un `viewer` recibe `403`.

## Diseño técnico

| Pieza | Detalle |
|---|---|
| `client.create_identity_role(name, description)` | `POST {identity}/Roles/StoreRole` con `{"Name", "Description"}` — endpoint y body confirmados contra el SDK oficial `ark-sdk-golang`; `retry_safe=False` (un timeout puede haber creado el rol). Devuelve `{id, name}` |
| `POST /tenants/{id}/identity-roles` | `writeop.run_write` (dry-run + razón + audit), feature `identity_role.create`, `operator+`. El `fetch` lista los nombres de rol (`SELECT Name FROM Role`) para detectar el duplicado |
| `app/domain/identity_roles.py` | `plan_identity_role_create(current, changes)`: duplicado → no-op con aviso; si no, diff (nombre, descripción, tipo `Static`) y aviso de nombre privilegiado |
| `IdentityRoleCreateDialog.tsx` | botón + diálogo, envía por `DryRunDialog`; invalida la lista `["identity-roles", tenant]` al terminar |
| `IdentityRoles.tsx` | pasa el diálogo al `toolbar` del `DataTable` (visible desde `operator+`) |
| `app/core/audit_catalog.py` | `identity_role.create` registrado, con su traducción ("Creó un rol de Identity") |
| `tests/unit/test_identity_role_create.py` | plan, dry-run sin tocar CyberArk, ejecución con nombre recortado, sin descripción, duplicado, nombre en blanco, sin razón, rol `viewer` |

**RBAC:** `operator+`. Crear un rol sin derechos ni apps no concede nada por
sí solo (es aditivo y reversible a mano en Identity), el mismo criterio de
`identity_role.member.add`. Si se prefiere más freno, subir a `approver+` en
`identity_role_create` (`app/api/writes.py`).

**Principios de `CLAUDE.md`:** gating por rol (sin feature-flag), todo
auditado, `dry-run` + diff, aislamiento por tenant (cliente nuevo por
request).

## Confirmado contra el tenant de lab (2026-10-02)

- `Roles/StoreRole` con `{"Name", "Description"}` crea el rol; el id sale de
  `Result._RowKey` (`{id, name}`) y el rol aparece en la lista con
  `RoleType: PrincipalList` (**Static**).
- **Permiso:** el service user necesita el derecho administrativo de **Role
  Management** en Identity (agregado a un rol del service user). Sin él,
  CyberArk responde "You are not authorized to perform this operation" y el
  mensaje se muestra tal cual. Crear roles con un service user más amplio de
  lo que PAM Web Console usa en el resto es una decisión de mínimo privilegio: probado
  primero en el lab, evaluar caso por caso en un cliente.
- Un service user sin roles de Privilege Cloud autentica (OAuth) pero
  Privilege Cloud responde 302; ver `docs/ESTADO_ACTUAL.md` (2026-10-02).

## Tareas

- [x] `client.create_identity_role` (ya existía de una sesión anterior).
- [x] `plan_identity_role_create` con duplicado y aviso de nombre privilegiado + tests.
- [x] `POST /tenants/{id}/identity-roles` en `writes.py` + `audit_catalog` + tests.
- [x] `IdentityRoleCreateDialog.tsx` + botón en `IdentityRoles.tsx`.
- [x] Probar contra un tenant real (lab) y cerrar la spec
      (`docs/specs/done/`) + actualizar `docs/PLAN.md` y
      `docs/plan/identity.md`.
