"""Tests de la cadena de hashes de app.core.audit.

Sin Postgres: el trigger anti UPDATE/DELETE (migracion 0002) se prueba en
contract. Aca se valida la logica del hash encadenado.

Nota: se mantienen referencias fuertes a las filas devueltas por `record()`.
El identity map de SQLAlchemy es de referencias debiles; si la fila se
recolecta, `verify_chain` la recarga de SQLite, que descarta el tzinfo del
timestamp y cambiaria el hash recalculado (en Postgres el `DateTime(timezone=
True)` conserva la zona, por eso no pasa en produccion).
"""

from __future__ import annotations

from app.core import audit


async def test_record_encadena_hashes(session):
    a = await audit.record(
        session, operator="alex", role="viewer", action="account.read", result="ok"
    )
    b = await audit.record(session, operator="alex", role="viewer", action="safe.read", result="ok")

    assert a.prev_hash is None
    assert a.hash
    assert b.prev_hash == a.hash
    assert b.hash != a.hash

    v = await audit.verify_chain(session)
    assert (v.intact, v.first_broken_id, v.checked) == (True, None, 2)
    assert (v.first_id, v.last_id) == (a.id, b.id)


async def test_verify_chain_detecta_tampering(session):
    a = await audit.record(
        session, operator="alex", role="viewer", action="account.read", result="ok"
    )
    b = await audit.record(
        session,
        operator="alex",
        role="operator",
        action="account.update",
        result="ok",
        detail={"before": {"name": "x"}, "after": {"name": "y"}},
    )

    v = await audit.verify_chain(session)
    assert v.intact is True  # cadena valida antes de tocar nada

    # se altera el contenido sin recalcular el hash -> la cadena se rompe
    b.detail = {"before": {"name": "x"}, "after": {"name": "HACKEADO"}}

    v = await audit.verify_chain(session)
    assert v.intact is False
    assert v.first_broken_id == b.id
    assert v.checked == 2  # se detectó en la segunda fila
    assert a.id != b.id  # mantiene viva la referencia a la primera fila
