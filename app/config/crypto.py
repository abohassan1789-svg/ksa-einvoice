"""Credential encryption helpers.

Secrets (the PostgreSQL password in the connection profile) are NEVER written to
disk in plain text. This module encrypts them at rest and returns a
*self-describing* string so :func:`decrypt` can always tell how a value was
protected:

    ``dpapi:<base64>``   Windows DPAPI (per-machine), no third-party dependency.
    ``fernet:<token>``   ``cryptography`` Fernet with a passphrase-derived key,
                         used for portable client profiles that must be
                         decryptable on another machine with the passphrase.
    ``plain:<base64>``   Last-resort obfuscation when neither is available. A
                         warning is logged; the value is only base64-encoded.

Design contract:

* No secret is ever logged or placed on a command line.
* DPAPI encryption is machine-scoped (``CRYPTPROTECT_LOCAL_MACHINE``) so the
  server service account and the interactive admin can both decrypt it.
* :func:`decrypt` dispatches purely on the prefix, so profiles written by one
  mechanism keep working after the others become available/unavailable.
"""

from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

_DPAPI_PREFIX = "dpapi:"
_FERNET_PREFIX = "fernet:"
_PLAIN_PREFIX = "plain:"

# ---------------------------------------------------------------------------
# Windows DPAPI (no third-party dependency; uses ctypes against crypt32.dll)
# ---------------------------------------------------------------------------


def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_crypt(data: bytes, encrypt: bool) -> bytes:
    """Call CryptProtectData / CryptUnprotectData via ctypes."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(raw: bytes) -> "DATA_BLOB":
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def from_blob(blob: "DATA_BLOB") -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob.pbData)

    blob_in = to_blob(data)
    blob_out = DATA_BLOB()
    # CRYPTPROTECT_LOCAL_MACHINE = 0x4  -> any account on this machine can decrypt.
    flags = 0x4
    fn = (
        ctypes.windll.crypt32.CryptProtectData
        if encrypt
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    ok = fn(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("DPAPI operation failed (CryptProtectData/CryptUnprotectData)")
    return from_blob(blob_out)


# ---------------------------------------------------------------------------
# Fernet (passphrase-derived) — optional, only when a passphrase is supplied
# ---------------------------------------------------------------------------


def _fernet_from_passphrase(passphrase: str, salt: bytes):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return Fernet(key)


# Fixed application salt. The passphrase is the real secret; the salt only needs
# to be stable so the same passphrase reproduces the same key across machines.
_FERNET_SALT = b"crm-connection-profile-v1-salt!!"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encrypt(secret: str, passphrase: str | None = None) -> str:
    """Encrypt *secret* and return a self-describing token.

    * With a *passphrase* -> Fernet (portable, decryptable elsewhere with the
      same passphrase). Falls back to DPAPI/plain if ``cryptography`` is missing.
    * Without a passphrase -> DPAPI on Windows, else obfuscated ``plain:``.
    """
    if secret is None:
        secret = ""
    raw = secret.encode("utf-8")

    if passphrase:
        try:
            token = _fernet_from_passphrase(passphrase, _FERNET_SALT).encrypt(raw)
            return _FERNET_PREFIX + token.decode("ascii")
        except Exception as exc:  # cryptography missing / other error
            logger.warning("Fernet encryption unavailable (%s); falling back.", exc)

    if _dpapi_available():
        try:
            protected = _dpapi_crypt(raw, encrypt=True)
            return _DPAPI_PREFIX + base64.b64encode(protected).decode("ascii")
        except Exception as exc:
            logger.warning("DPAPI encryption failed (%s); storing obfuscated.", exc)

    logger.warning(
        "No OS credential store available; the password is only base64-obfuscated. "
        "Install 'cryptography' or run on Windows for real encryption."
    )
    return _PLAIN_PREFIX + base64.b64encode(raw).decode("ascii")


def decrypt(token: str, passphrase: str | None = None) -> str:
    """Reverse :func:`encrypt`. Empty/None token -> empty string."""
    if not token:
        return ""

    if token.startswith(_DPAPI_PREFIX):
        blob = base64.b64decode(token[len(_DPAPI_PREFIX):])
        return _dpapi_crypt(blob, encrypt=False).decode("utf-8")

    if token.startswith(_FERNET_PREFIX):
        if not passphrase:
            raise ValueError("A passphrase is required to decrypt this profile.")
        raw = token[len(_FERNET_PREFIX):].encode("ascii")
        return _fernet_from_passphrase(passphrase, _FERNET_SALT).decrypt(raw).decode("utf-8")

    if token.startswith(_PLAIN_PREFIX):
        return base64.b64decode(token[len(_PLAIN_PREFIX):]).decode("utf-8")

    # Legacy / unknown -> assume it was already plain text (backward compatible).
    return token


def is_encrypted(token: str) -> bool:
    """True if *token* looks like output of :func:`encrypt` (not raw plaintext)."""
    return bool(token) and token.startswith(
        (_DPAPI_PREFIX, _FERNET_PREFIX, _PLAIN_PREFIX)
    )
