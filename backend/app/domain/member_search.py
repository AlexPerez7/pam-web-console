"""Búsqueda de principales para "agregar miembro a una safe".

Puro: combina lo que devuelven las 3 fuentes (usuarios del Vault, grupos del
Vault, usuarios de Identity vía Redrock) en una lista de candidatos
deduplicada. El fan-out y las llamadas viven en `app.api.inventory`.
Ver `docs/specs/safe-member-add.md`.
"""

from __future__ import annotations

from typing import Any

# `source` del Vault que representa cuentas internas (no de directorio)
_VAULT_INTERNAL = {"", "vault", "cyberark"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _vault_search_in(source: str) -> str:
    """`searchIn` para un usuario/grupo del Vault: 'Vault' si es interno, si no
    el nombre del directorio."""
    return "Vault" if source.lower() in _VAULT_INTERNAL else source


def merge_member_candidates(
    vault_users: list[dict],
    vault_groups: list[dict],
    identity_users: list[dict],
    *,
    existing_members: set[str],
    kind: str = "any",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """`existing_members` = nombres (lower) ya miembros de la safe destino."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}

    def add(cand: dict[str, Any]) -> None:
        name = cand["name"]
        if not name:
            return
        key = (name.lower(), cand["kind"])
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = cand
            return
        # merge: completa displayName/email si faltan, prioriza searchIn del Vault
        for f in ("displayName", "email"):
            if not prev.get(f) and cand.get(f):
                prev[f] = cand[f]
        if cand["source"] == "Vault" and prev["source"] != "Vault":
            prev["source"] = "Vault"
            prev["searchIn"] = "Vault"

    if kind in ("any", "user"):
        for u in vault_users:
            source = _norm(u.get("source"))
            add(
                {
                    "name": _norm(u.get("username") or u.get("userName")),
                    "displayName": _norm(
                        " ".join(
                            filter(
                                None,
                                [
                                    _norm((u.get("personalDetails") or {}).get("firstName")),
                                    _norm((u.get("personalDetails") or {}).get("lastName")),
                                ],
                            )
                        )
                    ),
                    "email": _norm((u.get("personalDetails") or {}).get("email")),
                    "kind": "user",
                    "source": "Vault" if source.lower() in _VAULT_INTERNAL else source,
                    "searchIn": _vault_search_in(source),
                    "alreadyMember": False,
                }
            )

    if kind in ("any", "group"):
        for g in vault_groups:
            directory = _norm(g.get("directory") or g.get("Directory"))
            add(
                {
                    "name": _norm(g.get("groupName") or g.get("GroupName") or g.get("name")),
                    "displayName": "",
                    "email": "",
                    "kind": "group",
                    "source": directory or "Vault",
                    "searchIn": directory or "Vault",
                    "alreadyMember": False,
                }
            )

    if kind in ("any", "user"):
        for r in identity_users:
            source = _norm(r.get("SourceDs") or r.get("SourceDsType"))
            add(
                {
                    "name": _norm(r.get("Username") or r.get("LoginName")),
                    "displayName": _norm(r.get("DisplayName")),
                    "email": _norm(r.get("Email")),
                    "kind": "user",
                    "source": source or "Identity",
                    "searchIn": source or "Vault",
                    "alreadyMember": False,
                }
            )

    out = list(by_key.values())
    for c in out:
        c["alreadyMember"] = c["name"].lower() in existing_members
    # los que ya son miembros al final; el resto por tipo y nombre
    out.sort(key=lambda c: (c["alreadyMember"], c["kind"], c["name"].lower()))
    return out[:limit]


def sanitize_search_term(term: str) -> str:
    """Deja sólo alfanumérico + unos pocos símbolos — el término entra crudo en
    el SQL de Redrock (no hay parámetros bind)."""
    return "".join(c for c in term if c.isalnum() or c in " ._-@").strip()


def redrock_user_search_sql(term: str) -> str:
    """SELECT de Identity por username o display name. `term` ya viene saneado
    (`sanitize_search_term`) — sin binds en Redrock."""
    like = f"%{term}%"
    return (
        "SELECT ID, Username, DisplayName, Email, SourceDs, SourceDsType FROM User "  # noqa: S608
        f"WHERE Username LIKE '{like}' OR DisplayName LIKE '{like}' "
        "ORDER BY Username COLLATE NOCASE LIMIT 50"
    )
