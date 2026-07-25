"""A printed invoice shows the customer's CR, never a phone number.

النموذج الثالث and النموذج الخامس each reproduced their reference PDF's customer
phone row. At the user's request (2026-07-16) those rows now carry the commercial
registration, read from the customer's own CR field, labelled «السجل التجارى» /
«CR». No template may print a customer phone again, and the CR must be the
customer's own — not the seller's, which every template also prints.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest

from app.ui.screens.saudi_invoice_print import build_invoice_html
from app.ui.screens.saudi_invoice_print_v2 import build_invoice_html_v2
from app.ui.screens.saudi_invoice_print_v3 import build_invoice_html_v3
from app.ui.screens.saudi_invoice_print_v4 import build_invoice_html_v4
from app.ui.screens.saudi_invoice_print_v5 import build_invoice_html_v5
from app.ui.screens.saudi_invoice_print_v6 import build_invoice_html_v6
from app.ui.screens.saudi_invoice_print_v7 import build_invoice_html_v7
from app.ui.screens.saudi_invoice_print_v8 import build_invoice_html_v8

SELLER_CR = "1010101010"
CUSTOMER_CR = "7070707070"
PHONE = "0500000000"

BUILDERS = [
    ("النموذج الأول", build_invoice_html),
    ("النموذج الثاني", build_invoice_html_v2),
    ("النموذج الثالث", build_invoice_html_v3),
    ("النموذج الرابع", build_invoice_html_v4),
    ("النموذج الخامس", build_invoice_html_v5),
    ("النموذج السادس", build_invoice_html_v6),
    ("النموذج السابع", build_invoice_html_v7),
    ("النموذج الثامن", build_invoice_html_v8),
]

# The templates that had a customer phone row, which is now the CR row.
CR_ROW_BUILDERS = [
    ("النموذج الثالث", build_invoice_html_v3),
    ("النموذج الخامس", build_invoice_html_v5),
]


def _data() -> dict:
    return {
        "invoice_number": "6285",
        "issue_datetime": datetime.datetime(2026, 6, 25, 10, 30),
        "payment_label": "نقدي",
        "seller": {"name": "شركة ريوف المواسم", "address": "الرياض",
                   "vat": "312389238400003", "cr": SELLER_CR},
        # A phone is supplied on purpose: nothing may print it.
        "customer": {"name": "المصنع روف الحال", "address": "سكاكا", "vat": "310045693200003",
                     "cr": CUSTOMER_CR, "phone": PHONE, "code": "77"},
        "lines": [
            {"code": "R3962", "name": "شكوالته بلجيكي", "qty": Decimal("65"), "price": Decimal("80"),
             "unit": "PCE", "before": Decimal("5200"), "vat_amount": Decimal("780"),
             "vat_rate": Decimal("15"), "total": Decimal("5980")},
        ],
        "totals": {"subtotal": Decimal("5200"), "vat": Decimal("780"), "total": Decimal("5980"),
                   "discount": Decimal("0"), "taxable": Decimal("5200")},
        "qr_payload": "AR1234TEST",
        "printed_by": "مدير النظام", "printed_at": "2026-06-25 10:30", "vat_rate": "15",
    }


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_no_template_prints_a_customer_phone(label, builder):
    assert PHONE not in builder(_data()), f"{label}: still prints the customer phone"


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_no_template_carries_a_phone_label(label, builder):
    html = builder(_data())
    for needle in ("رقم الجوال", "رقم جوال العميل", "Mobile"):
        assert needle not in html, f"{label}: still shows a phone label ({needle})"


@pytest.mark.parametrize("label, builder", CR_ROW_BUILDERS, ids=[b[0] for b in CR_ROW_BUILDERS])
def test_customer_cr_row_shows_the_customers_own_cr(label, builder):
    html = builder(_data())
    assert CUSTOMER_CR in html, f"{label}: the customer's CR is missing"
    assert "CR" in html
    assert "السجل التجارى" in html
    # Both CRs appear (the seller's is printed too), so prove they are distinct
    # values and the customer row did not fall back to the seller's.
    assert SELLER_CR in html


def test_v3_totals_are_exactly_as_wide_as_the_items_table():
    # User request (2026-07-16): the totals block must line up with the items
    # table above it, not sit in its own narrower grid.
    from app.ui.screens.saudi_invoice_print_v3 import _COL_WIDTHS, _TABLE_W, _TOTALS_COLS

    assert _TABLE_W == sum(_COL_WIDTHS)
    assert sum(_TOTALS_COLS) == _TABLE_W


def test_v3_drops_the_footer_strip():
    # «تاريخ الطباعة» / «صفحة رقم» / «طباعة بواسطة» / «أرقام التواصل» are gone.
    html = build_invoice_html_v3(_data())
    for needle in ("تاريخ الطباعة", "صفحة رقم", "طباعة بواسطة", "أرقام التواصل"):
        assert needle not in html, f"v3 still prints «{needle}»"


def test_v5_drops_the_signature_strip():
    html = build_invoice_html_v5(_data())
    for needle in ("توقيع المستلم", "توقيع المسؤول", "Received By", "Authorized"):
        assert needle not in html, f"v5 still prints «{needle}»"


def test_customer_cr_row_is_blank_when_the_customer_has_no_cr():
    # A customer without a CR simply prints an empty row — never a phone, and
    # never the seller's CR leaking into the customer's row.
    data = _data()
    data["customer"]["cr"] = ""
    html = build_invoice_html_v5(data)
    assert PHONE not in html
    assert html.count(SELLER_CR) == 2  # the seller's own two rows (AR + EN), no more
