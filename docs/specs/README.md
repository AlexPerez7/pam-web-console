# Specs

Una spec por feature del roadmap. Se escribe y aprueba **antes** de codear
(ver `CLAUDE.md § Flujo de specs`).

- `docs/specs/<feature>.md` — specs en curso o pendientes de implementar.
- `docs/specs/done/<feature>.md` — specs ya implementadas y probadas en el
  server (se mueven aquí al cerrar).

## Formato

```markdown
# <feature>

- **Estado:** propuesta | aprobada | en curso | hecha
- **Roadmap:** Fase N, ítem "<…>"
- **Fecha:** YYYY-MM-DD

## Problema
Qué falta o qué duele hoy. 2-4 frases.

## Alcance
Qué entra en esta spec y qué queda explícitamente afuera.

## Requisitos
Numerados. Cada uno con al menos un escenario concreto (Dado / Cuando /
Entonces) o un ejemplo de request/response.

## Diseño técnico
Endpoints, modelos, archivos que se tocan, decisiones y trade-offs.
Respetar los principios no negociables de `CLAUDE.md` (read-only por
defecto, todo auditado, aislamiento por tenant).

## Tareas
- [ ] …
```
