"""Unit tests for the Saudi receipt-voucher print template (pure functions only).

These never instantiate ``QWebEngineView`` (no Chromium in headless CI); they
exercise the HTML builder that reproduces the ``سند قبض`` mock-up.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.ui.screens.saudi_receipt_voucher_print import (
    build_receipt_voucher_html,
    build_receipt_voucher_html_v2,
    build_receipt_voucher_html_v3,
    build_receipt_voucher_html_v4,
    build_receipt_voucher_html_v5,
    build_receipt_voucher_html_v6,
    build_receipt_voucher_html_v7,
    build_receipt_voucher_html_v8,
    build_receipt_voucher_html_v9,
)


def _sample_data() -> dict:
    return {
        "number": "5862",
        "issue_date": datetime.datetime(2026, 4, 2, 9, 15),
        "amount": Decimal("12558.00"),
        "received_from": "شركة خيال برو",
        "purpose": "قيمة فاتورة مبيعات رقم 5862",
        "seller": {
            "name": "مؤسسة سهم اطياف التجارية",
            "cr": "7036415102",
            "vat": "314722371900003",
            "address": "حائل",
        },
    }


def test_build_voucher_html_is_populated():
    html = build_receipt_voucher_html(_sample_data())
    # Static title from the mock-up.
    assert "ايصال إستلام نقديه" in html
    # Seller header block.
    assert "مؤسسة سهم اطياف التجارية" in html
    assert "7036415102" in html
    assert "314722371900003" in html
    assert "حائل" in html
    # Serial, amount (comma-grouped, 2dp) and dd-mm-yyyy date.
    assert "5862" in html
    assert "12,558.00" in html
    assert "02-04-2026" in html
    # Payer + purpose rows.
    assert "شركة خيال برو" in html
    assert "قيمة فاتورة مبيعات رقم 5862" in html
    # Amount spelled out in Arabic words (reuses the invoice helper).
    assert "فقط" in html
    assert "ريالاً" in html
    # The one signature line (the mock-up's two captioned blocks are gone).
    assert "التوقيع: ........." in html


# ---------------------------------------------------------------------------
# The single signature line (all six layouts) — replaced the two captioned
# signature blocks and النموذج الرابع's stamp ring at the user's request.
# ---------------------------------------------------------------------------
ALL_VOUCHER_BUILDERS = (
    build_receipt_voucher_html,
    build_receipt_voucher_html_v2,
    build_receipt_voucher_html_v3,
    build_receipt_voucher_html_v4,
    build_receipt_voucher_html_v5,
    build_receipt_voucher_html_v6,
    build_receipt_voucher_html_v7,
    build_receipt_voucher_html_v8,
    build_receipt_voucher_html_v9,
)

# Every caption the six layouts used to print, verbatim, plus the stamp ring.
GONE_FROM_EVERY_VOUCHER = (
    "توقيع الخزينة", "توقيع الحسابات",          # النموذج الأول
    "توقيع المحاسب", "توقيع أمين الصندوق",      # النموذج الثاني
    "Receiver Signature", "Accountant Signature",  # النموذج الثالث
    "ختم الشركة",                                # النموذج الرابع's stamp ring
    "Received by", "Accountant",                 # النموذج الخامس
    "توقيع المستلم",                             # النموذج السادس
    # The markup the caption/underline pairs were built from — a leftover block
    # would drag these in even if its wording changed.
    'class="sg"', "sg-l", "sg-c", 'class="stamp"',
)


@pytest.mark.parametrize("build", ALL_VOUCHER_BUILDERS)
def test_voucher_prints_one_signature_line_and_nothing_else(build):
    html = build(_sample_data())
    assert html.count("التوقيع: .........") == 1
    for gone in GONE_FROM_EVERY_VOUCHER:
        assert gone not in html, f"{gone!r} still printed by {build.__name__}"


@pytest.mark.parametrize("build", ALL_VOUCHER_BUILDERS)
def test_voucher_signature_line_sits_bottom_left_and_reads_rtl(build):
    html = build(_sample_data())
    # The line is the sheet's only text-align:left — physical left (not `start`,
    # which would flip it back to the right on these RTL sheets) — and it carries
    # direction:rtl so the label reads first and the dots trail to its left.
    signature_div = [
        chunk for chunk in html.split("<div ") if "التوقيع: ........." in chunk
    ]
    assert len(signature_div) == 1
    style = signature_div[0]
    assert "text-align:left" in style
    assert "direction:rtl" in style


def test_build_voucher_html_is_a4_sheet_not_page_level_rtl():
    html = build_receipt_voucher_html(_sample_data())
    assert '<html lang="ar">' in html
    # A4 design sheet dimensions preserved (794×1123 @ 96dpi).
    assert "width: 794px" in html
    assert "height: 1123px" in html
    # No whole-document mirroring — RTL is applied per-element only.
    assert 'dir="rtl"' not in html.split("<body>")[0]


def test_print_css_keeps_it_to_a_single_page():
    html = build_receipt_voucher_html(_sample_data())
    # A4 page box with zero margins is declared for print.
    assert "@page" in html
    assert "size: A4" in html
    # In print the full-height A4 sheet is shrunk to the content region so it
    # never rounds up into a trailing blank second page.
    assert "@media print" in html
    assert "height: 600px" in html


def test_build_voucher_html_handles_missing_fields():
    html = build_receipt_voucher_html({})
    # Still renders the skeleton without raising; amount falls back to 0.00.
    assert "ايصال إستلام نقديه" in html
    assert "0.00" in html


def test_build_voucher_html_escapes_seller_name():
    data = _sample_data()
    data["seller"] = {**data["seller"], "name": "مؤسسة <إطياف> & شركاه"}
    html = build_receipt_voucher_html(data)
    assert "&lt;إطياف&gt;" in html
    assert "&amp;" in html
