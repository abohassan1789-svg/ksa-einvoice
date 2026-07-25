"""النموذج الثامن keeps its geometry consistent as the item count grows.

The sheet is a fixed 816 × 1056 with ``overflow:hidden``: whatever does not fit
is cut off with no error and no warning. Two things must therefore hold however
many items an invoice carries, and neither is visible in a code review — the
first template to get this wrong (النموذج السادس) shipped rows that pushed its
own totals off the page.

Up to 2026-07-23 the answer here was *compression*: rows shrank towards 18px and
their text towards 10px, and past that the surplus rows were dropped so the
totals would survive. The user's rule replaced it with pagination — nothing is
compressed, nothing is dropped, a long invoice simply runs onto more sheets —
so what these tests hold to account is the split, not the squeeze.

These are string checks over the built HTML, so they run headless. The pixel
geometry itself was verified in a real browser when the template was written.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest

from app.ui.screens.saudi_invoice_print import QR_PRINT_PX
from app.ui.screens.saudi_invoice_print_v8 import (
    _ITEM_FONT,
    _ITEM_ROW_BUDGET,
    _ITEM_ROW_H,
    _ROW_BUDGET,
    _ROW_BUDGET_LAST,
    _SHEET_H,
    _row_height_v8,
    build_invoice_html_v8,
)


def _sheets(html: str) -> list[str]:
    """The built page's sheets, in order."""
    body = html.split("<body>", 1)[1]
    return [chunk for chunk in re.split(r"(?=<div data-sheet)", body) if "data-sheet" in chunk]

SHORT_NAME = "أعمال كهرباء"


def _data(count: int, name: str = SHORT_NAME) -> dict:
    lines = [
        {
            "name": f"{name} {i}",
            "qty": Decimal("2"),
            "price": Decimal("150"),
            "vat_rate": Decimal("15"),
            "vat_amount": Decimal("45"),
            "total": Decimal("345"),
        }
        for i in range(1, count + 1)
    ]
    return {
        "invoice_number": "985",
        "issue_datetime": datetime.datetime(2026, 7, 16, 10, 0),
        "seller": {
            "name": "شركة الشرارة المضيئة", "address": "الرياض",
            "cr": "4030492763", "vat": "311474361800003",
        },
        "customer": {
            "name": "شركة الريادة", "address": "", "cr": "",
            "vat": "312356423300003",
        },
        "lines": lines,
        "totals": {
            "subtotal": Decimal(300 * count),
            "vat": Decimal(45 * count),
            "total": Decimal(345 * count),
        },
        "qr_payload": "AR1234TEST",
    }


@pytest.mark.parametrize("count", [1, 3, 5, 12, 14, 20, 40])
def test_the_totals_always_reach_the_page(count: int) -> None:
    """No item count may cost the invoice its totals or its QR.

    They print once, on the last sheet, whole. If a future edit lets the rows
    push into the band's room again, this is what catches it.
    """
    html = build_invoice_html_v8(_data(count))
    assert "إجمالي المبلغ المستحق" in html, f"{count} items: the amount-due bar is gone"
    assert f"{345 * count:,}.00" in html, f"{count} items: the total itself is gone"
    assert html.count('alt="QR"') == 1, f"{count} items: the QR is gone or duplicated"
    assert "إجمالي المبلغ المستحق" in _sheets(html)[-1], "the band must close the invoice"


@pytest.mark.parametrize("count", [1, 3, 12, 13, 20, 40])
def test_no_item_is_ever_dropped(count: int) -> None:
    """Every line printed, exactly once, whatever the count.

    This is the regression the pagination exists for: the items band used to be
    clipped, so item 13 of a 13-item invoice simply was not on the paper.
    """
    html = build_invoice_html_v8(_data(count))
    for index in range(1, count + 1):
        assert html.count(f"{SHORT_NAME} {index}<") == 1, f"item {index} of {count}"


@pytest.mark.parametrize("count", [1, 5, 12, 14, 20, 40])
def test_rows_keep_the_mock_ups_height_and_size(count: int) -> None:
    """Nothing is compressed any more — page 7 of an invoice reads like page 1."""
    assert _row_height_v8({"name": f"{SHORT_NAME} {count}"}) == _ITEM_ROW_H
    html = build_invoice_html_v8(_data(count))
    assert f"font-size:{_ITEM_FONT}px" in html
    assert "min-height:32px" in html.replace(" ", "")


def test_the_row_budget_matches_the_sheet() -> None:
    """The budget is what is genuinely left over, not a number someone liked.

    It was wrong once already: the bands were declared 140px and 210px while
    rendering at 160 and 221, because a flex item never shrinks below its own
    content — so the budget over-counted by 31px.
    """
    from app.ui.screens.saudi_invoice_print_v8 import (
        _ABOVE_ROWS, _BELOW_ROWS, _FOOTER_H, _GAP, _PAD_BOTTOM,
    )

    assert _ITEM_ROW_BUDGET == _SHEET_H - _ABOVE_ROWS - _BELOW_ROWS
    assert _ROW_BUDGET_LAST == _ITEM_ROW_BUDGET
    # A sheet without the band gets the band's room back — and nothing more.
    assert _ROW_BUDGET == _SHEET_H - _ABOVE_ROWS - (_GAP + _FOOTER_H + _PAD_BOTTOM)
    assert _ROW_BUDGET_LAST > 0 and _ROW_BUDGET > _ROW_BUDGET_LAST


@pytest.mark.parametrize(
    "count,pages", [(1, 1), (12, 1), (13, 2), (19, 2), (31, 2), (32, 3)]
)
def test_the_split_is_where_the_geometry_says_it_is(count: int, pages: int) -> None:
    """Twelve rows beside the band, nineteen without it — so 12/19/19/…

    Not a preference: 12 × 32px is what fits above the QR/totals band, and the
    band is the only thing that changes between a last sheet and any other.
    """
    assert len(_sheets(build_invoice_html_v8(_data(count)))) == pages


def test_a_wrapping_name_is_charged_for_the_room_it_takes() -> None:
    """A name that wraps grows its row, and the budget has to know.

    ``min-height`` is a floor, not a ceiling. This is what used to make rows
    disappear at twelve items with wrapping names while a one-line invoice
    managed twenty: the count said the rows were 32px and the page disagreed.
    """
    long_name = "خدمة " * 30
    assert _row_height_v8({"name": long_name}) > _ITEM_ROW_H
    # …and it costs pages, rather than costing items.
    data = _data(12)
    for line in data["lines"]:
        line["name"] = long_name
    assert len(_sheets(build_invoice_html_v8(data))) > 1


def test_the_qr_is_the_first_templates_size() -> None:
    # User request (2026-07-16): النموذج الأول's QR size, not a size of its own.
    html = build_invoice_html_v8(_data(3))
    tag = next(t for t in re.findall(r"<img[^>]*>", html) if 'alt="QR"' in t)
    assert f"width:{QR_PRINT_PX}px" in tag
    assert f"height:{QR_PRINT_PX}px" in tag


def test_no_supply_date_and_no_pincode() -> None:
    # The two fields dropped from النموذج السادس earlier the same day were never
    # to be reintroduced by a new template.
    html = build_invoice_html_v8(_data(3))
    for needle in ("تاريخ التوريد", "Date of Supply", "الرمز السري", "Pincode"):
        assert needle not in html, f"النموذج الثامن reintroduces «{needle}»"


def test_an_empty_customer_field_prints_a_dash_not_a_gap() -> None:
    html = build_invoice_html_v8(_data(3))
    assert "—" in html  # the customer has no address and no CR
    assert "312356423300003" in html  # but the VAT it does have still prints
