"""Secure password hashing using PBKDF2-HMAC-SHA256 (standard library only).

No plain-text passwords are ever stored. A stored hash looks like::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

Using the stdlib avoids adding a bcrypt/argon2 dependency to the project.
"""

from __future__ import annotations

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 240_000


class PasswordHasher:
    """Hash and verify passwords. Stateless; safe to instantiate anywhere."""

    def __init__(self, iterations: int = DEFAULT_ITERATIONS) -> None:
        self.iterations = iterations

    def hash(self, password: str) -> str:
        if password is None:
            password = ""
        salt = os.urandom(16)
        digest = self._derive(password, salt, self.iterations)
        return f"{ALGORITHM}${self.iterations}${salt.hex()}${digest.hex()}"

    def verify(self, password: str, stored: str | None) -> bool:
        if not stored:
            return False
        try:
            algorithm, iterations_text, salt_hex, hash_hex = stored.split("$")
        except ValueError:
            return False
        if algorithm != ALGORITHM:
            return False
        try:
            iterations = int(iterations_text)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except ValueError:
            return False
        candidate = self._derive(password or "", salt, iterations)
        return hmac.compare_digest(candidate, expected)

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
