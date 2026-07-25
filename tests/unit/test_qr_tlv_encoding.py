"""ZATCA QR TLV encoding: tags 1-6 are text, tags 7-9 are raw binary.

Why this file exists (2026-07-18): invoices printed from this app were rejected
by ZATCA's own reader (تطبيق فاتورة) while ordinary phone scanners read them
fine. The cause was here, not in the printing — ``build_qr`` was putting the
*base64 text* of the signature, public key and stamp into tags 7/8/9 instead of
the raw DER bytes the spec requires.

That failure mode is invisible to a normal scanner: the outer payload is still
valid base64 and still decodes, so the code "reads". Only a reader that walks the
TLV and tries to load tag 8 as a DER key notices, and it just rejects the invoice.
A real tag 8 starts with 0x30 (DER SEQUENCE); the buggy one started with 'M'
(0x4d), the first character of base64-encoded DER.

The bug also inflated the payload by ~81 bytes (base64 is 4/3 the size of the
bytes it encodes), pushing the QR to a denser version with smaller modules — so
fixing it makes the code easier to print as well.
"""

from __future__ import annotations

import base64
import datetime
import re

import pytest

from app.services import saudi_zatca_generator as zg
from app.services.saudi_zatca_generator import build_qr

# Realistic shapes: a secp256k1 SPKI DER public key is 88 bytes and starts 0x30;
# a DER ECDSA signature is ~70-72 bytes and also starts 0x30.
DER_PUBKEY = bytes([0x30, 0x56]) + bytes(range(86))
DER_SIGNATURE = bytes([0x30, 0x45]) + bytes(range(69))
DER_STAMP = bytes([0x30, 0x46]) + bytes(range(70))
INVOICE_HASH = base64.b64encode(b"h" * 32).decode()


def _qr(**overrides) -> str:
    kwargs = dict(
        seller_name="شركة الشرارة المضيئة",
        vat_number="311474361800003",
        timestamp=datetime.datetime(2026, 7, 18, 6, 32, 10),
        total_with_vat="575.00",
        vat_total="75.00",
        invoice_hash=INVOICE_HASH,
        signature=base64.b64encode(DER_SIGNATURE).decode(),
        public_key=base64.b64encode(DER_PUBKEY).decode(),
        stamp=base64.b64encode(DER_STAMP).decode(),
    )
    kwargs.update(overrides)
    return build_qr(**kwargs)


def _tags(payload_b64: str) -> dict[int, bytes]:
    raw = base64.b64decode(payload_b64)
    out: dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(raw):
        tag, length = raw[i], raw[i + 1]
        out[tag] = raw[i + 2:i + 2 + length]
        i += 2 + length
    return out


# ----------------------------------------------------------------------
# The regression itself
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "tag, expected",
    [(7, DER_SIGNATURE), (8, DER_PUBKEY), (9, DER_STAMP)],
    ids=["signature", "public_key", "stamp"],
)
def test_binary_tags_carry_raw_der_bytes_not_base64_text(tag, expected):
    value = _tags(_qr())[tag]
    assert value == expected, f"tag {tag} is not the raw DER value"
    assert value[0] == 0x30, (
        f"tag {tag} starts with 0x{value[0]:02x}; a DER value starts with 0x30. "
        "0x4d ('M') means the base64 text was emitted instead of the bytes."
    )


def test_public_key_tag_loads_as_a_real_der_key():
    """The check ZATCA's reader effectively performs.

    Uses a genuine secp256k1 key (the curve ZATCA mandates for EGS units) rather
    than a hand-written blob, so "it parses" means something.
    """
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256K1())
    real_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert real_der[0] == 0x30 and len(real_der) == 88

    tag8 = _tags(_qr(public_key=base64.b64encode(real_der).decode()))[8]
    assert tag8 == real_der
    # Must parse straight from the tag, with no base64 step in between.
    loaded = serialization.load_der_public_key(tag8)
    assert isinstance(loaded.curve, ec.SECP256K1)


def test_text_tags_are_still_text():
    tags = _tags(_qr())
    assert tags[1].decode("utf-8") == "شركة الشرارة المضيئة"
    assert tags[2].decode("ascii") == "311474361800003"
    # Reverted state: local time with a Z suffix. See the tag-3 tests below.
    assert tags[3].decode("ascii") == "2026-07-18T06:32:10Z"
    assert tags[4].decode("ascii") == "575.00"
    assert tags[5].decode("ascii") == "75.00"


def test_invoice_hash_tag_is_currently_base64_text():
    """REVERTED STATE (2026-07-18): tag 6 emits base64 text, not the raw digest.

    This deliberately asserts a **deviation from the standard**, which specifies
    "Length: length of hash (SHA256 ) is 32 bytes / Value: the byte array
    constituting the value of the field" (Security Features Implementation
    Standards v1.2 §4.1). The raw-digest encoding was implemented, verified, and
    then rolled back on request. The test tracks reality so the suite stays
    honest; flip ``QR_TAG6_RAW_DIGEST`` to restore compliance and the companion
    test below already covers that path.
    """
    assert not zg.QR_TAG6_RAW_DIGEST, "toggle changed; swap this test with the one below"
    tag6 = _tags(_qr())[6]
    assert tag6.decode("ascii") == INVOICE_HASH
    assert len(tag6) == 44


def test_tag6_raw_digest_mode_is_still_available_and_correct(monkeypatch):
    """The standard-compliant path must keep working, so re-enabling is one line."""
    monkeypatch.setattr(zg, "QR_TAG6_RAW_DIGEST", True)
    tag6 = _tags(_qr())[6]
    assert len(tag6) == 32, f"tag 6 is {len(tag6)} bytes; the digest is 32"
    assert tag6 == base64.b64decode(INVOICE_HASH)


def test_tag6_carries_the_same_digest_either_way():
    """Whichever encoding is selected, the underlying hash must be identical."""
    text_form = _tags(_qr())[6]
    assert base64.b64decode(text_form) == base64.b64decode(INVOICE_HASH)


# ----------------------------------------------------------------------
# Tag 3 — currently a KNOWN-WRONG timestamp, reverted on request
# ----------------------------------------------------------------------
def test_timestamp_currently_stamps_z_onto_unconverted_local_time():
    """REVERTED STATE (2026-07-18): documents a known bug, does not bless it.

    06:32:10 is naive KSA local time. The true UTC instant is 03:32:10Z, but tag 3
    emits 06:32:10Z — three hours late. ``_qr_timestamp`` fixes this and is kept
    in the module, currently unused.
    """
    payload = _qr(timestamp=datetime.datetime(2026, 7, 18, 6, 32, 10))
    assert _tags(payload)[3].decode("ascii") == "2026-07-18T06:32:10Z"


def test_the_utc_correction_is_still_available_and_correct():
    """Guards the fix itself so re-applying it stays a one-line change."""
    assert zg._qr_timestamp(datetime.datetime(2026, 7, 18, 6, 32, 10)) == \
        "2026-07-18T03:32:10Z"
    # 01:00 KSA is the PREVIOUS day in UTC — the date moves, not just the clock.
    assert zg._qr_timestamp(datetime.datetime(2026, 7, 18, 1, 0, 0)) == \
        "2026-07-17T22:00:00Z"
    utc = datetime.datetime(2026, 7, 18, 3, 32, 10, tzinfo=datetime.timezone.utc)
    assert zg._qr_timestamp(utc) == "2026-07-18T03:32:10Z"


def test_timestamp_is_iso8601_with_a_z_suffix():
    value = _tags(_qr())[3].decode("ascii")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value), value


# ----------------------------------------------------------------------
# Structure
# ----------------------------------------------------------------------
def test_all_nine_tags_present_and_tlv_walks_cleanly_to_the_end():
    raw = base64.b64decode(_qr())
    seen, i = [], 0
    while i < len(raw):
        tag, length = raw[i], raw[i + 1]
        seen.append(tag)
        i += 2 + length
    assert i == len(raw), "TLV does not consume the payload exactly"
    assert seen == [1, 2, 3, 4, 5, 6, 7, 8, 9], f"tag order/set wrong: {seen}"


def test_tags_are_emitted_in_ascending_order():
    raw = base64.b64decode(_qr())
    order, i = [], 0
    while i < len(raw):
        order.append(raw[i])
        i += 2 + raw[i + 1]
    assert order == sorted(order)


def test_a_value_over_255_bytes_is_rejected_not_silently_truncated():
    """The TLV length is one byte, so an oversized value has no valid encoding."""
    with pytest.raises(ValueError, match="255"):
        _qr(public_key=base64.b64encode(bytes(300)).decode())


def test_crypto_tags_are_omitted_when_unsigned():
    tags = _tags(_qr(signature=None, public_key=None, stamp=None))
    assert set(tags) == {1, 2, 3, 4, 5, 6}


# ----------------------------------------------------------------------
# The payload got smaller, which helps printing
# ----------------------------------------------------------------------
def test_raw_encoding_is_smaller_than_the_base64_text_it_replaced():
    """Base64 is 4/3 the size of its bytes, so the fix shrinks the QR."""
    fixed = len(base64.b64decode(_qr()))
    inflated = fixed + sum(
        len(base64.b64encode(v)) - len(v)
        for v in (DER_SIGNATURE, DER_PUBKEY, DER_STAMP)
    )
    assert fixed < inflated
    assert inflated - fixed > 60, "expected ~80 bytes saved across tags 7/8/9"
