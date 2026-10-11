# platform-config-viewer

- **Estado:** hecha (probada contra Globex el 2026-10-07)
- **Roadmap:** extra de Plataformas, a pedido de Alex
- **Fecha:** 2026-09-29

## Problema

En el PVWA, "Editar" una plataforma abre `editor.aspx`, un árbol con la
configuración completa: "UI & Workflows" (Properties, Linked Accounts,
Usages, Ticketing System, Privileged Session Management, Connection
Components) y "Automatic Password Management" (General, CPM Plug-in,
Password Change/Verification/Reconciliation, Notifications, Generate
Password). En PAM Web Console sólo se veía el resumen de `GET /API/Platforms`, sin los
parámetros del CPM. Para revisar qué hace una plataforma había que entrar
al PVWA clásico.

`editor.aspx` no se puede llamar desde PAM Web Console: el parámetro `Data` de la URL
es un identificador de la sesión del navegador del operador (decodificado:
`<guid>^@^BackURL=…`), y la página se autentica con cookies, no con
`Authorization: Bearer`. Es el mismo bloqueo que `AccountsMgt.asmx/Move` y
`ReportsMgt.asmx/LaunchReport`.

## Alcance

Entra:
- Ver la configuración completa de una plataforma, con el mismo contenido
  que el árbol del PVWA y un diseño propio de PAM Web Console (navegación lateral,
  nombres y descripciones en español, búsqueda sobre todas las secciones).
- La fuente es el paquete de "Export Platform" (`.zip` con `Policy-<id>.xml`
  + `Policy-<id>.ini`), que PAM Web Console ya sabe descargar (`client.export_platform`).

Queda afuera:
- **Editar.** La API REST de CyberArk no permite modificar los parámetros del
  CPM ni el XML de interfaz de una plataforma existente (sólo la parte de
  PSM, que PAM Web Console ya edita con `platform.psm.update`). El único camino sería
  exportar → modificar → importar como plataforma NUEVA → mover las cuentas,
  que reemplaza en vez de editar; no se hace. Para editar sigue el PVWA.
- Comparar dos plataformas lado a lado (posible fast-follow, reusa el
  mismo endpoint).
- Caché: se pide en vivo cada vez que se abre la pestaña (el frontend
  retiene el resultado 5 minutos con TanStack Query).

## Requisitos

1. **Árbol completo.** Dado un operador `viewer+` sobre el tenant, cuando
   abre la pestaña "Configuración" del detalle de una plataforma, entonces
   ve dos grupos — "Interfaz y flujos" (el `.xml`) y "Gestión automática de
   contraseñas" (el `.ini`) — con las mismas secciones que el PVWA.
2. **Parámetros legibles.** Cada parámetro muestra su nombre real (el mismo
   del PVWA, para no perder la correspondencia), el valor (Sí/No con ícono,
   números en monoespaciado) y, si se conoce, una descripción en español;
   si no, el comentario que trae el propio `.ini`.
3. **Búsqueda.** Cuando el operador escribe en el buscador, entonces ve los
   parámetros que coinciden (por nombre, valor o descripción) de todas las
   secciones, agrupados con su ruta, y la navegación marca cuántos hay en
   cada sección.
4. **Tolerancia.** Si uno de los dos archivos del paquete no se puede leer,
   el otro se muestra igual, con un aviso. Si el paquete no es un `.zip`,
   502 con un mensaje claro.
5. **Auditado.** Cada consulta queda en `audit_log` como
   `platform.config.view` (ok o error), atribuida al operador real.
6. **Sólo lectura.** Ninguna escritura contra CyberArk.

## Diseño técnico

- `backend/app/domain/platform_config.py` (puro): `parse_platform_package`
  (zip → `{files, tree, errors}`), `parse_ini`, `parse_xml`.
  - `.ini`: las claves sueltas se agrupan como en el PVWA por nombre/prefijo
    (`VF*` verificación, `RC*` reconciliación, `NF*` notificaciones,
    `DllName`/`XMLFile` plug-in, `PasswordLength`/`Min*` generación, el
    resto General). Las secciones explícitas (`[ExtraInfo]`, …) cuelgan del
    plug-in. Comentario inline = texto después de ` ;` (un `;` pegado al
    valor es parte del valor). Decodifica UTF-16 con BOM, UTF-8 o latin-1.
  - `.xml`: parseo **genérico** (no hay esquema oficial del paquete):
    atributos = filas, hijos = sub-secciones; los hijos hoja de un
    contenedor-lista (todos del mismo tipo: `Property`, `Link`,
    `ConnectionComponent`…) se vuelven filas. Se saltean los envoltorios
    `Device`/`Policies`. `xml.etree` sin `defusedxml`: el XML viene de
    CyberArk por un canal autenticado y el `expat` de Python 3.12 no
    resuelve entidades externas.
  - Tope de 5 MB por archivo descomprimido.
- `GET /tenants/{id}/platforms/{platform_id}/config` en
  `app/api/inventory.py` — `viewer+` (mismo criterio que `.../export`),
  auditado vía `_run`. `platform.config.view` agregado a
  `app/core/audit_catalog.py`.
- Frontend: `components/PlatformConfigExplorer.tsx` (navegación lateral +
  parámetros + búsqueda con resaltado), en la pestaña "Configuración" de
  `PlatformDetailPanel.tsx`, que se ensancha a `max-w-5xl` en esa pestaña.
  Reemplaza la vista anterior de esa pestaña (sólo los campos de
  `GET /API/Platforms`, ahora cubiertos por el `.ini`/`.xml`).

## Tareas

- [x] Parser puro + tests (`tests/unit/test_platform_config.py`).
- [x] Endpoint + auditoría + catálogo de acciones.
- [x] Explorador en el frontend.
- [x] Probar contra Globex: confirmar la forma real del `.xml` y el
      encoding del `.ini`; ajustar las etiquetas/agrupación si algo cae en
      "General" sin corresponder. Probado el 2026-10-07 con una plataforma
      Windows, una Unix SSH y una MSSql: agrupación correcta y `.ini`
      bien decodificado, sin ajustes de código.
