"""
Secret store — puente de Fase 0-1.

Un único archivo JSON cifrado con `age` (binario en la imagen). PAM Web Console lo
descifra en runtime con la clave privada; ningún secreto queda en claro en
disco ni en el repo. En Fase 2 esto se reemplaza por OpenBao (ver
docs/PLAN.md §5.3), manteniendo la misma interfaz `get_secret(ref)`.

Estructura del JSON descifrado:
    {
      "globex":  {"client_id": "...", "client_secret": "..."},
      "umbrella":   {"client_id": "...", "client_secret": "..."},
      "lab": {"client_id": "...", "client_secret": "..."}
    }

El archivo se descifra una vez y se vuelve a descifrar solo cuando cambia
(fecha de modificación, tamaño o inodo): rotar una credencial no exige
reiniciar `api` ni `worker`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import threading
from pathlib import Path

from app.config import get_settings


class SecretStoreError(Exception):
    pass


_lock = threading.Lock()
# (huella del archivo cifrado, contenido descifrado)
_cache: tuple[tuple[int, int, int], dict] | None = None


def _run_age(cmd: list[str], *, stdin: bytes | None = None) -> bytes:
    """Corre `age`/`age-keygen`. Comando estático: los argumentos salen de la
    config (no de input del usuario). Sin shell. Nunca loguea `stdin`."""
    try:
        out = subprocess.run(cmd, input=stdin, capture_output=True, check=True, timeout=10)  # noqa: S603
    except FileNotFoundError:
        raise SecretStoreError(f"el binario '{cmd[0]}' no está instalado en la imagen")
    except subprocess.TimeoutExpired:
        raise SecretStoreError(f"'{cmd[0]}' no respondió a tiempo")
    except subprocess.CalledProcessError as e:
        raise SecretStoreError(e.stderr.decode(errors="replace")[:200])
    return out.stdout


def _file_key(path: str) -> tuple[int, int, int]:
    try:
        st = os.stat(path)
    except OSError as e:
        raise SecretStoreError(f"no se pudo leer {path}: {e}") from e
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _decrypt_file(path: str | None = None) -> dict:
    s = get_settings()
    target = path or s.secrets_file
    try:
        raw = _run_age(["age", "--decrypt", "-i", s.secrets_age_key, target])
    except SecretStoreError as e:
        raise SecretStoreError(f"no se pudo descifrar {target}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SecretStoreError(f"el secret store no es JSON válido: {e}") from e
    if not isinstance(data, dict):
        raise SecretStoreError("el secret store no es un objeto JSON")
    return data


def _load() -> dict:
    global _cache
    key = _file_key(get_settings().secrets_file)
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        data = _decrypt_file()
        _cache = (key, data)
        return data


def get_secret(ref: str) -> dict:
    """Devuelve el dict de credenciales para un secret_ref (ej. 'globex')."""
    from app.demo import demo_secret, is_demo

    if is_demo():
        return demo_secret()
    data = _load()
    if ref not in data:
        raise SecretStoreError(f"secret_ref '{ref}' no está en el secret store")
    return data[ref]


def available() -> bool:
    """True si el secret store se puede leer (para /health y diagnóstico)."""
    from app.demo import is_demo

    if is_demo():
        return True
    try:
        _load()
        return True
    except SecretStoreError:
        return False


def reload() -> None:
    """Descarta lo descifrado: la próxima lectura vuelve a descifrar."""
    global _cache
    with _lock:
        _cache = None


def clean_secret_input(raw: str) -> str:
    """Normaliza un secreto pegado en la terminal: quita solo el salto de línea
    final y rechaza espacios al inicio o al final (un pegado de más es la causa
    típica de un `invalid client creds` que cuesta encontrar)."""
    value = raw.rstrip("\r\n")
    if not value:
        raise ValueError("el valor está vacío")
    if value != value.strip():
        raise ValueError("el valor tiene espacios al inicio o al final (¿pegado de más?)")
    # BOM u otros caracteres invisibles (típico al pasar el valor por una
    # tubería de PowerShell): un `https://...` con un BOM delante no es una URL
    if not value.isprintable():
        raise ValueError(
            "el valor tiene caracteres no imprimibles (¿un BOM al pegarlo o canalizarlo?)"
        )
    return value


def _write_store(data: dict) -> Path:
    """Reescribe el archivo cifrado con `data`. Devuelve la ruta de la copia
    previa (`<archivo>.prev`, cifrada con la misma clave).

    Pasos seguros: cifra a un archivo temporal junto al original, comprueba
    que ese archivo descifra a lo esperado y recién entonces lo reemplaza de
    forma atómica. Requiere que el directorio sea escribible (en
    `api`/`worker` está montado `:ro`; ver el servicio `secrets-admin` de
    docker-compose.yml)."""
    s = get_settings()
    path = Path(s.secrets_file)
    recipient = _run_age(["age-keygen", "-y", s.secrets_age_key]).decode().strip()
    payload = json.dumps(data, indent=2).encode()
    tmp = path.with_name(f".{path.name}.tmp")
    prev = path.with_name(f"{path.name}.prev")
    try:
        tmp.unlink(missing_ok=True)
        _run_age(["age", "-r", recipient, "-o", str(tmp)], stdin=payload)
        if _decrypt_file(str(tmp)) != data:
            raise SecretStoreError(
                "el archivo cifrado nuevo no descifra a lo esperado; no se reemplazó nada"
            )
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        shutil.copy2(path, prev)
        os.replace(tmp, path)
    except OSError as e:
        raise SecretStoreError(
            f"no se pudo escribir en {path.parent} (¿está montado de solo lectura?): {e}"
        ) from e
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
    reload()
    return prev


def update_secret_field(ref: str, field: str, value: str) -> Path:
    """Cambia UN campo de UNA entrada del secret store y reescribe el archivo
    cifrado, dejando el resto intacto. Devuelve la ruta de la copia previa."""
    data = _decrypt_file()
    if ref not in data:
        raise SecretStoreError(f"secret_ref '{ref}' no está en el secret store")
    if field not in data[ref]:
        raise SecretStoreError(f"la entrada '{ref}' no tiene el campo '{field}'")
    data[ref][field] = value
    return _write_store(data)


def set_secret_entry(ref: str, entry: dict[str, str]) -> Path:
    """Alta o reemplazo COMPLETO de una entrada del secret store (p. ej. credenciales
    de una integración que no es un tenant), dejando el resto
    intacto. Mismos pasos seguros que `update_secret_field`."""
    data = _decrypt_file()
    data[ref] = dict(entry)
    return _write_store(data)
