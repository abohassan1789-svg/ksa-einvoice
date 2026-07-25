"""Unit tests for the Saudi invoice print template (pure functions only).

These never instantiate ``QWebEngineView`` (no Chromium in headless CI); they
exercise the HTML builder, the Arabic amount-in-words and the QR helper.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.ui.screens.saudi_invoice_print import (
    amount_in_words_ar,
    build_invoice_html,
    qr_data_uri,
    signature_line_html,
)
from app.ui.screens.saudi_invoice_print_v2 import build_invoice_html_v2
from app.ui.screens.saudi_invoice_print_v3 import build_invoice_html_v3
from app.ui.screens.saudi_invoice_print_v4 import build_invoice_html_v4
from app.ui.screens.saudi_invoice_print_v5 import build_invoice_html_v5
from app.ui.screens.saudi_invoice_print_v6 import build_invoice_html_v6
from app.ui.screens.saudi_invoice_print_v7 import build_invoice_html_v7
from app.ui.screens.saudi_invoice_print_v8 import build_invoice_html_v8


def _sample_data() -> dict:
    return {
        "invoice_number": "6285",
        "issue_datetime": datetime.datetime(2026, 6, 25, 10, 30),
        "payment_label": "نقدي",
        "seller": {"name": "شركة ريوف المواسم", "address": "الرياض", "vat": "312389238400003", "cr": ""},
        "customer": {"name": "المصنع روف الحال", "address": "سكاكا", "vat": "310045693200003", "cr": ""},
        "lines": [
            {"code": "R3962", "name": "شكوالته بلجيكي", "qty": Decimal("65"), "price": Decimal("80"),
             "before": Decimal("5200"), "vat_amount": Decimal("780"), "vat_rate": Decimal("15"),
             "total": Decimal("5980")},
        ],
        "totals": {"subtotal": Decimal("5200"), "vat": Decimal("780"), "total": Decimal("5980")},
        "qr_payload": "AR1234TEST",
    }


_ALL_INVOICE_BUILDERS = (
    build_invoice_html,
    build_invoice_html_v2,
    build_invoice_html_v3,
    build_invoice_html_v4,
    build_invoice_html_v5,
    build_invoice_html_v6,
    build_invoice_html_v7,
    build_invoice_html_v8,
)


# ---------------------------------------------------------------------------
# Long item names — النموذج الخامس's rule, applied to the four that lacked it.
# ---------------------------------------------------------------------------
# An item name can be one unbroken token («ممممم…»), which offers no space to
# wrap at. النماذج الأول/الثاني/السادس/السابع each printed it as a single line
# that ran out over the neighbouring columns and, in the first three, clean off
# the paper's left edge. النموذج الخامس already did the right thing: wrap inside
# the column and let the row grow (see its `_NAME_COLUMN`).
#
# The declaration below is the load-bearing half of that fix, and it is
# specifically `anywhere` — NOT `break-word`. Both break the line, but only
# `anywhere` also reduces *min-content*, which is what a flex/grid item's
# automatic minimum is measured against. النموذج السابع shipped with
# `break-word` and still overflowed, which is exactly this distinction. These
# are string checks (no Chromium in CI); the rendered geometry was measured
# separately with PyMuPDF.
_NAME_WRAPS_ANYWHERE = (
    ("الأول", build_invoice_html),
    ("الثاني", build_invoice_html_v2),
    ("السادس", build_invoice_html_v6),
    ("السابع", build_invoice_html_v7),
)


def _long_name_data() -> dict:
    data = _sample_data()
    data["lines"] = [{**data["lines"][0], "name": "م" * 200}]
    return data


@pytest.mark.parametrize("label,build", _NAME_WRAPS_ANYWHERE)
def test_long_item_name_is_allowed_to_break_mid_token(label, build):
    # Spaces stripped: the four write the declaration in their own house style.
    css = build(_long_name_data()).replace(" ", "")
    assert "overflow-wrap:anywhere" in css, (
        f"النموذج {label} lost its mid-token break — a long name will overrun the column"
    )


def test_second_templates_grid_tracks_cannot_be_widened_by_content():
    """`Nfr` has an automatic minimum of min-content; `minmax(0,Nfr)` does not.

    Without the 0 floor the الوصف track grew to fit the whole unbroken name and
    pushed the row off the sheet, whatever the cell's own wrapping said.
    """
    html = build_invoice_html_v2(_long_name_data())
    assert "minmax(0,179fr)" in html
    # No bare `fr` track may survive anywhere in the items grid.
    assert "grid-template-columns:78fr" not in html


# ---------------------------------------------------------------------------
# النموذج السابع — the totals block flows under the items table (user, 2026-07-17)
# ---------------------------------------------------------------------------
def test_seventh_totals_share_the_items_table_box():
    """The two blocks must be cut to one box, and the totals must follow the table.

    Both were wrong: the totals were pinned near the foot of the sheet (313pt of
    white under a one-item invoice, and it never moved as the table grew), and it
    was 1px narrower and 1px to the left of the table — a visible step where the
    two are meant to read as one block.
    """
    from app.ui.screens import saudi_invoice_print_v7 as v7

    # One positioned block holds both, so the totals cannot be left behind.
    assert ".block" in build_invoice_html_v7(_sample_data())
    assert "_TOTALS_LEFT" not in dir(v7), "the totals must not carry an x of their own"
    assert "_TOTALS_RIGHT" not in dir(v7)
    # The rows span that block rather than a width of their own.
    assert "left: 0; width: 100%" in build_invoice_html_v7(_sample_data())


def test_seventh_totals_rows_keep_their_measured_overlaps():
    # The reference's five boxes overlap by 1.5-4.9px, which is what gives the
    # block its irregular 23/19/22/19/31 rhythm. Re-based to the block's own top,
    # the gaps between consecutive tops must be unchanged.
    from app.ui.screens.saudi_invoice_print_v7 import _TOTALS_ROWS

    tops = [t for t, _h in _TOTALS_ROWS]
    assert tops[0] == 0.0, "the first row defines the block's top"
    gaps = [round(b - a, 1) for a, b in zip(tops, tops[1:])]
    assert gaps == [22.3, 19.0, 22.4, 19.2]   # 929.9-907.6, 948.9-929.9, ...


def test_seventh_spends_the_whole_sheet_on_item_rows():
    """The table must claim every px the sheet has left, and clip nothing silently.

    Originally this pinned the budget to the reference's own totals row (907.6),
    because deriving it from the block's foot silently took 8px — enough to drop
    the 19th row of a 19-item invoice, which `overflow:hidden` eats without a word.

    2026-07-18: the QR grew to QR_PRINT_PX and pushed _ITEMS_TOP down by 48px.
    Holding the old pin would have charged all 48px to the table. Since the totals
    block is in normal flow, the budget is now taken from the SHEET instead.

    2026-07-23: the answer to «more rows than the sheet holds» is no longer to
    compress them towards 17px and then clip the surplus — it is to paginate. So
    the budget is now a *page* capacity rather than a whole-invoice one, and the
    sheet must still be spent to the last px: every px left on the table here is
    a row that costs a whole extra sheet.
    """
    import math

    from app.ui.screens.saudi_invoice_print_v7 import (
        _ITEMS_MAX_H, _ITEMS_TOP, _ROW_BUDGET, _ROW_BUDGET_LAST, _ROW_UNIT,
        _SHEET_H, _SHEET_BOTTOM_MARGIN, _TOTALS_GAP, _TOTALS_H,
    )

    # Nothing may fall off the sheet at full stretch...
    foot = _ITEMS_TOP + _ITEMS_MAX_H + _TOTALS_GAP + _TOTALS_H
    assert foot <= _SHEET_H
    # ...and nothing may be left on the table either: the margin is exactly the
    # one declared, not accidental slack that costs rows.
    assert _SHEET_H - foot == pytest.approx(_SHEET_BOTTOM_MARGIN)

    # A sheet that carries the totals holds nine rows; one that does not gets the
    # totals block's height back, and holds thirteen.
    assert math.floor(_ROW_BUDGET_LAST / _ROW_UNIT) == 9
    assert math.floor(_ROW_BUDGET / _ROW_UNIT) == 13
    assert _ROW_BUDGET - _ROW_BUDGET_LAST == pytest.approx(_TOTALS_H + _TOTALS_GAP)


@pytest.mark.parametrize("build", _ALL_INVOICE_BUILDERS)
def test_a_long_item_name_never_disappears(build):
    # Whatever each layout does with it, the name must still be printed: a cell
    # that "fixed" the overflow by clipping would be worse than the bug.
    html = build(_long_name_data())
    assert "م" * 40 in html


@pytest.mark.parametrize("build", _ALL_INVOICE_BUILDERS)
def test_no_invoice_template_prints_a_signature(build):
    """Invoices carry no signature — the vouchers' line must not leak in here.

    :func:`signature_line_html` lives in this module because it is the shared home
    for print helpers, and that puts it one import away from every invoice
    template. But the user scoped the signature line to the vouchers and the
    statement, and explicitly kept the invoices out of it (2026-07-17); النموذج
    الخامس's own signature strip was dropped for the same reason. So an invoice
    growing one is a regression, not a nicety.
    """
    html = build(_sample_data())
    assert "التوقيع" not in html
    assert "توقيع" not in html


def test_signature_line_reads_rtl_with_the_dots_trailing_left():
    # `left` is physical (so the line lands left of an RTL sheet) and the element
    # is RTL (so the label leads and the neutral dots fall to its left, instead of
    # jumping to its right as they would under an LTR container).
    line = signature_line_html()
    assert "التوقيع: ........." in line
    assert "text-align:left" in line
    assert "direction:rtl" in line


def test_signature_line_takes_each_templates_own_type_scale():
    line = signature_line_html(font_size="15px", color="#000", extra_style="top:9px;")
    assert "font-size:15px" in line
    assert "color:#000" in line
    assert "top:9px;" in line


def test_amount_in_words_whole_riyals():
    words = amount_in_words_ar(Decimal("8464.00"))
    assert words.startswith("فقط ")
    assert "ريالاً" in words
    assert words.endswith("لا غير")
    # The conjunction و is attached to the following word (وأربعمائة), not spaced.
    assert " و " not in words


def test_amount_in_words_with_halalas():
    words = amount_in_words_ar(Decimal("100.50"))
    assert "هللة" in words


def test_build_invoice_html_is_ltr_structured_and_populated():
    html = build_invoice_html(_sample_data())
    # LTR page (no whole-document mirroring) — matches the approved mock-up.
    assert "<html lang=\"ar\">" in html
    assert "dir=\"rtl\"" not in html.split("<body>")[0]  # no page-level rtl
    # Key data made it into the document.
    assert "6285" in html
    assert "25-06-2026" in html
    assert "شركة ريوف المواسم" in html
    assert "المصنع روف الحال" in html
    assert "شكوالته بلجيكي" in html
    assert "R3962" in html
    assert "5,980.00" in html
    # Header grid preserved (title centered, QR right) via justify-self.
    assert "justify-self: start" in html
    assert "justify-self: end" in html
    # The logo was removed from the printout at the user's request.
    assert 'alt="logo"' not in html


def test_build_invoice_html_no_lines_placeholder():
    data = _sample_data()
    data["lines"] = []
    data["totals"] = {"subtotal": Decimal("0"), "vat": Decimal("0"), "total": Decimal("0")}
    html = build_invoice_html(data)
    assert "لا توجد أصناف" in html


def test_qr_data_uri_generates_png():
    uri = qr_data_uri("AR1234TEST")
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 100


def test_qr_data_uri_falls_back_without_payload():
    uri = qr_data_uri(None)
    # Falls back to the bundled placeholder asset (still a PNG data URI).
    assert uri.startswith("data:image/png;base64,")
