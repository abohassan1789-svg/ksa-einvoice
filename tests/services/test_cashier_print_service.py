"""Tests for the Cashier thermal-print service.

Two layers:

* **Pure logic** (no DB, no Qt) — a fabricated invoice with *present* (test)
  cryptographic fields exercises the receipt model, the binary-TLV/Base64 QR
  encoder + validator, tag order/length/UTF-8 rules, determinism, and the
  prerequisite gate.
* **Integration** (real DB) — print-count tracking updates only the permitted
  columns, never touches financial/cryptographic fields, and creates no
  sales/receipt/accounting rows. Rows are cleaned up.

NOTE: the "crypto" values in the pure fixtures are arbitrary test strings used
only to exercise the TLV encoder; they are NOT presented as real ZATCA data. The
service refuses to build a production QR whenever any real input is missing (see
``test_missing_prereqs_blocks_production_qr``).
"""

from __future__ import annotations

import base64
import datetime
import io
from decimal import Decimal

import pytest

from app.database.db import Database
from app.services import cashier_print_service as cps
from app.services.cashier_print_service import (
    CashierPrintService,
    CashierPrintValidationError,
    CashierQrError,
    ZatcaPrerequisitesMissing,
    build_zatca_qr_base64,
    check_zatca_prerequisites,
    decode_and_validate_zatca_qr,
    decode_tlv,
    encode_tlv,
)


# ---------------------------------------------------------------------------
# Fixtures (pure)
# ---------------------------------------------------------------------------

_TS = datetime.datetime(2026, 7, 15, 14, 20, 0)


def _signed_invoice(**overrides):
    header = {
        "id": 999,
        "invoice_number": "CINV-000099",
        "invoice_uuid": None,
        "invoice_date": datetime.date(2026, 7, 15),
        "invoice_time": datetime.time(14, 20),
        "invoice_datetime": _TS,
        "customer_id": None,
        "company_id": 1,
        "created_by_user_id": 1,
        "invoice_status": "ISSUED",
        "invoice_type": "SIMPLIFIED",
        "currency_code": "SAR",
        "subtotal": Decimal("100.00"),
        "vat_amount": Decimal("15.00"),
        "grand_total": Decimal("115.00"),
        "seller_name_snapshot": "شركة الاختبار للتجارة",
        "seller_vat_number_snapshot": "311111111111113",
        "seller_commercial_registration_snapshot": "1010101010",
        "seller_address_snapshot": "الرياض",
        "buyer_name_snapshot": None,
        "buyer_vat_number_snapshot": None,
        "zatca_invoice_hash": "hD1P0hE3n0Q9m3o9test-hash==",
        "zatca_signature": "MEUCIQDtest-signature==",
        "zatca_public_key": "MFkwEwtest-public-key==",
        "zatca_cryptographic_stamp": "test-ca-signature==",
        "zatca_status": "NOT_GENERATED",
        "print_count": 0,
        "last_printed_at": None,
    }
    header.update(overrides)
    lines = [{
        "id": 1,
        "cashier_invoice_id": 999,
        "line_number": 1,
        "product_id": 5,
        "product_code_snapshot": "1001",
        "product_name_snapshot": "قهوة عربية",
        "quantity": Decimal("2"),
        "unit_price": Decimal("50.00"),
        "line_subtotal": Decimal("100.00"),
        "vat_rate": Decimal("15.00"),
        "vat_amount": Decimal("15.00"),
        "line_total": Decimal("115.00"),
    }]
    return {"header": header, "lines": lines}


class _FakeRepo:
    def __init__(self, invoice):
        self._invoice = invoice

    def load_invoice(self, _id):
        return self._invoice

    def get_user_display_name(self, _uid):
        return "أمين الصندوق"

    def get_company(self, _cid):
        # A *different* current name to prove the model uses the snapshot, not this.
        return {"name_ar": "اسم شركة مختلف حالياً", "logo": None, "logo_mime": None}


def _svc(invoice):
    return CashierPrintService(repository=_FakeRepo(invoice))


# ---------------------------------------------------------------------------
# Receipt model + validation
# ---------------------------------------------------------------------------

def test_issued_invoice_builds_receipt_model():
    inv = _signed_invoice()
    svc = _svc(inv)
    svc.validate_invoice_for_print(inv)
    svc.validate_invoice_totals(inv)
    model = svc.build_cashier_receipt_model(inv)
    assert model["title_ar"] == "فاتورة ضريبية مبسطة"
    assert model["qr"]["available"] is True
    assert model["lines"][0]["name"] == "قهوة عربية"


def test_draft_cannot_be_printed_officially_but_preview_ok():
    inv = _signed_invoice(invoice_status="DRAFT")
    svc = _svc(inv)
    with pytest.raises(CashierPrintValidationError):
        svc.validate_invoice_for_print(inv, allow_draft_preview=False)
    svc.validate_invoice_for_print(inv, allow_draft_preview=True)  # preview allowed
    model = svc.build_cashier_receipt_model(inv, is_preview=True)
    assert model["watermark"] and "DRAFT" in model["watermark"]
    # A draft DOES get a QR now, but a preview one: it is rebuilt at issue, and
    # the official-print path stays closed (the dialog gates on is_draft).
    assert model["is_draft"] is True
    assert model["qr"]["is_preview"] is True


def test_draft_preview_qr_is_not_the_stored_issued_qr():
    """The draft preview must never pass off a stored/final QR as its own."""
    inv = _signed_invoice(invoice_status="DRAFT")
    model = _svc(inv).build_cashier_receipt_model(inv, is_preview=True)
    if model["qr"]["available"]:
        # Built from the live company + placeholder chain position, so it differs
        # from what the fabricated "stored" crypto fields would encode.
        assert model["qr"]["base64"] != build_zatca_qr_base64(inv["header"])


def test_issued_invoice_qr_is_never_flagged_as_preview():
    model = _svc(_signed_invoice()).build_cashier_receipt_model(_signed_invoice())
    assert model["qr"]["available"] is True
    assert model["qr"]["is_preview"] is False


def test_cancelled_invoice_cannot_be_printed():
    inv = _signed_invoice(invoice_status="CANCELLED")
    with pytest.raises(CashierPrintValidationError):
        _svc(inv).validate_invoice_for_print(inv, allow_draft_preview=True)


def test_seller_and_item_come_from_snapshots_not_current_master():
    inv = _signed_invoice()
    model = _svc(inv).build_cashier_receipt_model(inv)
    # Seller legal text is the snapshot, not the (different) current company name.
    assert model["seller"]["name_ar"] == "شركة الاختبار للتجارة"
    assert model["seller"]["vat"] == "311111111111113"
    # Item data is the line snapshot.
    assert model["lines"][0]["unit_price"] == "50.00"
    assert model["lines"][0]["name"] == "قهوة عربية"


def test_totals_match_and_mismatch_blocks():
    inv = _signed_invoice()
    _svc(inv).validate_invoice_totals(inv)  # consistent -> ok
    bad = _signed_invoice()
    bad["lines"][0]["line_subtotal"] = Decimal("999.00")  # break the sum
    with pytest.raises(CashierPrintValidationError):
        _svc(bad).validate_invoice_totals(bad)


# ---------------------------------------------------------------------------
# QR: TLV / Base64 / order / lengths / matches / determinism
# ---------------------------------------------------------------------------

def test_qr_is_binary_tlv_base64_tags_1_to_9_in_order():
    inv = _signed_invoice()
    qr = build_zatca_qr_base64(inv["header"])
    raw = base64.b64decode(qr, validate=True)          # valid Base64
    records = decode_tlv(raw)
    assert [t for t, _v in records] == [1, 2, 3, 4, 5, 6, 7, 8, 9]  # present, once, in order


def test_utf8_seller_name_length_is_byte_count():
    inv = _signed_invoice()
    raw = base64.b64decode(build_zatca_qr_base64(inv["header"]))
    records = decode_tlv(raw)
    tag1, value1 = records[0]
    assert tag1 == 1
    name = inv["header"]["seller_name_snapshot"]
    assert len(value1) == len(name.encode("utf-8"))     # bytes, not characters
    assert len(name.encode("utf-8")) > len(name)         # Arabic => multi-byte


def test_qr_tag_values_match_invoice_sources():
    h = _signed_invoice()["header"]
    records = dict(decode_tlv(base64.b64decode(build_zatca_qr_base64(h))))
    assert records[2].decode() == h["seller_vat_number_snapshot"]
    assert records[3].decode() == cps.iso_timestamp(h["invoice_datetime"])
    assert records[4].decode() == "115.00"               # grand_total
    assert records[5].decode() == "15.00"                # vat_amount
    assert records[6].decode() == h["zatca_invoice_hash"]
    assert records[7].decode() == h["zatca_signature"]
    assert records[8].decode() == h["zatca_public_key"]
    assert records[9].decode() == h["zatca_cryptographic_stamp"]


def test_qr_validation_and_determinism():
    h = _signed_invoice()["header"]
    qr = build_zatca_qr_base64(h)
    decode_and_validate_zatca_qr(qr, h)                  # passes
    assert build_zatca_qr_base64(h) == qr                # deterministic / same twice


def test_invalid_base64_rejected():
    h = _signed_invoice()["header"]
    with pytest.raises(CashierQrError):
        decode_and_validate_zatca_qr("not_base64!!!", h)


def test_invalid_tlv_length_rejected():
    bad = bytes([1, 5]) + b"abc"                          # declares 5, has 3
    with pytest.raises(CashierQrError):
        decode_tlv(bad)


def test_missing_tags_rejected():
    partial = encode_tlv([(1, b"x"), (2, b"y"), (3, b"z"), (4, b"1"), (5, b"2")])
    h = _signed_invoice()["header"]
    with pytest.raises(CashierQrError):
        decode_and_validate_zatca_qr(base64.b64encode(partial).decode(), h)


def test_duplicate_tags_rejected():
    dup = encode_tlv([(1, b"a"), (1, b"b")] + [(t, b"x") for t in range(2, 10)])
    h = _signed_invoice()["header"]
    with pytest.raises(CashierQrError):
        decode_and_validate_zatca_qr(base64.b64encode(dup).decode(), h)


def test_missing_prereqs_blocks_production_qr():
    # A real Cashier invoice: seller present but crypto fields NULL.
    inv = _signed_invoice(
        zatca_invoice_hash=None, zatca_signature=None,
        zatca_public_key=None, zatca_cryptographic_stamp=None,
    )
    missing = check_zatca_prerequisites(inv["header"])
    assert "بصمة الفاتورة (Tag 6)" in missing
    with pytest.raises(ZatcaPrerequisitesMissing):
        _svc(inv).build_or_load_zatca_qr(inv["header"])
    # And the model marks the QR unavailable (no fabricated code).
    model = _svc(inv).build_cashier_receipt_model(inv)
    assert model["qr"]["available"] is False
    assert model["qr"]["base64"] is None


# ---------------------------------------------------------------------------
# Receipt HTML + QR image
# ---------------------------------------------------------------------------

def test_receipt_html_thermal_width_dynamic_height_and_arabic():
    from app.ui.screens.cashier_receipt_print import build_cashier_receipt_html
    inv = _signed_invoice()
    model = _svc(inv).build_cashier_receipt_model(inv)
    html = build_cashier_receipt_html(model, qr_data_uri="data:image/png;base64,AAAA")
    assert "80mm" in html and "auto" in html            # thermal width + dynamic height
    assert "قهوة عربية" in html                          # Arabic present, not reversed
    assert "فاتورة ضريبية مبسطة" in html
    assert html.count('class="item"') == len(model["lines"])


def test_receipt_height_grows_with_lines():
    from app.ui.screens.cashier_receipt_print import build_cashier_receipt_html
    inv = _signed_invoice()
    inv["lines"] = inv["lines"] * 5                       # 5 identical lines
    model = _svc(inv).build_cashier_receipt_model(inv)
    html = build_cashier_receipt_html(model)
    assert html.count('class="item"') == 5


def test_reprint_label_shown_when_printed_before():
    from app.ui.screens.cashier_receipt_print import build_cashier_receipt_html
    inv = _signed_invoice(print_count=1)
    model = _svc(inv).build_cashier_receipt_model(inv)
    assert model["is_reprint"] is True
    assert "REPRINT" in build_cashier_receipt_html(model)
    # Reprint QR is byte-identical to the original.
    assert model["qr"]["base64"] == build_zatca_qr_base64(inv["header"])


def test_qr_image_is_sharp_square_with_quiet_zone():
    from PIL import Image
    from app.ui.screens.cashier_receipt_print import render_qr_data_uri, QR_QUIET_MODULES
    uri = render_qr_data_uri(build_zatca_qr_base64(_signed_invoice()["header"]), paper_mm=80)
    assert uri.startswith("data:image/png;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert img.width == img.height                        # perfect square
    assert QR_QUIET_MODULES >= 4                          # quiet zone >= 4 modules
    # Pure black-on-white (mode "1").
    assert img.convert("1").getextrema() == (0, 255)


# ---------------------------------------------------------------------------
# Integration: print tracking updates only allowed fields, no side effects
# ---------------------------------------------------------------------------

def _try_connect():
    try:
        db = Database()
        db.execute("SELECT 1")
        return db
    except Exception:
        return None


_probe = _try_connect()
_db_required = pytest.mark.skipif(_probe is None, reason="No PostgreSQL reachable.")


@pytest.fixture()
def issued_invoice_and_cleanup():
    from app.services.cashier_service import CashierService
    admin = Database()
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, price FROM products ORDER BY id LIMIT 1")
    if company is None or product is None:
        pytest.skip("need a company and a product")
    svc = CashierService()
    form = {"company_id": company["id"], "customer_id": None,
            "lines": [{"product_id": product["id"], "quantity": Decimal("2")}]}
    saved = svc.save_cashier_draft(None, form, user_id=None)
    issued = svc.issue_cashier_invoice_locally(saved["header"]["id"], form, user_id=None)
    invoice_id = issued["header"]["id"]
    created = [invoice_id]
    yield admin, invoice_id, issued
    for iid in created:
        admin.execute("DELETE FROM cashier_invoices WHERE id = %s", [iid])


@_db_required
def test_record_print_updates_only_allowed_fields_no_side_effects(issued_invoice_and_cleanup):
    admin, invoice_id, issued = issued_invoice_and_cleanup
    header_before = issued["header"]
    sales_before = admin.fetch_one("SELECT COUNT(*) c FROM sales_invoices")["c"]
    lines_before = admin.fetch_one("SELECT COUNT(*) c FROM sales_invoice_lines")["c"]
    rv_before = admin.fetch_one("SELECT COUNT(*) c FROM receipt_vouchers")["c"]

    print_svc = CashierPrintService()
    updated = print_svc.record_successful_print(invoice_id, None)  # no QR (none available)
    h = updated["header"]

    # Only the print columns changed.
    assert h["print_count"] == 1
    assert h["last_printed_at"] is not None
    # Financial + cryptographic + snapshot + status fields are unchanged.
    for field in ("subtotal", "vat_amount", "grand_total", "invoice_number",
                  "seller_name_snapshot", "seller_vat_number_snapshot",
                  "zatca_invoice_hash", "zatca_signature", "zatca_public_key",
                  "invoice_status", "invoice_datetime"):
        assert h[field] == header_before[field]

    # A second print is a reprint (count 2), still no other change.
    updated2 = print_svc.record_successful_print(invoice_id, None)
    assert updated2["header"]["print_count"] == 2

    # No sales / receipt side effects.
    assert admin.fetch_one("SELECT COUNT(*) c FROM sales_invoices")["c"] == sales_before
    assert admin.fetch_one("SELECT COUNT(*) c FROM sales_invoice_lines")["c"] == lines_before
    assert admin.fetch_one("SELECT COUNT(*) c FROM receipt_vouchers")["c"] == rv_before


@_db_required
def test_record_print_refuses_non_issued(issued_invoice_and_cleanup):
    from app.services.cashier_service import CashierService
    from app.repositories.cashier_repository import StaleCashierInvoiceError
    admin, _issued_id, _issued = issued_invoice_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id FROM products ORDER BY id LIMIT 1")
    svc = CashierService()
    draft = svc.save_cashier_draft(
        None, {"company_id": company["id"], "customer_id": None,
               "lines": [{"product_id": product["id"], "quantity": Decimal("1")}]}, user_id=None
    )
    draft_id = draft["header"]["id"]
    try:
        with pytest.raises(StaleCashierInvoiceError):
            CashierPrintService().record_successful_print(draft_id, None)
    finally:
        admin.execute("DELETE FROM cashier_invoices WHERE id = %s", [draft_id])
