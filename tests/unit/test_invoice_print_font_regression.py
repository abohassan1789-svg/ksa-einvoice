"""Guards against the inline-``font-family`` trap across every print template.

The font stacks contain double quotes (``'Arial, "Segoe UI", …'``). Emitting one
inside an inline ``style="…"`` closes the attribute at that first inner quote,
so **every declaration after it is silently dropped**::

    <td style="… font-family:Arial, "Segoe UI", …; font-size:13.32px; …">
                                   ^ attribute ends here

النموذج الرابع shipped like this in eight places: its whole page rendered as a
single 12pt Arial in black — Calibri never applied, the ``#1a2a6c`` label colour
never applied, the title fell 15.97pt → 12pt and the items table 9.99pt → 12pt,
which also disabled ``_fit_px``. Nothing caught it because Arial and the default
sans look identical; only the PDF's extracted ``size`` revealed it.

Fonts therefore belong in the ``<style>`` block (classes), never inline. These
tests are pure string checks — no Chromium, so they run in headless CI.
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

_BUILDERS = {
    "الأول": build_invoice_html,
    "الثاني": build_invoice_html_v2,
    "الثالث": build_invoice_html_v3,
    "الرابع": build_invoice_html_v4,
    "الخامس": build_invoice_html_v5,
    "السادس": build_invoice_html_v6,
    "السابع": build_invoice_html_v7,
    "الثامن": build_invoice_html_v8,
}

# Any inline style attribute that contains a font-family declaration. The stacks
# all carry double quotes, so this is unsafe regardless of what follows it.
_INLINE_FONT_FAMILY = re.compile(r'style="[^"]*font-family:')


def _sample_data() -> dict:
    return {
        "invoice_number": "Inv-10120",
        "issue_datetime": datetime.datetime(2026, 6, 10, 9, 0, 0),
        "payment_label": "نقدى",
        "seller": {
            "name": "شركة عالم فوريو للتجارة", "name_en": "Furio World Trading Co",
            "address": "الرياض", "address_en": "Riyadh",
            "vat": "310101193600003", "cr": "7009410601",
        },
        "customer": {
            "name": "شركة فوج المتحدة التجارية", "address": "خميس مشيط",
            "vat": "314858083700003", "cr": "7017464566",
        },
        "lines": [{
            "code": "15203", "name": "توريد و تركيب سكريت", "qty": Decimal("1"),
            "unit": "PCE", "price": Decimal("57000.00"), "before": Decimal("57000.00"),
            "vat_amount": Decimal("8550.00"), "vat_rate": Decimal("15"),
            "total": Decimal("65550.00"),
        }],
        "totals": {
            "subtotal": Decimal("57000.00"), "vat": Decimal("8550.00"),
            "total": Decimal("65550.00"),
        },
        "qr_payload": None,
    }


@pytest.mark.parametrize("name", sorted(_BUILDERS))
def test_no_inline_font_family(name: str) -> None:
    """No template may put a quoted font stack inside an inline style attribute."""
    html = _BUILDERS[name](_sample_data())
    offenders = _INLINE_FONT_FAMILY.findall(html)
    assert not offenders, (
        f"النموذج {name}: {len(offenders)} inline style attribute(s) declare "
        f"font-family. The stack's double quotes end the attribute there and "
        f"every later declaration (font-size, colour, white-space) is dropped. "
        f"Move the font to a class in the <style> block."
    )


@pytest.mark.parametrize("name", sorted(_BUILDERS))
def test_builder_emits_a_full_page(name: str) -> None:
    """Sanity: each builder still returns a populated sheet for the same data.

    Deliberately not asserting ``data-sheet`` — that marker is a convention of
    the later templates only; النموذج الأول predates it and is fine without.
    """
    html = _BUILDERS[name](_sample_data())
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "65,550.00" in html  # the invoice total reaches the page
    assert "شركة فوج المتحدة التجارية" in html  # so does the customer


# ======================================================================
# Receipt voucher (سند قبض) — the same trap, the same rule
# ======================================================================
from app.ui.screens.saudi_receipt_voucher_print import (  # noqa: E402
    build_receipt_voucher_html_v2,
    build_receipt_voucher_html_v3,
    build_receipt_voucher_html_v4,
    build_receipt_voucher_html_v5,
    build_receipt_voucher_html_v6,
    build_receipt_voucher_html_v7,
    build_receipt_voucher_html_v8,
    build_receipt_voucher_html_v9,
)

# النموذج الأول is deliberately absent: it is the pixel-traced template and it
# does declare font-family inline — but with *single*-quoted stacks
# ('Arimo','Arial'), which never terminate the attribute, so nothing is dropped.
# It would trip this regex without being broken. Every later voucher template
# uses classes, and new ones must.
_VOUCHER_BUILDERS = {
    "الثاني": build_receipt_voucher_html_v2,
    "الثالث": build_receipt_voucher_html_v3,
    "الرابع": build_receipt_voucher_html_v4,
    "الخامس": build_receipt_voucher_html_v5,
    "السادس": build_receipt_voucher_html_v6,
    "السابع": build_receipt_voucher_html_v7,
    "الثامن": build_receipt_voucher_html_v8,
    "التاسع": build_receipt_voucher_html_v9,
}


def _voucher_sample_data() -> dict:
    return {
        "number": "PA-000148",
        "issue_date": datetime.date(2026, 7, 15),
        "amount": Decimal("4600.00"),
        "received_from": "شركة النخبة للمقاولات المحدودة",
        "purpose": "دفعة من حساب فاتورة رقم INV-10120",
        "payment_type": "نقدًا",
        "seller": {
            "name": "مؤسسة الركن التجارية", "name_en": "Al-Rukn Trading Est.",
            "address": "الرياض — شارع العليا", "address_en": "Riyadh — Olaya St.",
            "vat": "300123456700003", "cr": "1010234567",
        },
    }


@pytest.mark.parametrize("name", sorted(_VOUCHER_BUILDERS))
def test_voucher_has_no_inline_font_family(name: str) -> None:
    """No voucher template may put a quoted font stack in an inline style."""
    html = _VOUCHER_BUILDERS[name](_voucher_sample_data())
    offenders = _INLINE_FONT_FAMILY.findall(html)
    assert not offenders, (
        f"سند القبض / النموذج {name}: {len(offenders)} inline style attribute(s) "
        f"declare font-family. Move the font to a class in the <style> block."
    )


@pytest.mark.parametrize("name", sorted(_VOUCHER_BUILDERS))
def test_voucher_builder_emits_a_full_page(name: str) -> None:
    """Sanity: each voucher builder returns a populated sheet for the same data."""
    html = _VOUCHER_BUILDERS[name](_voucher_sample_data())
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "4,600.00" in html  # the amount reaches the page
    assert "PA-000148" in html  # so does the serial
    assert "شركة النخبة للمقاولات المحدودة" in html  # and the payer


def test_v5_prints_both_halves_of_its_bilingual_letterhead() -> None:
    """النموذج الخامس's whole point is the mirrored header — pin both halves.

    The English half is the only thing distinguishing it from the other five, and
    it is the one part that silently degrades: `name_en`/`address_en` fall back
    to the Arabic values, so a template that dropped the English block entirely
    would still render a plausible-looking page.
    """
    html = build_receipt_voucher_html_v5(_voucher_sample_data())
    assert "Al-Rukn Trading Est." in html and "مؤسسة الركن التجارية" in html
    assert "Riyadh — Olaya St." in html and "الرياض — شارع العليا" in html
    assert "RECEIPT VOUCHER" in html and "سند قبض" in html


def test_v6_repeats_the_serial_on_its_stub() -> None:
    """النموذج السادس prints the serial twice — the band and the tear-off stub."""
    html = build_receipt_voucher_html_v6(_voucher_sample_data())
    assert html.count("PA-000148") == 2, "the stub must repeat the band's serial"
    assert "كعب المحاسبة" in html


def test_no_voucher_template_doubles_the_amount_terminator() -> None:
    """`amount_in_words_ar` already ends with «لا غير» — see النموذج الرابع.

    Its first cut appended a terminator of its own and printed «لا غير» twice.
    """
    for name, builder in _VOUCHER_BUILDERS.items():
        html = builder(_voucher_sample_data())
        assert html.count("لا غير") == 1, f"النموذج {name} prints «لا غير» twice"


def test_v4_keeps_its_two_fonts_and_label_colour() -> None:
    """النموذج الرابع's design is Calibri + Arial with one coloured label.

    This is what the trap silently erased, so pin it: both stacks must be
    declared, and the label colour must be reachable via a class rather than an
    inline declaration that would be dropped.
    """
    html = build_invoice_html_v4(_sample_data())
    assert "Calibri" in html, "Calibri stack missing"
    assert "#1a2a6c" in html, "the meta-label colour is missing"
    # The colour must live in the stylesheet, not inline after a font-family.
    assert re.search(r"\.lbl\s*{[^}]*#1a2a6c", html), (
        "the #1a2a6c label colour must be declared in the <style> block"
    )
    assert 'class="lbl"' in html and 'class="cal"' in html
