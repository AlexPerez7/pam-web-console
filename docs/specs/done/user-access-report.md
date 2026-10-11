# Spec — Reporte "Accesos de un usuario"

**Estado:** cerrada — implementada (2026-09-28), probada contra Globex (2026-09-30).
**Reporte:** `user-access` (categoría Safes, `heavy=True`).

## Problema

Pedido real de Globex: "qué safes tiene asignado el usuario
`jdoe@globex.example` y todos los targets que estaría viendo (user -
target)".

Con lo que había, la única forma era filtrar "Auditoría de permisos de
safes" por el correo y después buscar las cuentas safe por safe en el
inventario. Eso sólo muestra la membresía **directa**; en Privilege Cloud
lo habitual es que el usuario llegue al safe a través de un **rol de
Identity** o de un **grupo** que es miembro, y esos accesos quedaban
afuera.

## Alcance

- Un reporte nuevo con un parámetro obligatorio: el usuario.
- Fuera de alcance: roles anidados (un rol dentro de otro), grupos de
  directorio (AD) agregados como miembros sin pasar por un rol de Identity,
  y reportar varios usuarios a la vez.

## Requisitos

### R1 — Safes asignados y por qué vía

- Un safe entra si entre sus miembros figura el usuario, uno de sus roles
  de Identity o uno de sus grupos del Vault. La comparación de nombres no
  distingue mayúsculas.
- Columna "Acceso por": `Directo`, `Rol de Identity: X` o `Grupo: X`. Si
  llega por varias vías, se listan todas.
- Permisos que importan para la pregunta del cliente: Listar cuentas,
  Conectarse (Use), Ver contraseña, Administrar safe. Si llega por varias
  vías, se suman (OR): es lo que efectivamente puede hacer.

### R2 — Targets de cada safe

- Una fila por cuenta del safe (cuenta, usuario, address, plataforma).
- Un safe sin cuentas aparece igual, con esas columnas vacías.
- Cada fila repite el usuario consultado (columna "Usuario consultado"), para que el CSV exportado diga de quién son los accesos. En pantalla, arriba de la tabla, se muestra el reporte, el tenant y los parámetros con que se generó el resultado.

### R3 — Degradación

- Si no se pueden obtener los roles de Identity (usuario no encontrado,
  permiso faltante), el reporte sigue con la membresía directa y los
  grupos, y lo avisa en la meta (`aviso`).
- Un safe cuyos miembros no se pueden consultar se omite, sin abortar el
  resto.

### R4 — Seguridad

Sólo lectura, sin secretos. Rol `viewer+`, auditado como
`report.user-access` con el usuario consultado en los parámetros.

## Diseño técnico

- `app/cyberark/client.py`:
  - `identity_find_user(username)`: Redrock
    `SELECT ID, Username, DisplayName FROM User WHERE Username = '…'
    COLLATE NOCASE`. El nombre se sanea antes de entrar al SQL (Redrock no
    tiene binds).
  - `identity_user_roles(user_id)`:
    `POST {identity}/UserMgmt/GetUsersRolesAndAdministrativeRights?id=`,
    el mismo endpoint que usa `Get-IDUserRole` del módulo `IdentityCommand`.
    El nombre del rol se lee de `Row.Name`. **Pendiente confirmar contra
    Globex.**
- Grupos del Vault: `groupsMembership` de `GET /API/Users`, igual que
  `license-candidates`.
- `app/reports/user_access.py`: `match_memberships` y `compute_user_access`
  (puras) + `_run` (roles, usuarios, safes y cuentas en paralelo; después
  un `safe_members` por safe).
- Parámetro de tipo `user` (nuevo en `app/reports/base.py`, viaja igual
  que un `text`): en el frontend es `components/UserSearchField.tsx`, un
  buscador con autocompletado. Se escribe parte del nombre o del correo
  ("jdoe") y lista coincidencias de Identity y del Vault, reusando
  `GET /tenants/{id}/member-search?kind=user` (el buscador de "Agregar
  miembro a safe", ya auditado como `member.search`). Espera 300 ms entre
  teclas y al menos 2 letras antes de buscar.

## Tareas

- [x] `identity_find_user` + `identity_user_roles` en el cliente
- [x] Reporte `user-access` registrado en `REGISTRY`
- [x] Tests (`tests/unit/test_user_access.py`)
- [x] Probar contra Globex con `jdoe@globex.example`: confirmar que
      los roles de Identity se resuelven (campo `Name`) y que coinciden con
      los nombres de miembro de los safes
