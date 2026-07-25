"""The three Customer Statement print templates (كشف حساب العميل).

Added 2026-07-16 with the templates themselves. These are string/logic checks over
the built HTML, so they run headless; the printed geometry, the A4 orientations
and the pagination were verified by exporting real PDFs through the production
QWebEngine path when the templates were written.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.ui.screens.customer_statement_print import (
    STATEMENT_TEMPLATE_OPTIONS,
    _BUILDERS,
    build_statement_html_v1,
    build_statement_html_v2,
    build_statement_html_v3,
    rows_with_balance,
)

BUILDERS = [
    ("النموذج الأول", build_statement_html_v1),
    ("النموذج الثاني", build_statement_html_v2),
    ("النموذج الثالث", build_statement_html_v3),
]


def _header_labels(html_text: str) -> list[str]:
    """The table column headers — the <th> text — in document order."""
    return [h.strip() for h in re.findall(r"<th[^>]*>([^<]*)</th>", html_text)]

# An inline style attribute containing a font-family: the stacks carry double
# quotes, which close the attribute early and silently drop everything after it.
# This is the trap that cost النموذج الرابع its whole typography — see
# tests/unit/test_invoice_print_font_regression.py.
_INLINE_FONT_FAMILY = re.compile(r'style="[^"]*font-family:')


def _row(date: str, invoice: str, debit: str, credit: str, description: str) -> dict:
    return {
        "transaction_date": date,
        "customer_name": "شركة الريادة",
        "company_name": "شركة الشرارة المضيئة",
        "sales_invoice_number": invoice,
        "debit": Decimal(debit),
        "credit": Decimal(credit),
        "description": description,
    }


ROWS = [
    _row("2026-03-04", "985", "65550.00", "0.00", "فاتورة مبيعات آجلة رقم 985"),
    _row("2026-04-12", "-", "0.00", "20000.00", "سند قبض رقم PA-014"),
    _row("2026-05-20", "991", "12650.00", "0.00", "فاتورة مبيعات نقدية رقم 991"),
]


def _data(rows=None) -> dict:
    rows = ROWS if rows is None else rows
    debit = sum((r["debit"] for r in rows), Decimal("0"))
    credit = sum((r["credit"] for r in rows), Decimal("0"))
    return {
        "title": "كشف حساب العميل",
        "company_name": "شركة الشرارة المضيئة",
        "tax_number": "311474361800003",
        "customer_label": "شركة الريادة",
        "company_label": "الكل",
        "date_from_label": "2026-01-01",
        "date_to_label": "2026-07-16",
        "rows": rows,
        "summary": {
            "total_debit_label": f"{debit:,.2f}",
            "total_credit_label": f"{credit:,.2f}",
            "difference_label": f"{debit - credit:,.2f}",
        },
        "is_empty": not rows,
        "empty_message": "لا توجد حركات للعميل خلال الفترة المحددة",
        "company": COMPANY,
    }


# The letterhead the screen passes: a `companies` row, verbatim. `logo` is raw
# bytes in the database, so it is raw bytes here too — company_logo_data_uri does
# the base64/data-URI work and must be exercised on what it will really get.
COMPANY = {
    "id": 11,
    "name_ar": "شركة الشرارة المضيئة",
    "name_en": "Bright Spark Company",
    "commercial_registration": "4030492763",
    "vat_number": "311474361800003",
    "address_ar": "الرياض",
    "address_en": "Riadh",
    "logo": b"\x89PNG\r\n\x1a\nFAKE",
    "logo_mime": "image/png",
}


# ======================================================================
# The company letterhead (all three templates, 2026-07-17)
# ======================================================================
@pytest.mark.parametrize("label,build", BUILDERS)
def test_letterhead_prints_the_company_in_both_languages_with_its_logo(label, build) -> None:
    html = build(_data())
    assert "شركة الشرارة المضيئة" in html, f"{label}: no Arabic company name"
    assert "Bright Spark Company" in html, f"{label}: no English company name"
    assert "311474361800003" in html and "4030492763" in html
    # The logo is embedded from the row's bytes, not a path or a URL.
    assert "data:image/png;base64," in html


@pytest.mark.parametrize("label,build", BUILDERS)
def test_letterhead_puts_arabic_right_english_left_and_the_logo_between(label, build) -> None:
    """Source order IS the layout here, so it is worth pinning.

    The document is `<html dir="rtl">`: grid items are placed right-to-left, so
    this same source order without `direction:ltr` on the track would print the
    English block on the *right*. The grid is what makes the requested
    «عربي يمين / إنجليزي شمال» true.
    """
    html = build(_data())
    assert "direction:ltr; display:grid" in html
    english = html.index("Bright Spark Company")
    logo = html.index("data:image/png;base64,")
    arabic = html.index("شركة الشرارة المضيئة")
    assert english < logo < arabic, f"{label}: letterhead columns out of order"


@pytest.mark.parametrize("label,build", BUILDERS)
def test_no_letterhead_when_the_filter_spans_every_company(label, build) -> None:
    """«الكل» has no single issuer, so the sheet must not claim one."""
    data = {**_data(), "company": None}
    html = build(data)
    assert "Bright Spark Company" not in html
    assert "data:image/png;base64," not in html
    assert "كشف حساب العميل" in html, f"{label}: dropped the title along with it"


def test_company_is_no_longer_a_row_among_the_details() -> None:
    # النموذج الثالث listed «اسم الشركة» / «الرقم الضريبي» in a tint box between the
    # header and the table. The user asked for it in the letterhead instead — and
    # only there.
    html = build_statement_html_v3(_data())
    assert "اسم الشركة" not in html
    assert html.count("311474361800003") == 2   # the letterhead's two languages


# ======================================================================
# A long customer name must not mangle the fields beside it
# ======================================================================
_LONG_NAME = "مؤسسة الشرارة المضيئة للمقاولات العامة والتشغيل والصيانة المحدودة"
_UNBROKEN_NAME = "م" * 90


@pytest.mark.parametrize("name", [_LONG_NAME, _UNBROKEN_NAME])
def test_third_templates_bar_keeps_the_period_whole_beside_a_long_name(name) -> None:
    """The bar was flex, so a long name starved the date fields.

    Measured on the printed PDF before this: «الفترة» broke mid-value across four
    lines («01-01-2026 / -2026 — / 17-07») and «تاريخ الإصدار» (since removed) was
    squeezed off the bar altogether. A grid with a fixed track for the period,
    `nowrap` on its value, and `minmax(0,1fr)` for the name is what confines the
    wrapping to the name's own column.
    """
    html = build_statement_html_v3({**_data(), "customer_label": name})
    bar = html.split('background:#1A2A6C; color:#fff; display:grid')[1].split("</div>")[0]
    # The name gets the only flexible track; the period gets its own and may not wrap.
    assert "grid-template-columns:minmax(0,1fr) auto" in bar
    assert bar.count("white-space:nowrap") == 1, "the period must be unwrappable"
    assert name in html


# ======================================================================
# «تاريخ الإصدار» — removed from all three (user, 2026-07-17)
# ======================================================================
@pytest.mark.parametrize("label,build", BUILDERS)
def test_no_template_stamps_an_issue_date(label, build) -> None:
    """None of the three may print when the sheet was produced.

    Note this is only about the *statement*: the invoice templates' «تاريخ الإصدار»
    is the invoice's own issue date, a required field on a tax invoice, and is a
    different thing entirely.
    """
    html = build(_data())
    assert "تاريخ الإصدار" not in html, f"النموذج {label} still stamps an issue date"


@pytest.mark.parametrize("label,build", BUILDERS)
def test_templates_do_not_read_issued_at_at_all(label, build) -> None:
    # The screen no longer sends the key. A template reaching for it again would
    # print an empty label rather than fail, so pin that none of them does.
    stamped = build({**_data(), "issued_at": "2026-07-16 10:30"})
    assert "2026-07-16 10:30" not in stamped


# ======================================================================
# The running balance (النموذج الثاني's one derived value)
# ======================================================================
def test_the_running_balance_is_a_cumulative_debit_minus_credit() -> None:
    balances = [balance for _row, balance in rows_with_balance(ROWS)]
    assert balances == [Decimal("65550.00"), Decimal("45550.00"), Decimal("58200.00")]


def test_the_final_running_balance_equals_the_reported_difference() -> None:
    """The balance column and the totals bar must never disagree.

    They are computed independently — the balance walks the rows, the difference
    comes from the service's own totals — so a reader comparing the last row to
    the closing figure is checking two separate calculations. If they can drift,
    the statement is worthless.
    """
    _row_, final = rows_with_balance(ROWS)[-1]
    summary = _data()["summary"]
    assert f"{final:,.2f}" == summary["difference_label"]


def test_the_running_balance_stays_decimal() -> None:
    # Money is Decimal throughout this codebase, never float — a float running
    # balance would print 0.1 + 0.2 artefacts on a tax document.
    for _row_, balance in rows_with_balance(ROWS):
        assert isinstance(balance, Decimal)


def test_the_balance_handles_an_empty_statement() -> None:
    assert rows_with_balance([]) == []


def test_the_balance_survives_rows_with_missing_money() -> None:
    # A defensive row (None debit/credit) must not raise mid-print.
    rows = [{"debit": None, "credit": None, "description": "x"}]
    assert rows_with_balance(rows) == [(rows[0], Decimal("0.00"))]


# ======================================================================
# Every template
# ======================================================================
@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_emits_a_full_document(label: str, builder) -> None:
    html = builder(_data())
    assert html.lstrip().startswith("<!DOCTYPE html>"), label
    assert 'dir="rtl"' in html, label


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_prints_the_movements_and_the_totals(label: str, builder) -> None:
    html = builder(_data())
    assert "65,550.00" in html, f"{label}: a movement is missing"
    assert "20,000.00" in html, f"{label}: a movement is missing"
    assert "78,200.00" in html, f"{label}: the debit total is missing"
    assert "58,200.00" in html, f"{label}: the difference is missing"
    assert "سند قبض رقم PA-014" in html, f"{label}: a description is missing"


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_repeats_its_header_across_pages(label: str, builder) -> None:
    """A statement's row count is unbounded, so it *will* paginate.

    ``display:table-header-group`` is what carries the column headers onto page 2
    and beyond; without it a long statement's later pages are unreadable columns
    of numbers. Verified against a real 4-page PDF when written.
    """
    html = builder(_data())
    assert "table-header-group" in html, f"{label}: headers would not repeat"
    assert "page-break-inside" in html, f"{label}: rows could split across the fold"


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_handles_the_empty_statement(label: str, builder) -> None:
    html = builder(_data(rows=[]))
    assert "لا توجد حركات للعميل خلال الفترة المحددة" in html, label


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_no_template_declares_a_font_inline(label: str, builder) -> None:
    offenders = _INLINE_FONT_FAMILY.findall(builder(_data()))
    assert not offenders, (
        f"{label}: {len(offenders)} inline style attribute(s) declare font-family. "
        f"The stack's double quotes end the attribute there and every later "
        f"declaration is dropped. Move the font to the <style> block."
    )


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_no_template_invents_an_opening_balance(label: str, builder) -> None:
    """The query reads nothing before the period, so there is no opening balance.

    Printing one would be a fabricated number on a financial document.
    """
    assert "الرصيد الافتتاحي" not in builder(_data()), label


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_no_template_prints_a_static_page_number(label: str, builder) -> None:
    """«1 / 1» cannot be honest here: Chromium decides the page count at print
    time, and these statements really do run to several pages."""
    assert "1 / 1" not in builder(_data()), label


_EXPECTED_HEADERS = {
    "النموذج الأول": ["التاريخ", "البيان", "المدين", "الدائن"],
    "النموذج الثاني": ["التاريخ", "البيان", "مدين", "دائن"],
    "النموذج الثالث": ["التاريخ", "البيان", "المدين", "الدائن"],
}


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_shows_only_the_four_columns(label: str, builder) -> None:
    # User 2026-07-23: the statement carries exactly التاريخ / البيان / مدين / دائن.
    # اسم العميل, اسم الشركة, the movement-number column and النموذج الثاني's per-row
    # balance column were all removed.
    heads = _header_labels(builder(_data()))
    assert heads == _EXPECTED_HEADERS[label], f"{label}: columns are {heads}"
    for gone in ("اسم العميل", "اسم الشركة", "فاتورة المبيعات", "الفاتورة", "الرصيد"):
        assert gone not in heads, f"{label}: still has the «{gone}» column"


def test_v2_keeps_the_closing_balance_but_no_per_row_balance_column() -> None:
    # النموذج الثاني's per-row running-balance column is gone, but the account
    # balance survives as the «الرصيد المستحق» card and the dark closing row.
    html = build_statement_html_v2(_data())
    assert "الرصيد" not in _header_labels(html)          # no per-row balance column
    assert "الرصيد المستحق" in html                       # the summary card stays
    assert "الرصيد الختامي المستحق على العميل" in html    # and the closing total


@pytest.mark.parametrize("label,build", BUILDERS)
def test_a_company_without_a_logo_still_gets_a_letterhead(label, build) -> None:
    """Two of the six companies have no logo — the letterhead must degrade, not break.

    (This test used to assert no template *ever* emitted a logo, which was true
    only because the screen hard-coded `logo: ""`. The real requirement is that a
    missing logo produces no <img>, while the names and numbers still print.)
    """
    company = {**COMPANY, "logo": None, "logo_mime": None}
    html = build({**_data(), "company": company})
    assert 'alt="logo"' not in html
    assert "شركة الشرارة المضيئة" in html
    assert "Bright Spark Company" in html
    assert "كشف حساب العميل" in html


def test_v3_signature_is_the_one_shared_bottom_left_line() -> None:
    # النموذج الثالث's «توقيع المحاسب» rule was replaced by the same line the
    # receipt vouchers print, at the sheet's physical left.
    html = build_statement_html_v3(_data())
    assert "توقيع المحاسب" not in html
    assert html.count("التوقيع: .........") == 1
    signature_div = [c for c in html.split("<div ") if "التوقيع: ........." in c]
    assert len(signature_div) == 1
    assert "text-align:left" in signature_div[0]


def test_only_v3_carries_a_signature() -> None:
    # النموذج الأول/الثاني never printed one and must not gain one.
    for build in (build_statement_html_v1, build_statement_html_v2):
        assert "التوقيع" not in build(_data()), f"{build.__name__} grew a signature"


def test_orientation_is_declared_per_template() -> None:
    # النموذج الأول/الثاني stay landscape, النموذج الثالث is a portrait letterhead.
    # statement_page_layout() reads these.
    assert build_statement_html_v1.landscape is True
    assert build_statement_html_v2.landscape is True
    assert build_statement_html_v3.landscape is False


def test_every_picker_option_has_a_builder() -> None:
    """A fourth option added to the picker without a builder would silently fall
    back to النموذج الأول — the exact bug the invoice dispatch test guards."""
    assert len(STATEMENT_TEMPLATE_OPTIONS) == len(_BUILDERS)
    assert sorted(_BUILDERS) == [1, 2, 3]
