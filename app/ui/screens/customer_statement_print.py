"""Printable Customer Statement (كشف حساب العميل) — three templates.

Approved in-conversation 2026-07-16. The screen offers the same template picker
the sales-invoice screen uses, on معاينة / طباعة / تصدير PDF:

All three print the SAME four columns — التاريخ / البيان / مدين / دائن (user
2026-07-23); they differ in identity and layout, not in what they carry. The
customer and company appear once, in the letterhead and the filter chips, and
the movement number is folded into the البيان («سند قبض رقم …»), never a column.

* :func:`build_statement_html_v1` — «النموذج الأول», the classic green layout,
  the applied filters as chips and the totals under a green rule. A4 **landscape**.
* :func:`build_statement_html_v2` — «النموذج الثاني», a bank-style statement:
  debit/credit coloured, totals as cards, and a dark closing totals row. A4
  landscape.
* :func:`build_statement_html_v3` — «النموذج الثالث», a formal letterhead sheet
  in النموذج الثامن's navy identity, so statements and invoices look like one
  set of documents. A4 **portrait**.

Why this module renders through ``QWebEngineView`` and not the shared
``PrintManager`` / ``PdfExporter`` the report used before
-----------------------------------------------------------------------
The old path had *two* renderers behind one report: ``QTextDocument`` (Qt rich
text) for preview/print and ReportLab for PDF, so every design had to be built
twice and could still diverge. More decisively, ``QTextDocument`` was measured
against these designs before they were drawn: it **ignores ``display:flex``**
(side-by-side cards stack) and **drops every ``border-radius``**. None of the
three approved layouts is renderable by it at all.

Chromium renders one HTML for preview, print *and* PDF — one source of truth,
the engine the invoice templates already use. It also **paginates**, which
matters far more here than on an invoice: a statement's row count is unbounded,
so the fixed-sheet approach every invoice template uses (and its silent clipping)
would be exactly wrong. Nothing here uses a fixed height; the table flows and
``thead`` repeats on every page.

``PrintManager`` / ``PdfExporter`` are untouched — Status Analysis and Daily
Follow-up still share them, and Excel export still goes through ``ExcelExporter``.

Presentation only: no database access. Every field printed comes straight from
the read-only service; the templates derive nothing of their own. (The
``rows_with_balance`` helper is kept — correct and unit-tested — for a future
balance view, but no template currently prints a per-row balance column.)
"""

from __future__ import annotations

import html
from decimal import Decimal
from typing import Any, Callable

from PySide6.QtCore import QMarginsF, Qt, QTimer, QUrl
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.ui.common.saudi_invoice_style import si_icon, style_button
from app.ui.screens.saudi_invoice_print import (
    choose_invoice_template,
    company_logo_data_uri,
    signature_line_html,
)

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
_GREEN = "#137A38"      # the reports' existing brand green — النموذج الأول keeps it
_GREEN_TINT = "#EFF5F0"
_NAVY = "#1A2A6C"       # النموذج الثامن's accent — النموذج الثالث shares it
_NAVY_ON = "#B9C4E8"
_INK = "#0F172A"        # النموذج الثاني's near-black
_DEBIT = "#A32D2D"
_CREDIT = "#0F6E56"
_MUTED = "#94A3B8"
_RULE = "#E2E8F0"
_TINT = "#F1F4FB"
_ZEBRA = "#F8FAFC"

_FONTS = 'Tahoma, Arial, "Segoe UI", "Helvetica Neue", sans-serif'

STATEMENT_TEMPLATE_OPTIONS: tuple[str, ...] = (
    "النموذج الأول",
    "النموذج الثاني",
    "النموذج الثالث",
)

_ZERO = Decimal("0.00")

# Printed page margin, in millimetres — comfortably outside the unprintable edge
# every common office printer has, and enough for a hole-punch on the statement.
_MARGIN_MM = 12.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _money(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:  # noqa: BLE001 - defensive formatting only
        return "0.00"


def _dash(value: Any) -> str:
    """An empty cell prints «—», so a blank reads as *absent*, not as a fault."""
    text = _esc(value).strip()
    return text if text and text != "-" else f'<span style="color:{_MUTED};">—</span>'


def rows_with_balance(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Decimal]]:
    """Pair every row with the running balance after it.

    The balance is *derived*, not fetched: the service already returns the debit
    and credit of each row in a deterministic order (transaction_date, then
    source_sequence, then source_id), so the cumulative sum is well defined and
    reproducible across preview / print / PDF.

    Note this is a period balance, not an account balance: the statement has no
    opening balance because the query does not read anything before the period.
    النموذج الثاني therefore labels its last figure «الرصيد المستحق» over the
    period — never «الرصيد الافتتاحي».
    """
    out: list[tuple[dict[str, Any], Decimal]] = []
    balance = _ZERO
    for row in rows:
        debit = row.get("debit") or _ZERO
        credit = row.get("credit") or _ZERO
        balance = balance + Decimal(str(debit)) - Decimal(str(credit))
        out.append((row, balance))
    return out


def _document(body: str, *, landscape: bool) -> str:
    """Wrap a template body in the shared print document.

    ``thead{display:table-header-group}`` is what repeats the column headers on
    every printed page, and ``page-break-inside:avoid`` on rows stops a line item
    being sliced in half across the fold. Both only matter because this document
    flows — see the module docstring.
    """
    width = "1123px" if landscape else "794px"
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    font-family: {_FONTS};
    color: #111;
    background: #fff;
    -webkit-font-smoothing: antialiased;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  .sheet {{ width: {width}; margin: 0 auto; padding: 18px 20px; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  thead {{ display: table-header-group; }}
  tr {{ page-break-inside: avoid; }}
  td, th {{ overflow-wrap: anywhere; }}
  .num {{ direction: ltr; }}
  @media print {{
    .sheet {{ width: auto; padding: 0; }}
  }}
</style>
</head>
<body><div class="sheet">{body}</div></body>
</html>"""


def _letterhead(data: dict[str, Any], *, accent: str, rule: str) -> str:
    """The company's letterhead: Arabic right, logo centre, English left.

    Added 2026-07-17 at the user's request. Before it, the company appeared only as
    a grey subtitle (النموذج الأول/الثاني) or a row among the details (النموذج
    الثالث) — and in practice not at all, because the screen fed it the legacy
    ``company_settings`` table, which is empty on this installation. It now reads
    ``data["company"]``: the row of the company picked in the statement's own
    filter, straight from ``companies``.

    ``None`` (the «الكل» filter) prints nothing at all: a statement spanning several
    companies has no single issuer, and heading it with one of them would be a
    claim about the document that is not true.

    The three columns are a grid, not flex, and the side columns are ``minmax(0,1fr)``
    so a long company name wraps inside its own column instead of shoving the logo
    off centre — the failure النموذج الثالث's header bar already had.
    """
    company = data.get("company")
    if not isinstance(company, dict):
        return ""

    name_ar = _esc(company.get("name_ar"))
    name_en = _esc(company.get("name_en"))
    vat = _esc(company.get("vat_number"))
    cr = _esc(company.get("commercial_registration"))
    address_ar = _esc(company.get("address_ar"))
    address_en = _esc(company.get("address_en") or company.get("address_ar"))
    logo = company_logo_data_uri(company)

    def line(text: str, *, size: str = "9.5px", weight: str = "400") -> str:
        return (
            f'<div style="font-size:{size}; font-weight:{weight}; color:#5A6180; '
            f'margin-top:2px; overflow-wrap:anywhere;">{text}</div>'
            if text
            else ""
        )

    arabic = (
        f'<div dir="rtl" style="text-align:right; min-width:0;">'
        f'<div style="font-size:14px; font-weight:700; color:{accent}; '
        f'overflow-wrap:anywhere;">{name_ar}</div>'
        + line(f"الرقم الضريبي: {vat}" if vat else "")
        + line(f"السجل التجاري: {cr}" if cr else "")
        + line(address_ar)
        + "</div>"
    )
    english = (
        f'<div dir="ltr" style="text-align:left; min-width:0;">'
        f'<div style="font-size:13px; font-weight:700; color:{accent}; '
        f'overflow-wrap:anywhere;">{name_en}</div>'
        + line(f"VAT No.: {vat}" if vat else "")
        + line(f"C.R. No.: {cr}" if cr else "")
        + line(address_en)
        + "</div>"
    )
    centre = (
        f'<div style="display:flex; align-items:center; justify-content:center;">'
        f'<img src="{logo}" alt="logo" style="max-width:130px; max-height:64px; '
        f'width:auto; height:auto; object-fit:contain; display:block;" /></div>'
        if logo
        else "<div></div>"   # the column still occupies its third, keeping symmetry
    )
    # `direction:ltr` on the grid is load-bearing, not decoration: the document is
    # `<html dir="rtl">`, so grid items are placed right-to-left and this same
    # source order would put the ENGLISH block on the right. Pinning the track
    # direction makes "English left, Arabic right" true regardless of what the
    # surrounding document does; each block still sets its own `dir` for its text.
    return (
        f'<div style="direction:ltr; display:grid; '
        f'grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); '
        f'gap:14px; align-items:center; padding-bottom:10px; margin-bottom:10px; '
        f'border-bottom:2px solid {rule};">'
        f"{english}{centre}{arabic}</div>"
    )


def _empty_note(data: dict[str, Any]) -> str:
    if not data.get("is_empty"):
        return ""
    message = _esc(data.get("empty_message") or "لا توجد حركات")
    return (
        f'<div style="text-align:center; padding:28px 10px; color:#64748B; '
        f'font-size:13px; border:1px dashed {_RULE}; margin-top:10px;">{message}</div>'
    )


# ---------------------------------------------------------------------------
# النموذج الأول — the classic green sheet
# ---------------------------------------------------------------------------
def build_statement_html_v1(data: dict[str, Any]) -> str:
    """The report's long-standing green look — four columns only: التاريخ / البيان
    / المدين / الدائن (user 2026-07-23). اسم العميل and اسم الشركة now live in the
    letterhead and the filter chips, not as repeated per-row columns, and the
    movement number is folded into the البيان («سند قبض رقم …»)."""
    chips = "".join(
        f'<span style="background:{_GREEN_TINT}; color:{_GREEN}; padding:3px 9px; '
        f'border-radius:3px;">{_esc(text)}</span>'
        for text in (
            f"العميل: {data.get('customer_label') or 'الكل'}",
            f"الشركة: {data.get('company_label') or 'الكل'}",
            f"الفترة: {data.get('date_from_label')} إلى {data.get('date_to_label')}",
        )
    )
    head = "".join(
        f'<th style="background:{_GREEN}; color:#fff; font-weight:700; padding:7px 4px; '
        f'width:{width};">{label}</th>'
        for label, width in (
            ("التاريخ", "14%"), ("البيان", "46%"),
            ("المدين", "20%"), ("الدائن", "20%"),
        )
    )
    body = []
    for row in data.get("rows") or []:
        cell = ('<td style="border:1px solid #D1D5DB; padding:5px 4px; '
                'text-align:center;">')
        body.append(
            "<tr>"
            f'{cell}{_esc(row.get("transaction_date"))}</td>'
            '<td style="border:1px solid #D1D5DB; padding:5px 6px; text-align:right;">'
            f'{_esc(row.get("description"))}</td>'
            f'{cell}<span class="num">{_money(row.get("debit"))}</span></td>'
            f'{cell}<span class="num">{_money(row.get("credit"))}</span></td>'
            "</tr>"
        )
    summary = data.get("summary") or {}
    table = (
        f'<table style="font-size:10.5px;"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
        if body
        else ""
    )
    return _document(
        # The company is the letterhead now, not a grey subtitle under the title.
        f'{_letterhead(data, accent=_GREEN, rule=_GREEN)}'
        f'<div style="text-align:center; font-size:20px; font-weight:700; color:{_GREEN};">'
        f'{_esc(data.get("title"))}</div>'
        f'<div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center; '
        f'margin:12px 0; font-size:10.5px;">{chips}</div>'
        f"{table}{_empty_note(data)}"
        f'<div style="border-top:2px solid {_GREEN}; margin:10px 0 7px;"></div>'
        f'<div style="text-align:left; font-size:11.5px; font-weight:700;">'
        f'إجمالي المدين: <span class="num">{_esc(summary.get("total_debit_label"))}</span>'
        "&nbsp;&nbsp;|&nbsp;&nbsp;"
        f'إجمالي الدائن: <span class="num">{_esc(summary.get("total_credit_label"))}</span>'
        "&nbsp;&nbsp;|&nbsp;&nbsp;"
        f'الفرق: <span class="num">{_esc(summary.get("difference_label"))}</span></div>',
        landscape=True,
    )


build_statement_html_v1.landscape = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# النموذج الثاني — bank-style, with a running balance
# ---------------------------------------------------------------------------
def _stat_card(label: str, value: str, *, bg: str, label_color: str,
               value_color: str) -> str:
    return (
        f'<div style="background:{bg}; padding:7px 13px; border-radius:5px; '
        f'text-align:center; min-width:104px;">'
        f'<div style="font-size:9.5px; color:{label_color};">{label}</div>'
        f'<div class="num" style="font-size:14px; font-weight:700; color:{value_color};">'
        f"{value}</div></div>"
    )


def build_statement_html_v2(data: dict[str, Any]) -> str:
    """Bank-style: coloured debit/credit and a dark closing totals row.

    Four columns only — التاريخ / البيان / مدين / دائن (user 2026-07-23). The
    per-row running-balance column and the الفاتورة column were dropped; the
    account balance survives as the «الرصيد المستحق» card and the closing row's
    total, which is where a reader looks for it anyway."""
    summary = data.get("summary") or {}
    cards = (
        _stat_card("إجمالي المدين", _esc(summary.get("total_debit_label")),
                   bg="#FCEBEB", label_color=_DEBIT, value_color="#501313")
        + _stat_card("إجمالي الدائن", _esc(summary.get("total_credit_label")),
                     bg="#E1F5EE", label_color=_CREDIT, value_color="#04342C")
        + _stat_card("الرصيد المستحق", _esc(summary.get("difference_label")),
                     bg=_INK, label_color=_MUTED, value_color="#ffffff")
    )
    head = "".join(
        f'<th style="padding:7px 4px; font-weight:700; width:{width}; '
        f'text-align:{align}; color:{color};">{label}</th>'
        for label, width, align, color in (
            ("التاريخ", "12%", "center", "#334155"),
            ("البيان", "auto", "right", "#334155"),
            ("مدين", "20%", "center", _DEBIT),
            ("دائن", "20%", "center", _CREDIT),
        )
    )
    body = []
    for index, row in enumerate(data.get("rows") or []):
        debit = Decimal(str(row.get("debit") or _ZERO))
        credit = Decimal(str(row.get("credit") or _ZERO))
        zebra = f" background:{_ZEBRA};" if index % 2 else ""
        cell = (f'<td style="border-bottom:1px solid {_RULE}; padding:6px 4px; '
                f'text-align:center;{zebra}">')
        # A zero side prints «—», not «0.00»: on a ledger the eye should land on
        # the side that actually moved.
        debit_text = (f'<span class="num" style="color:{_DEBIT};">{_money(debit)}</span>'
                      if debit else f'<span style="color:{_MUTED};">—</span>')
        credit_text = (f'<span class="num" style="color:{_CREDIT};">{_money(credit)}</span>'
                       if credit else f'<span style="color:{_MUTED};">—</span>')
        body.append(
            "<tr>"
            f'{cell}{_esc(row.get("transaction_date"))}</td>'
            f'<td style="border-bottom:1px solid {_RULE}; padding:6px 6px; '
            f'text-align:right;{zebra}">{_esc(row.get("description"))}</td>'
            f"{cell}{debit_text}</td>"
            f"{cell}{credit_text}</td>"
            "</tr>"
        )
    closing = (
        f'<tr style="background:{_INK}; color:#fff;">'
        f'<td colspan="2" style="padding:8px 6px; font-weight:700;">'
        "الرصيد الختامي المستحق على العميل: "
        f'<span class="num">{_esc(summary.get("difference_label"))}</span></td>'
        f'<td style="padding:8px 4px; text-align:center;" class="num">'
        f'{_esc(summary.get("total_debit_label"))}</td>'
        f'<td style="padding:8px 4px; text-align:center;" class="num">'
        f'{_esc(summary.get("total_credit_label"))}</td></tr>'
        if body
        else ""
    )
    table = (
        f'<table style="font-size:10.5px;">'
        f'<thead><tr style="border-bottom:1.5px solid {_INK};">{head}</tr></thead>'
        f'<tbody>{"".join(body)}{closing}</tbody></table>'
        if body
        else ""
    )
    return _document(
        f'{_letterhead(data, accent=_INK, rule=_INK)}'
        '<div style="display:flex; justify-content:space-between; '
        'align-items:flex-start; gap:14px; margin-bottom:12px;">'
        # min-width:0 so a long customer name wraps in this column instead of
        # squeezing the stat cards.
        '<div style="min-width:0;">'
        f'<div style="font-size:20px; font-weight:700; color:{_INK};">'
        f'{_esc(data.get("title"))}</div>'
        f'<div style="font-size:11px; color:#64748B; margin-top:3px; '
        f'overflow-wrap:anywhere;">'
        f'{_esc(data.get("customer_label") or "الكل")} · من '
        f'{_esc(data.get("date_from_label"))} إلى {_esc(data.get("date_to_label"))}</div>'
        "</div>"
        f'<div style="display:flex; gap:8px; flex:none;">{cards}</div>'
        "</div>"
        f"{table}{_empty_note(data)}",
        landscape=True,
    )


build_statement_html_v2.landscape = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# النموذج الثالث — formal letterhead, النموذج الثامن's identity
# ---------------------------------------------------------------------------
def build_statement_html_v3(data: dict[str, Any]) -> str:
    """A formal sheet sharing النموذج الثامن's navy identity. Portrait."""
    summary = data.get("summary") or {}
    head = "".join(
        f'<th style="background:{_NAVY}; color:#fff; font-weight:700; padding:7px 4px; '
        f'width:{width}; text-align:{align};">{label}</th>'
        for label, width, align in (
            ("التاريخ", "16%", "center"), ("البيان", "auto", "right"),
            ("المدين", "20%", "center"), ("الدائن", "20%", "center"),
        )
    )
    body = []
    for index, row in enumerate(data.get("rows") or []):
        zebra = f" background:#F7F9FD;" if index % 2 else ""
        cell = (f'<td style="border:1px solid #DBE1F2; padding:5px 4px; '
                f'text-align:center;{zebra}">')
        body.append(
            "<tr>"
            f'{cell}{_esc(row.get("transaction_date"))}</td>'
            f'<td style="border:1px solid #DBE1F2; padding:5px 6px; '
            f'text-align:right;{zebra}">{_esc(row.get("description"))}</td>'
            f'{cell}<span class="num">{_money(row.get("debit"))}</span></td>'
            f'{cell}<span class="num">{_money(row.get("credit"))}</span></td>'
            "</tr>"
        )
    table = (
        f'<table style="font-size:10.5px;"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
        if body
        else ""
    )
    totals = (
        f'<div style="display:flex; justify-content:space-between; font-size:11px; '
        f'padding:5px 9px; border-bottom:1px solid #E4E8F3;">'
        f'<span style="color:#4A5170;">إجمالي المدين</span>'
        f'<span class="num" style="font-weight:700;">'
        f'{_esc(summary.get("total_debit_label"))}</span></div>'
        f'<div style="display:flex; justify-content:space-between; font-size:11px; '
        f'padding:5px 9px; border-bottom:1px solid #E4E8F3;">'
        f'<span style="color:#4A5170;">إجمالي الدائن</span>'
        f'<span class="num" style="font-weight:700;">'
        f'{_esc(summary.get("total_credit_label"))}</span></div>'
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f'background:{_NAVY}; color:#fff; padding:9px; margin-top:2px;">'
        '<span style="font-size:12px;">الرصيد المستحق</span>'
        f'<span class="num" style="font-size:15px; font-weight:700;">'
        f'{_esc(summary.get("difference_label"))} SAR</span></div>'
    )
    return _document(
        f'{_letterhead(data, accent=_NAVY, rule=_NAVY)}'
        # The logo used to sit here, opposite the title. It is the letterhead's
        # centre column now, so this is the title alone.
        '<div style="padding-bottom:10px;">'
        f'<div style="font-size:19px; font-weight:700; color:{_NAVY};">'
        f'{_esc(data.get("title"))}</div>'
        '<div dir="ltr" style="font-size:11px; color:#5A6180; text-align:right;">'
        "Customer Statement</div>"
        "</div>"
        # The navy bar is a GRID, not flex. As flex with `gap:24px` a long customer
        # name starved the period field: «الفترة» broke mid-value onto four lines
        # («01-01-2026 / -2026 — / 17-07»). A fixed track + `nowrap` on the period
        # means a long name can only ever wrap in its own column.
        f'<div style="background:{_NAVY}; color:#fff; display:grid; '
        f'grid-template-columns:minmax(0,1fr) auto; gap:8px 24px; '
        f'padding:8px 12px; font-size:11px; align-items:baseline;">'
        f'<span style="min-width:0; overflow-wrap:anywhere;">'
        f'<span style="color:{_NAVY_ON};">العميل</span> '
        f'{_esc(data.get("customer_label") or "الكل")}</span>'
        f'<span style="white-space:nowrap;"><span style="color:{_NAVY_ON};">الفترة</span> '
        f'{_esc(data.get("date_from_label"))} — {_esc(data.get("date_to_label"))}</span>'
        "</div>"
        f'<div style="margin-bottom:12px;"></div>'
        f"{table}{_empty_note(data)}"
        # The «توقيع المحاسب» rule that sat opposite the totals was dropped at the
        # user's request (2026-07-17) for the shared bottom-left signature line —
        # the same one every receipt voucher now prints.
        '<div style="display:flex; gap:12px; margin-top:14px; align-items:flex-start;">'
        f'<div style="flex:1;">{totals}</div>'
        '<div style="flex:1; display:flex; flex-direction:column; '
        'justify-content:flex-end; padding-top:34px;">'
        f'{signature_line_html(font_size="10.5px", color="#6B7290")}'
        "</div></div>"
        '<div style="margin-top:16px; padding-top:8px; border-top:1px solid #E4E8F3; '
        'display:flex; justify-content:space-between; font-size:9.5px; color:#8189A6;">'
        "<span>هذا الكشف صادر آلياً من النظام ولا يحتاج إلى ختم</span></div>",
        landscape=False,
    )


build_statement_html_v3.landscape = False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Dispatch + page layout
# ---------------------------------------------------------------------------
_BUILDERS: dict[int, Callable[[dict[str, Any]], str]] = {
    1: build_statement_html_v1,
    2: build_statement_html_v2,
    3: build_statement_html_v3,
}


def choose_statement_builder(
    parent: QWidget | None, action_label: str
) -> "Callable[[dict[str, Any]], str] | None":
    """Ask which statement layout to use; return its builder or ``None``.

    Reuses the sales-invoice picker dialog rather than growing a second one — it
    already takes its options as an argument.
    """
    choice = choose_invoice_template(parent, action_label, STATEMENT_TEMPLATE_OPTIONS)
    if choice is None:
        return None
    return _BUILDERS.get(choice, build_statement_html_v1)


def statement_page_layout(html_builder: Callable[[dict[str, Any]], str]) -> QPageLayout:
    """A4 in the orientation the chosen template declares.

    The orientation lives on the builder because it is a property of the design,
    not of the caller: النموذج الأول/الثاني are wide (seven and six columns), while
    النموذج الثالث is a portrait letterhead. Chromium's ``printToPdf`` takes the
    layout as an argument and ignores any CSS ``@page``, so this is the only place
    it can be decided.
    """
    landscape = bool(getattr(html_builder, "landscape", True))
    return QPageLayout(
        QPageSize(QPageSize.A4),
        QPageLayout.Landscape if landscape else QPageLayout.Portrait,
        QMarginsF(_MARGIN_MM, _MARGIN_MM, _MARGIN_MM, _MARGIN_MM),
        # Millimetre, explicitly: QPageLayout's default unit is **Point**, so the
        # same numbers silently meant ~4mm and printed the table hard against the
        # paper edge — inside the unprintable margin of most office printers.
        QPageLayout.Millimeter,
    )


def _default_pdf_name(data: dict[str, Any]) -> str:
    label = str(data.get("customer_label") or "").strip()
    safe = "".join(ch for ch in label if ch.isalnum() or ch in (" ", "-", "_")).strip()
    return f"كشف حساب - {safe}.pdf" if safe and safe != "الكل" else "كشف حساب العميل.pdf"


# ---------------------------------------------------------------------------
# Preview dialog
# ---------------------------------------------------------------------------
class CustomerStatementPreviewDialog(QDialog):
    """On-screen preview of the printable statement, with export/print/close."""

    def __init__(
        self,
        data: dict[str, Any],
        parent: QWidget | None = None,
        html_builder: Callable[[dict[str, Any]], str] = build_statement_html_v1,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._html_builder = html_builder
        self.setWindowTitle("معاينة كشف الحساب")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1040, 780)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.export_button = QPushButton("تصدير PDF")
        self.export_button.setFixedHeight(36)
        self.export_button.setMinimumWidth(130)
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.setIcon(si_icon("fa5s.file-pdf", color="#FFFFFF"))
        style_button(self.export_button, "red")
        self.export_button.clicked.connect(self._on_export)

        self.print_button = QPushButton("طباعة")
        self.print_button.setFixedHeight(36)
        self.print_button.setMinimumWidth(110)
        self.print_button.setCursor(Qt.PointingHandCursor)
        self.print_button.setIcon(si_icon("fa5s.print", color="#374151"))
        style_button(self.print_button, "white")
        self.print_button.clicked.connect(self._on_print)

        close_button = QPushButton("إغلاق")
        close_button.setFixedHeight(36)
        close_button.setMinimumWidth(100)
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setIcon(si_icon("fa5s.times", color="#374151"))
        style_button(close_button, "white")
        close_button.clicked.connect(self.reject)

        bar.addWidget(self.export_button)
        bar.addWidget(self.print_button)
        bar.addStretch(1)
        bar.addWidget(close_button)
        root.addLayout(bar)

        self.view = QWebEngineView(self)
        self.view.setStyleSheet(
            "background:#e9e9ea; border:1px solid #E5E7EB; border-radius:8px;"
        )
        self.view.setHtml(html_builder(data))
        root.addWidget(self.view, 1)

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "حفظ كشف الحساب PDF", _default_pdf_name(self._data), "PDF (*.pdf)"
        )
        if not path:
            return
        self.export_button.setEnabled(False)

        def _finished(file_path: str, success: bool) -> None:
            self.export_button.setEnabled(True)
            try:
                self.view.page().pdfPrintingFinished.disconnect(_finished)
            except (RuntimeError, TypeError):
                pass
            if success:
                QMessageBox.information(self, "تم", f"تم تصدير كشف الحساب إلى:\n{file_path}")
            else:
                QMessageBox.warning(self, "تعذّر التصدير", "حدث خطأ أثناء إنشاء ملف PDF.")

        self.view.page().pdfPrintingFinished.connect(_finished)
        self.view.page().printToPdf(path, statement_page_layout(self._html_builder))

    def _on_print(self) -> None:
        try:
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except Exception:  # noqa: BLE001
            QMessageBox.warning(self, "الطباعة", "خدمة الطباعة غير متاحة.")
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageLayout(statement_page_layout(self._html_builder))
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QPrintDialog.Accepted:
            return
        self._printer = printer  # keep a reference during the async print

        def _done(success: bool) -> None:  # noqa: ARG001
            try:
                self.view.printFinished.disconnect(_done)
            except (RuntimeError, TypeError):
                pass

        self.view.printFinished.connect(_done)
        self.view.print(printer)


# ---------------------------------------------------------------------------
# Direct PDF export (toolbar button, no preview window)
# ---------------------------------------------------------------------------
def export_statement_to_pdf(
    parent: QWidget,
    data: dict[str, Any],
    html_builder: Callable[[dict[str, Any]], str] = build_statement_html_v1,
) -> None:
    """Ask for a path and render the statement straight to a PDF file."""
    path, _ = QFileDialog.getSaveFileName(
        parent, "حفظ كشف الحساب PDF", _default_pdf_name(data), "PDF (*.pdf)"
    )
    if not path:
        return

    from PySide6.QtWebEngineCore import QWebEnginePage

    page = QWebEnginePage(parent)
    # Hold a reference on the parent so the page survives the async PDF write.
    holder = getattr(parent, "_statement_pdf_pages", None)
    if holder is None:
        holder = []
        parent._statement_pdf_pages = holder  # type: ignore[attr-defined]
    holder.append(page)

    def _cleanup() -> None:
        try:
            holder.remove(page)
        except ValueError:
            pass
        page.deleteLater()

    def _on_pdf(file_path: str, success: bool) -> None:
        if success:
            QMessageBox.information(parent, "تم", f"تم تصدير كشف الحساب إلى:\n{file_path}")
        else:
            QMessageBox.warning(parent, "تعذّر التصدير", "حدث خطأ أثناء إنشاء ملف PDF.")
        _cleanup()

    def _on_load(ok: bool) -> None:
        if not ok:
            QMessageBox.warning(parent, "تعذّر التصدير", "تعذّر تجهيز مستند كشف الحساب.")
            _cleanup()
            return
        QTimer.singleShot(150, lambda: page.printToPdf(path, statement_page_layout(html_builder)))

    page.pdfPrintingFinished.connect(_on_pdf)
    page.loadFinished.connect(_on_load)
    page.setHtml(html_builder(data))


def print_statement(
    parent: QWidget,
    data: dict[str, Any],
    html_builder: Callable[[dict[str, Any]], str] = build_statement_html_v1,
) -> None:
    """Send the statement straight to a printer, without the preview window.

    Chromium can only print a *loaded* page, and both the load and the print are
    asynchronous, so the view is parented and held on ``parent`` until printing
    reports back — dropping it early cancels the job silently. It is deliberately
    never shown: it exists only to render.
    """
    from PySide6.QtPrintSupport import QPrintDialog, QPrinter

    view = QWebEngineView(parent)
    view.setVisible(False)
    holder = getattr(parent, "_statement_print_views", None)
    if holder is None:
        holder = []
        parent._statement_print_views = holder  # type: ignore[attr-defined]
    holder.append(view)

    def _cleanup() -> None:
        try:
            holder.remove(view)
        except ValueError:
            pass
        view.deleteLater()

    def _on_load(ok: bool) -> None:
        if not ok:
            QMessageBox.warning(parent, "الطباعة", "تعذّر تجهيز مستند كشف الحساب.")
            _cleanup()
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageLayout(statement_page_layout(html_builder))
        dialog = QPrintDialog(printer, parent)
        if dialog.exec() != QPrintDialog.Accepted:
            _cleanup()
            return

        def _done(success: bool) -> None:
            if not success:
                QMessageBox.warning(parent, "الطباعة", "تعذّرت طباعة كشف الحساب.")
            _cleanup()

        view.printFinished.connect(_done)
        view.print(printer)

    view.loadFinished.connect(_on_load)
    view.setHtml(html_builder(data))


__all__ = [
    "STATEMENT_TEMPLATE_OPTIONS",
    "build_statement_html_v1",
    "build_statement_html_v2",
    "build_statement_html_v3",
    "choose_statement_builder",
    "statement_page_layout",
    "rows_with_balance",
    "CustomerStatementPreviewDialog",
    "export_statement_to_pdf",
    "print_statement",
]
