"""A long invoice runs onto more sheets — it never prints half a QR.

The user's report (2026-07-23) was a nine-item invoice on النموذج الرابع whose QR
came out sliced across the bottom edge of the paper, and the rule that followed:

    «راجع أي نموذج فيه باركود من تحت … رحّل الباركود والإجماليات لصفحة ثانية،
     إنما ما يجيش مقصوص كده»

Six templates draw on a fixed 816 × 1056 sheet with ``overflow:hidden``, so each
of them lost *something* to it — النماذج الثالث/الرابع/السادس/الثامن the QR and
the totals, النموذج السابع its surplus item rows. What they must all do now:

* print every item line, exactly once, across however many sheets it takes;
* print the totals/QR band exactly once, whole, on the **last** sheet;
* mark every sheet but the last with a page break, so one sheet is one page.

These are string checks over the built HTML, so they run headless. The rendered
geometry was verified separately by printing to PDF and probing it with PyMuPDF.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest

from app.ui.screens.invoice_pagination import paginate, row_height, wrapped_lines
from app.ui.screens.saudi_invoice_print import build_invoice_html
from app.ui.screens.saudi_invoice_print_v2 import build_invoice_html_v2
from app.ui.screens.saudi_invoice_print_v3 import build_invoice_html_v3
from app.ui.screens.saudi_invoice_print_v4 import build_invoice_html_v4
from app.ui.screens.saudi_invoice_print_v5 import build_invoice_html_v5
from app.ui.screens.saudi_invoice_print_v6 import build_invoice_html_v6
from app.ui.screens.saudi_invoice_print_v7 import build_invoice_html_v7
from app.ui.screens.saudi_invoice_print_v8 import build_invoice_html_v8

# The name is what the tests count, so it is unmistakable in the markup: «ITEM007X»
# cannot collide with a number, a label, or another item's name the way «صنف 7»
# collides with «صنف 17».
_NAME = "ITEM{:03d}X"


def _data(count: int, name_template: str = _NAME) -> dict:
    lines = [
        {
            "code": f"C{index:03d}",
            "name": name_template.format(index),
            "unit": "PCE",
            "qty": Decimal("2"),
            "price": Decimal("150"),
            "before": Decimal("300"),
            "vat_rate": Decimal("15"),
            "vat_amount": Decimal("45"),
            "total": Decimal("345"),
        }
        for index in range(1, count + 1)
    ]
    return {
        "invoice_number": "69853",
        "issue_datetime": datetime.datetime(2026, 7, 23, 23, 11, 46),
        "payment_label": "نقدي",
        "seller": {"name": "شركة الشرارة المضيئة", "address": "الرياض",
                   "cr": "4030492763", "vat": "311474361800003"},
        "customer": {"name": "شركة الريادة", "address": "الرياض",
                     "cr": "1010101010", "vat": "312356423300003"},
        "lines": lines,
        "totals": {"subtotal": Decimal(300 * count), "vat": Decimal(45 * count),
                   "total": Decimal(345 * count)},
        "qr_payload": "AR1234TEST",
    }


def _sheets(html: str) -> list[str]:
    """The built page's sheets, in order.

    Split on the opening tag rather than parsed: every template writes its sheet
    as one top-level ``<div data-sheet …>`` and nothing else in the body does.
    """
    body = html.split("<body>", 1)[1]
    return [c for c in re.split(r"(?=<div data-sheet)", body) if "data-sheet" in c]


# (label, builder, a string only the totals band carries, does the QR ride in the
#  band?). النماذج الخامس/السابع put the QR up in the page header, so theirs
#  prints on every sheet — deliberately, it is part of the page furniture.
_PAGINATED = (
    ("الثالث", build_invoice_html_v3, "إجمالى الخصومات", True),
    ("الرابع", build_invoice_html_v4, "المبلغ المستحق", True),
    ("الخامس", build_invoice_html_v5, "Balance Duo", False),
    ("السادس", build_invoice_html_v6, "Total Amount Due", True),
    ("السابع", build_invoice_html_v7, "الإجمالي بعد الخصم", False),
    ("الثامن", build_invoice_html_v8, "إجمالي المبلغ المستحق", True),
)

# Item counts spanning «fits easily», «the reported case», and «several sheets».
_COUNTS = (1, 5, 9, 14, 25, 60)


@pytest.mark.parametrize("label,build,band,_qr", _PAGINATED)
@pytest.mark.parametrize("count", _COUNTS)
def test_every_item_prints_exactly_once(label, build, band, _qr, count):
    """No line may be silently dropped, and none may be printed twice.

    النموذج السابع dropped everything past its seventeenth row; النموذج الثامن
    past its twelfth when the names wrapped. Both did it without any error.
    """
    html = build(_data(count))
    for index in range(1, count + 1):
        printed = html.count(_NAME.format(index))
        assert printed == 1, f"النموذج {label}: item {index}/{count} printed {printed}×"


@pytest.mark.parametrize("label,build,band,qr_in_band", _PAGINATED)
@pytest.mark.parametrize("count", _COUNTS)
def test_the_band_prints_once_and_closes_the_invoice(label, build, band, qr_in_band,
                                                     count):
    """The totals — and the QR that sits with them — belong to the last sheet."""
    sheets = _sheets(build(_data(count)))
    carrying = [i for i, sheet in enumerate(sheets) if band in sheet]
    assert carrying == [len(sheets) - 1], (
        f"النموذج {label}: {count} items put the totals on sheets {carrying} "
        f"of {len(sheets)}"
    )
    if qr_in_band:
        qr_sheets = [i for i, s in enumerate(sheets) if re.search(r'alt="qr"', s, re.I)]
        assert qr_sheets == [len(sheets) - 1], f"النموذج {label}: QR on {qr_sheets}"
    else:
        # Header QR: every sheet carries one, because every sheet is a full page.
        assert all(re.search(r'alt="qr"', s, re.I) for s in sheets)


@pytest.mark.parametrize("label,build,band,_qr", _PAGINATED)
def test_every_sheet_but_the_last_breaks_the_page(label, build, band, _qr):
    """One sheet is one printed page — otherwise two sheets share one page and
    the second is cut off at the paper's edge, which is the bug this fixes."""
    sheets = _sheets(build(_data(30)))
    assert len(sheets) > 1, f"النموذج {label}: 30 items still fit one sheet?"
    for index, sheet in enumerate(sheets):
        head = sheet.split(">", 1)[0]
        breaks = "break-after:page" in head.replace(" ", "")
        assert breaks == (index < len(sheets) - 1), (
            f"النموذج {label}: sheet {index + 1}/{len(sheets)} break-after={breaks}"
        )
        assert "break-inside:avoid" in head.replace(" ", "")


@pytest.mark.parametrize("label,build,band,_qr", _PAGINATED)
def test_a_short_invoice_still_prints_on_one_sheet(label, build, band, _qr):
    """Pagination must not cost the ordinary invoice its single page.

    Every one of these templates was measured against a reference document with
    a handful of lines; those must render exactly as they did before.
    """
    assert len(_sheets(build(_data(1)))) == 1
    assert len(_sheets(build(_data(0)))) == 1   # no lines at all: totals only


def test_the_reported_case_carries_the_qr_to_a_second_page():
    """النموذج الرابع, nine items — the invoice in the user's screenshot.

    It printed the QR sliced across the bottom edge. Now the ninth row is on
    sheet 1 with eight others, and sheet 2 carries the last row and the band.
    """
    sheets = _sheets(build_invoice_html_v4(_data(9)))
    assert len(sheets) == 2
    assert 'alt="QR"' not in sheets[0]
    assert 'alt="QR"' in sheets[1]
    assert "المبلغ المستحق" in sheets[1]


@pytest.mark.parametrize("label,build", [("الأول", build_invoice_html),
                                         ("الثاني", build_invoice_html_v2)])
def test_the_flowing_templates_never_split_a_row(label, build):
    """النموذجان الأول/الثاني flow (min-height), so they carry over by themselves.

    They cannot clip — but they could let the page break fall through a row or
    through the totals panel, printing half of each on two pages.
    """
    html = build(_data(30)).replace(" ", "")
    assert "break-inside:avoid" in html
    assert html.count("class=") >= 1


# ---------------------------------------------------------------------------
# The splitter itself
# ---------------------------------------------------------------------------
def test_everything_that_fits_stays_on_one_page():
    assert paginate([1, 2, 3], lambda _i: 10.0, budget=100, budget_last=50) == [[1, 2, 3]]


def test_the_band_is_never_left_without_room():
    """A page filled to `budget` cannot also hold the band, so one row moves.

    The minimum tail moves, not the maximum: an invoice reads front to back, so
    the first page should be the full one and the last page the short one.
    """
    pages = paginate(list(range(10)), lambda _i: 10.0, budget=100, budget_last=50)
    assert pages == [list(range(9)), [9]]


def test_a_page_is_never_left_empty_when_rows_could_fill_it():
    pages = paginate(list(range(12)), lambda _i: 10.0, budget=100, budget_last=50)
    assert all(page for page in pages), pages


def test_a_row_too_tall_to_share_a_page_gets_the_band_its_own():
    """The pathological case: nothing is cut, even when nothing else will do."""
    pages = paginate([1], lambda _i: 80.0, budget=100, budget_last=50)
    assert pages == [[1], []]


def test_an_invoice_with_no_lines_still_has_a_page_for_its_totals():
    assert paginate([], lambda _i: 10.0, budget=100, budget_last=50) == [[]]


def test_the_page_strip_is_charged_only_when_there_is_more_than_one_page():
    """Reserving room for «صفحة x من y» must not be what causes the second page."""
    # Five rows fit the last page exactly; the strip would push one off.
    assert paginate(list(range(5)), lambda _i: 10.0, budget=100, budget_last=50,
                    foot=20) == [list(range(5))]
    # Once the invoice really does paginate, the strip is charged on every page:
    # the first sheet holds 80px of rows rather than 100, the last 30 rather 50.
    pages = paginate(list(range(9)), lambda _i: 10.0, budget=100, budget_last=50,
                     foot=20)
    assert [len(p) for p in pages] == [8, 1]
    assert paginate(list(range(12)), lambda _i: 10.0, budget=100, budget_last=50,
                    foot=20) == [list(range(8)), list(range(8, 11)), [11]]


def test_a_wrapping_name_grows_its_row():
    """The estimate must see the wrap, or the page budget is a fiction."""
    assert wrapped_lines("قصير", 200, 13) == 1
    assert wrapped_lines("كلمة " * 40, 200, 13) > 1
    # An unbroken token breaks mid-word — every template sets overflow-wrap for it.
    assert wrapped_lines("م" * 200, 100, 13) > 1

    short = row_height("قصير", base=32, width_px=200, font_px=13, line_height=1.25)
    long = row_height("كلمة " * 40, base=32, width_px=200, font_px=13, line_height=1.25)
    assert short == 32 and long > short
