"""Secret store: recarga al cambiar el archivo y edición de UN campo.

`age` no está en el runner del CI, así que se reemplaza `_run_age` por un
doble que "cifra" y "descifra" en claro: se prueba la lógica de PAM Web Console (cuándo
se vuelve a descifrar, qué se escribe, con qué permisos), no el binario.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import secrets as store

SEED = {
    "globex": {"client_id": "k@x", "client_secret": "viejo-k"},
    "umbrella": {"client_id": "a@x", "client_secret": "viejo-a"},
}


@pytest.fixture
def fake_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    secrets_file = tmp_path / "secrets.age"
    secrets_file.write_text(json.dumps(SEED))
    os.chmod(secrets_file, 0o640)
    key_file = tmp_path / "age.key"
    key_file.write_text("fake")

    monkeypatch.setattr(
        store,
        "get_settings",
        lambda: SimpleNamespace(secrets_file=str(secrets_file), secrets_age_key=str(key_file)),
    )
    calls = SimpleNamespace(decrypts=0)

    def fake_run_age(cmd: list[str], *, stdin: bytes | None = None) -> bytes:
        if cmd[0] == "age-keygen":
            return b"age1fakerecipient\n"
        if "--decrypt" in cmd:
            calls.decrypts += 1
            return Path(cmd[-1]).read_bytes()
        # cifrar: age -r <recipient> -o <salida> (datos por stdin)
        assert stdin is not None
        Path(cmd[cmd.index("-o") + 1]).write_bytes(stdin)
        return b""

    monkeypatch.setattr(store, "_run_age", fake_run_age)
    store.reload()
    yield SimpleNamespace(path=secrets_file, calls=calls)
    store.reload()


def test_descifra_una_sola_vez_mientras_el_archivo_no_cambia(fake_store):
    store.get_secret("globex")
    store.get_secret("umbrella")
    store.get_secret("globex")
    assert fake_store.calls.decrypts == 1


def test_recarga_sola_cuando_el_archivo_cambia(fake_store):
    assert store.get_secret("umbrella")["client_secret"] == "viejo-a"

    nuevo = {**SEED, "umbrella": {"client_id": "a@x", "client_secret": "nuevo-a"}}
    fake_store.path.write_text(json.dumps(nuevo))
    # el tamaño distinto basta, pero se fuerza también la fecha para no
    # depender de la resolución del sistema de archivos
    st = fake_store.path.stat()
    os.utime(fake_store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    assert store.get_secret("umbrella")["client_secret"] == "nuevo-a"
    assert fake_store.calls.decrypts == 2


def test_update_cambia_solo_ese_campo_y_deja_copia_previa(fake_store):
    prev = store.update_secret_field("umbrella", "client_secret", "nuevo-a")

    assert json.loads(fake_store.path.read_text()) == {
        "globex": SEED["globex"],
        "umbrella": {"client_id": "a@x", "client_secret": "nuevo-a"},
    }
    assert prev.name == "secrets.age.prev"
    assert json.loads(prev.read_text()) == SEED
    # sin restos del archivo temporal
    assert not list(fake_store.path.parent.glob(".*.tmp"))


@pytest.mark.skipif(sys.platform == "win32", reason="los bits de permisos son POSIX")
def test_update_conserva_los_permisos_del_archivo(fake_store):
    store.update_secret_field("globex", "client_secret", "otro")
    assert stat.S_IMODE(fake_store.path.stat().st_mode) == 0o640


def test_set_secret_entry_agrega_entrada_nueva_sin_tocar_las_demas(fake_store):
    prev = store.set_secret_entry("extra", {"url": "https://o/jsonrpc", "token": "t"})

    assert json.loads(fake_store.path.read_text()) == {
        **SEED,
        "extra": {"url": "https://o/jsonrpc", "token": "t"},
    }
    assert json.loads(prev.read_text()) == SEED
    # se ve enseguida, sin reiniciar
    assert store.get_secret("extra")["token"] == "t"


def test_set_secret_entry_reemplaza_la_entrada_completa(fake_store):
    store.set_secret_entry("extra", {"url": "a", "token": "viejo"})
    store.set_secret_entry("extra", {"url": "b"})
    assert store.get_secret("extra") == {"url": "b"}  # reemplazo completo, no merge


def test_update_se_ve_enseguida_sin_reiniciar(fake_store):
    assert store.get_secret("globex")["client_secret"] == "viejo-k"
    store.update_secret_field("globex", "client_secret", "nuevo-k")
    assert store.get_secret("globex")["client_secret"] == "nuevo-k"


def test_update_rechaza_tenant_o_campo_inexistente(fake_store):
    with pytest.raises(store.SecretStoreError, match="no está en el secret store"):
        store.update_secret_field("nadie", "client_secret", "x")
    with pytest.raises(store.SecretStoreError, match="no tiene el campo"):
        store.update_secret_field("globex", "no_existe", "x")
    # no tocó el archivo
    assert json.loads(fake_store.path.read_text()) == SEED


def test_update_no_reemplaza_si_el_cifrado_nuevo_no_verifica(
    fake_store, monkeypatch: pytest.MonkeyPatch
):
    original = store._run_age

    def corrupto(cmd: list[str], *, stdin: bytes | None = None) -> bytes:
        if "-o" in cmd:  # cifrar: deja un contenido distinto al pedido
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps({"roto": {}}))
            return b""
        return original(cmd, stdin=stdin)

    monkeypatch.setattr(store, "_run_age", corrupto)
    with pytest.raises(store.SecretStoreError, match="no se reemplazó nada"):
        store.update_secret_field("globex", "client_secret", "nuevo-k")
    assert json.loads(fake_store.path.read_text()) == SEED
    assert not list(fake_store.path.parent.glob(".*.tmp"))


def test_directorio_de_solo_lectura_da_un_error_claro(fake_store, monkeypatch: pytest.MonkeyPatch):
    def sin_permiso(*_a, **_k):
        raise PermissionError("[Errno 30] Read-only file system")

    monkeypatch.setattr(store.os, "replace", sin_permiso)
    with pytest.raises(store.SecretStoreError, match="solo lectura"):
        store.update_secret_field("globex", "client_secret", "nuevo-k")


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        ("abc123\n", "abc123"),
        ("abc123\r\n", "abc123"),
        ("con espacio interno ok", "con espacio interno ok"),
    ],
)
def test_clean_secret_input_acepta(raw, esperado):
    assert store.clean_secret_input(raw) == esperado


@pytest.mark.parametrize("raw", ["", "\n", " abc", "abc ", "abc\t\n"])
def test_clean_secret_input_rechaza(raw):
    with pytest.raises(ValueError):
        store.clean_secret_input(raw)
