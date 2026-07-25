"""LOCAL, TEST-ONLY ECDSA signer for the Saudi invoice QR (Phase-2 tags 7/8/9).

⚠️  IMPORTANT — this is **not** a ZATCA-onboarded cryptographic stamp.

A real Phase-2 QR carries three cryptographic TLV tags on top of the plain facts:

* tag 7 — the ECDSA digital signature of the invoice,
* tag 8 — the signing EGS public key,
* tag 9 — ZATCA's signature over that public key (the CSID *stamp*).

Producing a **ZATCA-valid** stamp needs an EGS keypair that has been onboarded
through the Fatoora portal (a compliance / production CSID). This module does
*not* do that. At the user's explicit request (for a development / demo build,
so an offline QR reader recognises the code as "Phase-2 shaped"), it instead
generates a **local, self-signed** secp256k1 keypair, signs the invoice hash
with it, and self-signs the public key for tag 9.

Consequences, stated plainly:

* the QR becomes *structurally* complete and self-consistent (tag 7 verifies
  against tag 8 over tag 6), so a structure-checking reader shows "compatible";
* it is **NOT** accepted by ZATCA and the invoice is **NOT** valid for
  clearance / reporting — the public key is not backed by a ZATCA certificate.

For real compliance, replace this with a proper EGS onboarding + signing flow.
The dedicated ``sales_invoice_zatca_data`` cryptographic columns are deliberately
left untouched by this module — only the QR carries the test signature.
"""

from __future__ import annotations

import base64

from app.config.connection_config import default_config_dir

# Marks the QR / any surfaced data as a local test stamp, never a ZATCA one.
TEST_STAMP_MARKER = "LOCAL-TEST-UNSIGNED-BY-ZATCA"

_KEY_FILENAME = "egs_test_signing_key.pem"
_cached_key = None


def _key_path():
    try:
        directory = default_config_dir()
    except Exception:  # noqa: BLE001 - fall back to a writable local dir
        from pathlib import Path

        directory = Path(__file__).resolve().parents[2] / "config"
    return directory / _KEY_FILENAME


def _load_or_create_key():
    """Return a stable local EC private key, generating + persisting it once.

    A stable key means the public key (tag 8) stays the same across invoices,
    as a real EGS unit's would.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = _key_path()
    if path.exists():
        _cached_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        return _cached_key

    key = ec.generate_private_key(ec.SECP256K1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pem)
    except OSError:
        pass  # in-memory key for this run is still fine
    _cached_key = key
    return key


def is_available() -> bool:
    """True if the optional ``cryptography`` dependency can be imported."""
    try:
        import cryptography  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def sign_invoice_hash(invoice_hash_b64: str) -> dict[str, str] | None:
    """Build the (test) Phase-2 signature tags for a base64 invoice hash.

    Returns ``{"signature", "public_key", "stamp"}`` as base64 strings (TLV tags
    7 / 8 / 9), or ``None`` if signing is unavailable — callers then fall back to
    a tags 1–6 QR.
    """
    if not invoice_hash_b64:
        return None
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        key = _load_or_create_key()
        digest = base64.b64decode(invoice_hash_b64)
        # Signing the SHA-256 digest directly == signing the invoice with
        # SHA256withECDSA (tag 6 is that digest) -> a verifier is self-consistent.
        signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        public_der = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # tag 9: (self-)signature over the public key. A real EGS gets this from
        # ZATCA's CA; here it is self-signed, hence not ZATCA-valid.
        stamp = key.sign(public_der, ec.ECDSA(hashes.SHA256()))
        return {
            "signature": base64.b64encode(signature).decode("ascii"),
            "public_key": base64.b64encode(public_der).decode("ascii"),
            "stamp": base64.b64encode(stamp).decode("ascii"),
        }
    except Exception:  # noqa: BLE001 - never let a signing hiccup break saving
        return None


__all__ = ["sign_invoice_hash", "is_available", "TEST_STAMP_MARKER"]
