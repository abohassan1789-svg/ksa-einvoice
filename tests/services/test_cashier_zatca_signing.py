"""Tests for the Cashier local Phase-2 (ZATCA) signing module.

Pure logic only — no DB, no Qt. These prove the receipt QR is *genuinely*
self-consistent rather than merely well-shaped:

* tag 6 really is base64(SHA-256(the generated XML));
* tag 7 really verifies against tag 8 over tag 6 (a real ECDSA check);
* the full tags 1-9 payload round-trips through the print service's own
  binary-TLV decoder + validator;
* placeholder seller values are used only when the source is genuinely absent,
  and are persisted so the QR, the stored row and the receipt all agree.

What they deliberately do NOT claim: that the stamp is ZATCA-valid. It is a local
self-signed key, so ZATCA would reject it — see the module docstring.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
from decimal import Decimal

import pytest

from app.services.cashier_print_service import (
    build_zatca_qr_base64,
    check_zatca_prerequisites,
    decode_and_validate_zatca_qr,
    decode_tlv,
)
from app.services.cashier_zatca_signing import (
    DEFAULT_SELLER_NAME_AR,
    DEFAULT_SELLER_VAT,
    ZATCA_STATUS_READY,
    build_cashier_zatca_fields,
    build_preview_header,
    resolve_seller_for_qr,
)
from app.services.saudi_zatca_generator import GENESIS_PIH

_TS = datetime.datetime(2026, 7, 15, 14, 20, 0)


def _header(**overrides):
    header = {
        "invoice_number": "CINV-000099",
        "invoice_uuid": None,
        "invoice_datetime": _TS,
        "currency_code": "SAR",
        "subtotal": Decimal("100.00"),
        "vat_amount": Decimal("15.00"),
        "grand_total": Decimal("115.00"),
        "seller_name_snapshot": "شركة الاختبار للتجارة",
        "seller_vat_number_snapshot": "311111111111113",
        "buyer_name_snapshot": None,
        "buyer_vat_number_snapshot": None,
    }
    header.update(overrides)
    return header


_LINES = [{
    "quantity": Decimal("2"),
    "unit_price": Decimal("50.00"),
    "vat_rate": Decimal("15.00"),
    "product_name_snapshot": "قهوة عربية",
    "line_subtotal": Decimal("100.00"),
    "vat_amount": Decimal("15.00"),
    "line_total": Decimal("115.00"),
}]


def _fields(**overrides):
    return build_cashier_zatca_fields(
        _header(**overrides), _LINES, icv=1, pih=GENESIS_PIH
    )


# ---------------------------------------------------------------------------
# Seller defaults
# ---------------------------------------------------------------------------

def test_real_seller_values_are_never_replaced_by_defaults():
    seller = resolve_seller_for_qr(_header())
    assert seller["name"] == "شركة الاختبار للتجارة"
    assert seller["vat"] == "311111111111113"
    assert seller["used_defaults"] is False


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_absent_seller_values_fall_back_to_documented_placeholders(missing):
    seller = resolve_seller_for_qr(
        _header(seller_name_snapshot=missing, seller_vat_number_snapshot=missing)
    )
    assert seller["name"] == DEFAULT_SELLER_NAME_AR
    assert seller["vat"] == DEFAULT_SELLER_VAT
    assert seller["used_defaults"] is True


def test_placeholders_are_persisted_so_qr_and_stored_row_agree():
    fields = _fields(seller_name_snapshot=None, seller_vat_number_snapshot=None)
    assert fields["seller_name_snapshot"] == DEFAULT_SELLER_NAME_AR
    assert fields["seller_vat_number_snapshot"] == DEFAULT_SELLER_VAT


def test_present_snapshots_are_not_rewritten():
    fields = _fields()
    assert "seller_name_snapshot" not in fields
    assert "seller_vat_number_snapshot" not in fields


# ---------------------------------------------------------------------------
# The cryptographic values are real, not fabricated
# ---------------------------------------------------------------------------

def test_tag6_is_the_real_sha256_of_the_generated_xml():
    fields = _fields()
    expected = base64.b64encode(
        hashlib.sha256(fields["zatca_xml"].encode("utf-8")).digest()
    ).decode("ascii")
    assert fields["zatca_invoice_hash"] == expected


def test_tag7_signature_actually_verifies_against_tag8_over_tag6():
    """A real ECDSA verification — the QR is self-consistent, not just shaped."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    fields = _fields()
    public_key = serialization.load_der_public_key(
        base64.b64decode(fields["zatca_public_key"])
    )
    public_key.verify(  # raises InvalidSignature if the signature is bogus
        base64.b64decode(fields["zatca_signature"]),
        base64.b64decode(fields["zatca_invoice_hash"]),
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )


def test_status_is_ready_never_reported_or_accepted():
    # The invoice was signed locally, never sent to ZATCA; the status must not
    # imply otherwise.
    assert _fields()["zatca_status"] == ZATCA_STATUS_READY


def test_xml_carries_the_icv_and_pih_it_was_given():
    fields = build_cashier_zatca_fields(_header(), _LINES, icv=7, pih=GENESIS_PIH)
    assert fields["zatca_icv"] == 7
    assert fields["zatca_previous_invoice_hash"] == GENESIS_PIH
    assert "<cbc:UUID>7</cbc:UUID>" in fields["zatca_xml"]
    assert GENESIS_PIH in fields["zatca_xml"]


def test_a_different_invoice_produces_a_different_hash_and_signature():
    a = _fields()
    b = _fields(grand_total=Decimal("230.00"))
    assert a["zatca_invoice_hash"] != b["zatca_invoice_hash"]
    assert a["zatca_signature"] != b["zatca_signature"]


def test_public_key_is_stable_across_invoices_like_a_real_egs_unit():
    assert _fields()["zatca_public_key"] == _fields(invoice_number="CINV-000100")["zatca_public_key"]


# ---------------------------------------------------------------------------
# End-to-end: signed fields -> a valid tags 1-9 QR the reader accepts
# ---------------------------------------------------------------------------

def _signed_header(**overrides):
    header = _header(**overrides)
    header.update(_fields(**overrides))
    return header


def test_signed_invoice_satisfies_every_phase2_prerequisite():
    assert check_zatca_prerequisites(_signed_header()) == []


def test_signed_invoice_yields_a_qr_with_tags_1_to_9_in_order():
    header = _signed_header()
    qr = build_zatca_qr_base64(header)
    records = decode_tlv(base64.b64decode(qr))
    assert [tag for tag, _v in records] == [1, 2, 3, 4, 5, 6, 7, 8, 9]

    values = dict(records)
    assert values[1].decode("utf-8") == "شركة الاختبار للتجارة"
    assert values[2].decode("utf-8") == "311111111111113"
    assert values[4].decode("utf-8") == "115.00"
    assert values[5].decode("utf-8") == "15.00"
    assert values[6].decode("utf-8") == header["zatca_invoice_hash"]


def test_signed_invoice_qr_passes_the_projects_own_validator():
    header = _signed_header()
    # Full check: TLV structure, tag order, UTF-8 byte lengths, tag->source match
    # and determinism.
    decode_and_validate_zatca_qr(build_zatca_qr_base64(header), header)


# ---------------------------------------------------------------------------
# DRAFT preview QR (snapshots are still NULL -> seller comes from the company)
# ---------------------------------------------------------------------------

_COMPANY = {
    "name_ar": "شركه ركن سنيم للتجاره",
    "name_en": "Rukn Sanim",
    "vat_number": "311628287100003",
    "commercial_registration": "1010101010",
    "address_ar": "الرياض",
}


def _draft_header(**overrides):
    # A saved draft: totals + datetime exist, every seller snapshot is NULL.
    return _header(
        seller_name_snapshot=None, seller_vat_number_snapshot=None, **overrides
    )


def test_draft_preview_qr_takes_seller_from_the_selected_company():
    preview = build_preview_header(_draft_header(), _LINES, _COMPANY)
    assert preview is not None
    assert check_zatca_prerequisites(preview) == []
    values = dict(decode_tlv(base64.b64decode(build_zatca_qr_base64(preview))))
    # The company's real data, NOT the placeholders — this is the bug the
    # screenshot showed: a draft reported seller name/VAT as "missing".
    assert values[1].decode("utf-8") == "شركه ركن سنيم للتجاره"
    assert values[2].decode("utf-8") == "311628287100003"


def test_draft_preview_qr_validates_and_has_all_nine_tags():
    preview = build_preview_header(_draft_header(), _LINES, _COMPANY)
    decode_and_validate_zatca_qr(build_zatca_qr_base64(preview), preview)
    records = decode_tlv(base64.b64decode(build_zatca_qr_base64(preview)))
    assert [tag for tag, _v in records] == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_draft_preview_falls_back_to_placeholders_when_company_has_no_vat():
    preview = build_preview_header(
        _draft_header(), _LINES, {"name_ar": "ورشة", "vat_number": None}
    )
    values = dict(decode_tlv(base64.b64decode(build_zatca_qr_base64(preview))))
    assert values[1].decode("utf-8") == "ورشة"       # real name kept
    assert values[2].decode("utf-8") == DEFAULT_SELLER_VAT  # only the VAT defaulted


def test_draft_preview_uses_placeholder_chain_position():
    # A draft has no place in the per-company chain until it is issued.
    preview = build_preview_header(_draft_header(), _LINES, _COMPANY)
    assert preview["zatca_icv"] == 0
    assert preview["zatca_previous_invoice_hash"] == GENESIS_PIH


def test_draft_preview_is_none_without_lines():
    assert build_preview_header(_draft_header(), [], _COMPANY) is None


def test_draft_preview_never_overrides_an_existing_snapshot():
    preview = build_preview_header(_header(), _LINES, _COMPANY)
    assert preview["seller_name_snapshot"] == "شركة الاختبار للتجارة"
    assert preview["seller_vat_number_snapshot"] == "311111111111113"


def test_qr_built_from_placeholder_seller_still_validates():
    header = _signed_header(seller_name_snapshot=None, seller_vat_number_snapshot=None)
    decode_and_validate_zatca_qr(build_zatca_qr_base64(header), header)
    values = dict(decode_tlv(base64.b64decode(build_zatca_qr_base64(header))))
    assert values[1].decode("utf-8") == DEFAULT_SELLER_NAME_AR
    assert values[2].decode("utf-8") == DEFAULT_SELLER_VAT
