# tenant-credential-from-vault

- **Estado:** propuesta (pendiente de revisión; hay preguntas abiertas)
- **Roadmap:** mejora operativa transversal (secret store de tenants, `docs/PLAN.md` §5.3)
- **Fecha:** 2026-10-05

## Problema

PAM Web Console guarda una **copia fija** de la contraseña del service user de cada
tenant en `secrets/secrets.age`. Si esa contraseña la rota otro (el CPM de
Privilege Cloud, o la política del cliente), la copia queda vieja y el tenant
deja de responder: es lo que pasa con Umbrella, cuya contraseña rota a diario.
Peor: mientras la copia está vieja, cada consulta y cada job suman intentos
fallidos y pueden **bloquear al service user** en el Identity del cliente.
`manage.py set-tenant-secret` (`docs/runbooks/rotate-service-account.md`)
reduce la rotación manual a un comando, pero con rotación diaria sigue siendo
una tarea diaria.

## Alcance

**Entra:** que un tenant pueda declarar que su credencial **se lee de una
cuenta de una bóveda de Privilege Cloud** en lugar de `secrets.age`; PAM Web Console la
recupera cuando la necesita, la cachea poco tiempo y, si el login falla, la
vuelve a pedir **una sola vez**. Un freno contra el bloqueo del service user.

**Queda afuera:** OpenBao/otro secret store; la clave privada de Alero; rotar
la credencial de la bóveda origen (esa sigue en `secrets.age`); escribir de
vuelta en la bóveda; tenants sin cuenta en una bóveda accesible por PAM Web Console.

## Requisitos

1. **Configuración por tenant.** Un tenant puede tener
   `credential_source = {source_tenant_id, account_id}`. Sin esto, todo sigue
   como hoy (`secrets.age`). Solo `platform_admin` la define, y no viaja por
   `seed/tenants.json` si incluye algo sensible (los ids no lo son).
   *Dado* el tenant `umbrella` con origen `lab`/cuenta `123_4`, *cuando*
   PAM Web Console necesita autenticarse contra Umbrella, *entonces* obtiene el
   `client_secret` de esa cuenta y no el de `secrets.age`.
2. **Obtención.** Con el cliente del tenant origen (su propio service user, que
   no rota) se llama a `retrieve_password` (`app/cyberark/client.py`). Se
   conserva el `client_id` de `secrets.age`; solo cambia el `client_secret`.
3. **Caché corta en memoria** por proceso (TTL a definir según la frecuencia de
   rotación, hoy se asume 5–15 min). El token OAuth ya se cachea aparte
   (`app/cyberark/auth.py`) y su clave incluye el hash del secret: una
   contraseña nueva invalida el token anterior sola.
4. **Reintento único.** *Dado* que el login contra Identity falla
   (`access_denied`/400 o 401) con el valor cacheado, *cuando* se pide de
   nuevo a la bóveda y el valor **es distinto**, *entonces* se reintenta una
   vez. Si el valor es igual (la bóveda aún no sincronizó), **no** se
   reintenta: se falla sin sumar otro intento.
5. **Freno anti-bloqueo.** Tras N fallos de autenticación seguidos para un
   tenant (N pequeño, ej. 3) se suspenden los intentos por T minutos (ej. 15)
   y el tenant se marca con error de credencial, salvo que el valor de la
   bóveda cambie. Aplica también a las credenciales de `secrets.age`: hoy
   nada frena los reintentos contra una contraseña mala.
6. **Auditoría.** Cada recuperación contra la bóveda (no cada uso desde caché)
   deja `tenant.credential.fetch` con tenant, origen, resultado y **nunca el
   valor**. Los cortes del freno quedan como `tenant.credential.suspended`.
7. **Degradación.** Si la bóveda origen no responde, se usa el último valor
   bueno en memoria; si no hay ninguno, se usa el de `secrets.age` y se marca
   advertencia en System Health. Nunca se inventa un valor.
8. **Visibilidad.** System Health muestra por tenant de dónde viene la
   credencial (`secrets.age` o bóveda), cuándo se obtuvo y si el freno está
   activo.

## Diseño técnico

- `app/core/secrets.py` conserva `get_secret(ref)` para la parte estática. Una
  capa nueva (`app/core/tenant_credentials.py`) resuelve la credencial final:
  estática o desde la bóveda, con la caché, el reintento y el freno.
- Los llamadores de `get_secret(tenant.secret_ref)` (`app/core/tenants.py`,
  jobs `inventory`/`alerts`/`reports`, `search`, `system_health`) pasan a la
  capa nueva. Hoy son 6 sitios.
- **Sin círculo:** el tenant origen (ej. `lab`) usa su propio service
  user, que no rota. Si el origen también rotara, esto no sirve.
- **Permiso mínimo:** ese service user necesita `Retrieve` (y `List`) **solo**
  sobre la cuenta, idealmente en un safe propio. Es el riesgo principal: PAM Web Console
  pasa a poder recuperar una contraseña privilegiada de forma automática. Se
  mitiga con el alcance de un solo safe, la auditoría de cada recuperación y
  el RBAC de configuración (`platform_admin`).
- **Plataforma de la cuenta:** la recuperación automática falla si el safe o
  la plataforma exigen **doble control**, **contraseña de un solo uso** o
  **acceso exclusivo**. Hay que revisarlo antes de empezar (pregunta 2).

## Preguntas abiertas

1. ¿La cuenta `umbrella_admin@...` está en la bóveda **del MSP** (nuestro
   tenant `lab`) o en la propia de Umbrella? Si es la de Umbrella, hace
   falta un acceso de recuperación en su tenant y una conversación con el
   cliente.
2. ¿El safe o la plataforma de esa cuenta tienen doble control, contraseña de
   un solo uso o acceso exclusivo?
3. ¿Cada cuánto rota de verdad (diaria, a qué hora)? Define el TTL de la
   caché.
4. ¿Seguridad acepta que PAM Web Console tenga permiso de recuperación sobre esa cuenta?
5. ¿Hay otros tenants en la misma situación (Acme, Globex, Initech)?

## Tareas

- [ ] Resolver las preguntas abiertas y pasar la spec a "aprobada".
- [ ] Migración: columnas/JSON `credential_source` en `tenants`.
- [ ] `app/core/tenant_credentials.py`: obtención, caché, reintento único.
- [ ] Freno anti-bloqueo (también para `secrets.age`).
- [ ] Auditoría (`tenant.credential.fetch` / `.suspended`).
- [ ] Migrar los 6 llamadores de `get_secret`.
- [ ] System Health: origen y estado de la credencial.
- [ ] Tests (rotación entre dos consultas, valor igual sin reintento, freno,
      bóveda caída, no se loguea el valor).
- [ ] Runbook: alta de un origen y permisos mínimos en la bóveda.
- [ ] Probar contra el lab (`lab`) antes de tocar Umbrella.
