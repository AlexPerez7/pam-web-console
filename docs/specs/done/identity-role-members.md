# Miembros de un rol de Identity (ver y agregar)

**Estado:** hecha (implementada 2026-09-28, probada contra el tenant de lab el 2026-10-07).
Pedido de Alex: en Identity > Roles, hacer clic en un rol y ver sus
miembros, como la pestaña *Members* de Identity Administration, y poder
agregar miembros desde ahí.

## Alcance

- **Ver miembros** (lectura, `viewer+`): usuarios, grupos y roles del rol.
- **Agregar miembros** (escritura, `operator+`): usuarios y roles de
  Identity, uno o varios por vez. Sólo agrega, nunca quita.
- Fuera de alcance: quitar miembros, agregar grupos de directorio (AD/LDAP),
  editar derechos administrativos o aplicaciones asignadas del rol.

## Diseño

| Pieza | Detalle |
|---|---|
| `client.identity_role_members(role_id)` | `POST {identity}/Roles/GetRoleMembers?name=<id>` → `Result.Results[].Row` (`Guid`/`Name`/`Type`) |
| `client.identity_role_add_members(role_id, users, roles)` | `POST {identity}/Roles/UpdateRole` con `{"Name": <id>, "Users": {"Add": [...]}, "Roles": {"Add": [...]}}` |
| `GET /tenants/{id}/identity-roles/{role_id}/members` | `viewer+`, auditado `identity_role.members` |
| `GET /tenants/{id}/identity-roles/{role_id}/candidates?q=` | usuarios y roles de Identity vía Redrock (término saneado), marca los que ya son miembros; auditado `identity_principal.search` |
| `POST /tenants/{id}/identity-roles/{role_id}/members` | `writeop.run_write` (dry-run + razón + audit), feature `identity_role.member.add`, `operator+` |
| `app/domain/identity_roles.py` | plan puro: omite los que ya son miembros, avisa si el rol parece de administración o si se agrega un rol anidado |
| `IdentityRoleDetailPanel.tsx` / `IdentityRoleMemberAdd.tsx` | panel del rol + buscador con selección múltiple → `DryRunDialog` |

**RBAC:** `operator+`, mismo criterio que `safe.member.add` (aditivo y
reversible). El plan avisa cuando el nombre del rol sugiere administración
(`admin`/`auditor`), porque ahí agregar a alguien es una escalada de
privilegios.

## Confirmado contra un tenant real (2026-10-07, lab `lab`)

Los tres puntos de abajo quedaron confirmados sin ajustes de código: la
lista de `Lab_Admins` coincide con la pestaña *Members* de Identity
Administration (9 usuarios), y agregar un usuario a un rol funcionó y se
vio en Identity Administration.

- Forma exacta de la respuesta de `GetRoleMembers` (`Guid`/`Name`/`Type`).
- Que `UpdateRole` acepte IDs en `Users.Add`/`Roles.Add` y no toque el resto
  del rol.
- Permiso del service account para modificar roles en Identity (si falta,
  CyberArk responde error y se muestra tal cual).
