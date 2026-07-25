"""Printable Saudi tax-invoice document — **eighth template** (النموذج الثامن).

An *additional* invoice layout offered alongside النماذج الأول → السابع. Unlike
every template before it, this one has no reference PDF and no design bundle: it
was proposed in-conversation and approved by the user (2026-07-16), so the
geometry here is chosen rather than recovered, and nothing needs to match an
external document to the pixel.

The design deliberately breaks with the older layouts in one respect. النماذج
الأول → السابع render each party's details as a bordered grid — an English label
column, a value column and an Arabic label column — which is what made the
customer table's columns drift out of step with the seller's (fixed in النموذج
السادس on the same day). Here the two parties are **borderless cards**: a muted
label beside its value, stacked. There are no shared column widths left to drift,
so the fields cannot fall out of alignment no matter how long a company name or
address runs.

Other choices, all approved with the mock-up:

* **RTL-structured** (like النماذج الثالث/الرابع), not LTR like الأول/الخامس/السادس.
  Numbers, money and the VAT / C.R. registrations carry ``dir="ltr"`` so they
  never reverse.
* A solid accent **info bar** carries the invoice number and date instead of a
  three-row table.
* The items table gets a **serial column** and zebra striping.
* «إجمالي المبلغ المستحق» sits in a **filled accent bar**, not as the last row of
  a plain grid.
* The discount line prints 0.00: ``_collect_preview_data`` produces no «discount»
  key, the same open point النماذج الثاني/الثالث/الخامس/السادس carry (see the
  handoff, section 8.5). It is kept visible for parity with them.

The QR is :data:`QR_PRINT_PX` — النموذج الأول's size, at the user's explicit
request. Every field printed below exists in ``data``; nothing is invented.

⚠️ Fonts are set through the ``<style>`` block, never through an inline
``style="… font-family:…"``. The font stacks contain double quotes, which close
an HTML attribute early and silently drop every declaration that follows —
النموذج الرابع still does this and loses its own ``font-size`` as a result.

Long invoices paginate (user, 2026-07-23): twelve item rows share the page with
the QR/totals band, nineteen fit a page without it, and the rest run onto further
sheets — each a complete invoice page — with the band whole on the last. Rows
used to be compressed and their surplus discarded instead; the footer's page
counter, which always read «1 / 1», now says what it means. See
:mod:`app.ui.screens.invoice_pagination`.

Only :func:`build_invoice_html_v8` lives here — it consumes the very same
``data`` dict produced by
:meth:`SaudiSalesInvoicePage._collect_preview_data`.

Presentation only: no database access, no ZATCA cryptography.
"""

from __future__ import annotations

import datetime
import html
from typing import Any

from app.ui.screens.saudi_invoice_print import (
    _asset_data_uri,
    _money,
    _qty,
    company_logo_data_uri,
    QR_PRINT_PX,
    qr_vector_data_uri,
)
from app.ui.screens.invoice_pagination import paginate, row_height, sheet_style
from app.ui.screens.saudi_invoice_print_v3 import _dec, _rate

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
_ACCENT = "#1A2A6C"        # the info bar, the table head, the amount-due bar
_ACCENT_ON = "#B9C4E8"     # secondary text *on* the accent fill
_TINT = "#F1F4FB"          # the party cards' fill
_ZEBRA = "#F7F9FD"         # every second item row
_RULE = "#E4E8F3"          # hairlines between item rows and above the footer
_RULE_STRONG = "#DBE1F2"   # the party cards' inner rule, the QR panel's border
_LABEL = "#6B7290"         # muted field labels
_MUTED = "#A6ABBF"         # the «—» standing in for an empty field
_FONTS = 'Tahoma, Arial, "Helvetica Neue", Helvetica, sans-serif'

_COUNTRY = "المملكة العربية السعودية"

# Sheet geometry — the same 816 × 1056 px page every other template uses, so the
# shared preview dialog and PDF exporter render this one unchanged.
_SHEET_W = 816
_SHEET_H = 1056
_PAD_X = 30

# Item-table column widths (px), right-to-left as RTL lays them out. They sum to
# the content width (816 - 2*30 = 756), so the table needs no width of its own.
_ITEM_COLS = (34, 251, 60, 104, 60, 114, 133)

# Fixed band heights (px). These are the *rendered* heights, read back off the
# page with the probe rather than assumed: a flex item never shrinks below its
# own content (``min-height:auto``), so declaring a basis smaller than the
# content silently yields the content's height instead and any budget derived
# from the declared number is wrong. Each band below is therefore declared
# `flex:0 0 auto` and its real height recorded here.
_HEADER_H = 100
_INFO_H = 40
_PARTIES_H = 160   # 16px top padding + the cards' own 144
_GAP = 14
_TABLE_HEAD_H = 36
_QR_BAND_H = 221   # 14px top padding + the 168px QR in its 207px panel
_FOOTER_H = 36
_PAD_BOTTOM = 24

_ABOVE_ROWS = _HEADER_H + _INFO_H + _PARTIES_H + _GAP + _TABLE_HEAD_H
_BELOW_ROWS = _QR_BAND_H + _GAP + _FOOTER_H + _PAD_BOTTOM
_ITEM_ROW_BUDGET = _SHEET_H - _ABOVE_ROWS - _BELOW_ROWS
_ITEM_ROW_H = 32        # the approved mock-up's row height, kept on every page

# --- pagination (user, 2026-07-23) -----------------------------------------
# The sheet is a fixed 1056px with `overflow:hidden` and the items band is the
# only one allowed to shrink, so a long invoice used to keep its QR and totals
# and lose its *rows* — silently, after they had been compressed towards 18px
# and their text shrunk to 10px to postpone it. Neither happens now: rows keep
# the mock-up's 32px and 12.5px text on every page, twelve of them share the
# page with the QR/totals band, and the rest run onto further sheets.
_ROW_BUDGET_LAST = _ITEM_ROW_BUDGET                                   # 12 rows
_ROW_BUDGET = _SHEET_H - _ABOVE_ROWS - (_GAP + _FOOTER_H + _PAD_BOTTOM)  # 19 rows

_LINE_H = "1.25"

_ITEM_FONT = 12.5       # the mock-up's own size, no longer scaled by item count

# Name-column metrics for the wrapped-row estimate: the cell's 251px less its 9px
# padding each side, and the row's 1px bottom rule.
_NAME_W = _ITEM_COLS[1] - 18
_NAME_LINE_H = 1.25
_ROW_CHROME = 1.0


def _row_height_v8(line: dict[str, Any]) -> float:
    """How tall this line's row will really be — see :mod:`invoice_pagination`.

    ``min-height`` is a floor, not a ceiling: a product name long enough to wrap
    grows its row past the mock-up's 32px, and a budget counted in 32px rows
    would then overflow. That is what used to make the rows disappear at twelve
    items with wrapping names while a one-line invoice managed twenty.
    """
    return row_height(
        str(line.get("name") or ""),
        base=_ITEM_ROW_H,
        width_px=_NAME_W,
        font_px=_ITEM_FONT,
        line_height=_NAME_LINE_H,
        chrome=_ROW_CHROME,
    )


def _field(label: str, value: str, *, ltr: bool = False, strong: bool = False) -> str:
    """One label/value pair inside a party card.

    An empty value prints «—» rather than a blank gap, so a missing customer
    address reads as *absent* instead of as a rendering fault.
    """
    if not value:
        inner = f'<span style="color:{_MUTED};">—</span>'
    else:
        weight = "500" if strong else "400"
        direction = ' dir="ltr" style="text-align:right;"' if ltr else ""
        inner = (
            f'<span{direction}><span style="font-weight:{weight};">{value}</span></span>'
            if ltr
            else f'<span style="font-weight:{weight};">{value}</span>'
        )
    return (
        f'<span style="color:{_LABEL};">{label}</span>{inner}'
    )


def _party_card(title_ar: str, title_en: str, party: dict[str, Any]) -> str:
    """A borderless party card — the whole point of this template (see module doc)."""
    name = html.escape(str(party.get("name") or ""))
    address = html.escape(str(party.get("address") or ""))
    full_address = f"{address}، {_COUNTRY}" if address else _COUNTRY
    fields = (
        _field("العنوان", full_address)
        + _field("سجل تجاري", html.escape(str(party.get("cr") or "")), ltr=True)
        + _field("الرقم الضريبي", html.escape(str(party.get("vat") or "")),
                 ltr=True, strong=True)
    )
    return (
        f'<div style="flex:1 1 0; min-width:0; background:{_TINT}; border-radius:6px; '
        f'padding:13px 15px;">'
        f'<div style="font-size:12px; color:{_ACCENT}; font-weight:700; '
        f'padding-bottom:8px; border-bottom:1px solid {_RULE_STRONG}; margin-bottom:9px;">'
        f'{title_ar} <span style="color:{_LABEL}; font-weight:400;">/ {title_en}</span></div>'
        f'<div style="font-size:15px; font-weight:700; margin-bottom:8px; '
        f'overflow-wrap:anywhere;">{name}</div>'
        f'<div style="display:grid; grid-template-columns:78px minmax(0,1fr); '
        f'row-gap:6px; column-gap:8px; font-size:13px; overflow-wrap:anywhere;">'
        f'{fields}</div>'
        f"</div>"
    )


def _items_header() -> str:
    labels = (
        ("#", ""),
        ("اسم المنتج", "Product"),
        ("الكمية", ""),
        ("السعر", ""),
        ("النسبة", ""),
        ("الضريبة", ""),
        ("المبلغ", ""),
    )
    cells = []
    for index, (arabic, english) in enumerate(labels):
        align = "right" if index == 1 else "center"
        pad = "0 9px" if index == 1 else "0 4px"
        suffix = (
            f' <span style="color:{_ACCENT_ON}; font-weight:400;">{english}</span>'
            if english
            else ""
        )
        # gap, not a literal space: this cell is a flex container, so the Arabic
        # label and the English <span> are separate flex items and any whitespace
        # between them is discarded — «اسم المنتج» printed hard against «Product».
        cells.append(
            f'<div style="flex:0 0 {_ITEM_COLS[index]}px; display:flex; gap:5px; '
            f'align-items:center; justify-content:{"flex-start" if index == 1 else "center"}; '
            f'text-align:{align}; font-size:12.5px; font-weight:700; padding:{pad};">'
            f"{arabic}{suffix}</div>"
        )
    return (
        f'<div style="display:flex; min-height:36px; background:{_ACCENT}; '
        f'color:#ffffff; border-radius:5px 5px 0 0;">{"".join(cells)}</div>'
    )


def _item_rows_v8(lines: list[dict[str, Any]], start: int = 1) -> str:
    """This page's rows; «#» numbers items across the whole invoice."""
    height = _ITEM_ROW_H
    font = _ITEM_FONT
    rows = []
    for index, line in enumerate(lines, start=start):
        values = (
            str(index),
            html.escape(str(line.get("name") or "")),
            _qty(line.get("qty")),
            _money(line.get("price")),
            _rate(line.get("vat_rate")),
            _money(line.get("vat_amount")),
            _money(line.get("total")),
        )
        cells = []
        for column, value in enumerate(values):
            is_name = column == 1
            # Money and quantities are Latin digits: they must not be reordered by
            # the RTL paragraph direction, so each numeric cell is its own LTR run.
            ltr = column >= 2
            attrs = ' dir="ltr"' if ltr else ""
            weight = "700" if column == 6 else "400"
            color = f" color:{_LABEL};" if column == 0 else ""
            cells.append(
                f'<div{attrs} style="flex:0 0 {_ITEM_COLS[column]}px; display:flex; '
                f'flex-direction:column; justify-content:center; '
                f'text-align:{"right" if is_name else "center"}; '
                f'font-size:{font}px; font-weight:{weight}; line-height:{_LINE_H}; '
                f'padding:{"0 9px" if is_name else "0 4px"};{color}'
                f'{" overflow-wrap:anywhere;" if is_name else " white-space:nowrap;"}">'
                f"{value}</div>"
            )
        background = f" background:{_ZEBRA};" if index % 2 == 0 else ""
        rows.append(
            f'<div style="display:flex; min-height:{height}px; '
            f'border-bottom:1px solid {_RULE};{background}">{"".join(cells)}</div>'
        )
    return "".join(rows)


def _totals_row(label_ar: str, value: str) -> str:
    return (
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f'font-size:13px; padding:7px 11px; border-bottom:1px solid {_RULE};">'
        f'<span style="color:#4A5170;">{label_ar}</span>'
        f'<span dir="ltr" style="font-weight:700;">{value}</span></div>'
    )


def build_invoice_html_v8(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML for النموذج الثامن from live data."""
    logo = company_logo_data_uri(data.get("seller")) or _asset_data_uri("logo.png")
    qr = qr_vector_data_uri(data.get("qr_payload"))

    seller = data.get("seller") or {}
    customer = data.get("customer") or {}
    totals = data.get("totals") or {}
    lines = data.get("lines") or []

    invoice_number = html.escape(str(data.get("invoice_number") or ""))
    issued = data.get("issue_datetime")
    if isinstance(issued, datetime.datetime):
        issue_date = issued.strftime("%d-%m-%Y")
    else:
        issue_date = html.escape(str(issued or ""))

    subtotal = _dec(totals.get("subtotal"))
    discount = _dec(totals.get("discount"))
    vat_total = _dec(totals.get("vat"))
    grand_total = _dec(totals.get("total"))
    taxable = subtotal - discount

    logo_html = (
        f'<img src="{logo}" alt="logo" style="width:150px; height:66px; '
        f'object-fit:contain; object-position:left center; display:block;" />'
        if logo
        else ""
    )
    qr_html = (
        f'<img src="{qr}" alt="QR" style="width:{QR_PRINT_PX}px; '
        f'height:{QR_PRINT_PX}px; display:block; margin:0 auto; background:#ffffff;" />'
        if qr
        else ""
    )

    # ---- the page furniture, identical on every sheet ----------------------
    # A continuation page is a full invoice page — header, info bar, both party
    # cards — so every sheet of a multi-page invoice stands on its own.
    page_top = f"""
  <!-- ============ HEADER ============ -->
  <div style="flex:0 0 {_HEADER_H}px; display:flex; align-items:center; justify-content:space-between; padding:0 {_PAD_X}px;">
    <div>
      <div style="font-size:27px; font-weight:700; color:{_ACCENT}; line-height:1.2;">فاتورة ضريبية</div>
      <div dir="ltr" style="font-size:15px; color:#5A6180; letter-spacing:0.4px; text-align:right;">Tax Invoice</div>
    </div>
    {logo_html}
  </div>

  <!-- ============ INFO BAR ============ -->
  <div style="flex:0 0 {_INFO_H}px; background:{_ACCENT}; color:#ffffff; display:flex; align-items:center; gap:32px; padding:0 {_PAD_X}px;">
    <div style="display:flex; gap:8px; align-items:baseline;">
      <span style="font-size:12px; color:{_ACCENT_ON};">رقم الفاتورة</span>
      <span dir="ltr" style="font-size:15px; font-weight:700;">{invoice_number}</span>
    </div>
    <div style="display:flex; gap:8px; align-items:baseline;">
      <span style="font-size:12px; color:{_ACCENT_ON};">تاريخ الفاتورة</span>
      <span dir="ltr" style="font-size:15px; font-weight:700;">{issue_date}</span>
    </div>
    <div dir="ltr" style="margin-inline-start:auto; font-size:12px; color:{_ACCENT_ON};">Invoice no. / Date</div>
  </div>

  <!-- ============ PARTIES ============ -->
  <div style="flex:0 0 auto; display:flex; gap:14px; padding:16px {_PAD_X}px 0; align-items:stretch;">
    {_party_card("بيانات المورد", "Seller", seller)}
    {_party_card("بيانات العميل", "Customer", customer)}
  </div>
"""

    # The QR/totals band closes the invoice, so it prints on the LAST sheet only,
    # whole. _ROW_BUDGET_LAST is what reserves its room there.
    band_html = f"""
  <!-- ============ QR + TOTALS ============ -->
  <div style="flex:0 0 auto; display:flex; gap:14px; padding:{_GAP}px {_PAD_X}px 0; align-items:flex-start;">
    <div style="flex:0 0 196px; border:1px solid {_RULE_STRONG}; border-radius:6px; padding:9px;">
      <div style="font-size:11px; color:{_LABEL}; text-align:center; margin-bottom:6px;">رمز الاستجابة السريعة</div>
      {qr_html}
    </div>
    <div style="flex:1 1 0; min-width:0;">
      {_totals_row("الإجمالي غير شامل الضريبة", _money(subtotal))}
      {_totals_row("مجموع الخصومات", _money(discount))}
      {_totals_row("الإجمالي الخاضع للضريبة", _money(taxable))}
      {_totals_row("مجموع ضريبة القيمة المضافة", _money(vat_total))}
      <div style="display:flex; justify-content:space-between; align-items:center; background:{_ACCENT}; color:#ffffff; padding:11px; border-radius:0 0 5px 5px; margin-top:2px;">
        <span style="font-size:13.5px;">إجمالي المبلغ المستحق <span style="color:{_ACCENT_ON}; font-size:12px;">Total due</span></span>
        <span dir="ltr" style="font-size:17px; font-weight:700;">{_money(grand_total)} SAR</span>
      </div>
    </div>
  </div>
"""

    pages = paginate(
        lines, _row_height_v8, budget=_ROW_BUDGET, budget_last=_ROW_BUDGET_LAST,
    )
    sheets = []
    first_index = 1
    for index, page_lines in enumerate(pages, start=1):
        last = index == len(pages)
        sheets.append(f"""
<div data-sheet dir="rtl" lang="ar" style="position:relative; width:{_SHEET_W}px; height:{_SHEET_H}px; margin:24px auto; padding-bottom:{_PAD_BOTTOM}px; background:#ffffff; box-shadow:0 2px 14px rgba(0,0,0,0.28); overflow:hidden; color:#111; display:flex; flex-direction:column; {sheet_style(last=last)}">
{page_top}
  <!-- ============ ITEMS ============ -->
  <!-- `flex:0 1 auto` + `min-height:0`: the band takes its natural height, so the
       totals sit directly under the last item as the approved mock-up has them,
       and it is the *only* band that may shrink. It no longer has to: the page's
       rows are chosen to fit it (see _ROW_BUDGET), and `overflow:hidden` stays
       only as a backstop against an estimate that is off by a pixel. The spacer
       below, not this band, absorbs the slack on a short invoice — that is what
       keeps a three-item invoice from printing a hole in its middle. -->
  <div style="flex:0 1 auto; min-height:0; padding:{_GAP}px {_PAD_X}px 0; overflow:hidden;">
    <div style="display:flex; flex-direction:column;">
      {_items_header()}
      {_item_rows_v8(page_lines, first_index)}
    </div>
  </div>
{band_html if last else ""}
  <!-- ============ SPACER ============ -->
  <!-- Absorbs the leftover height of a short invoice so the slack lands *here*,
       between the totals and the footer, instead of as a hole in the middle of
       the page. -->
  <div style="flex:1 1 auto; min-height:0;"></div>

  <!-- ============ FOOTER ============ -->
  <div style="flex:0 0 {_FOOTER_H}px; margin:0 {_PAD_X}px; margin-top:{_GAP}px; padding-top:10px; border-top:1px solid {_RULE}; display:flex; justify-content:space-between; align-items:flex-start; font-size:11px; color:{_LABEL};">
    <span>فاتورة ضريبية صادرة وفق أحكام ضريبة القيمة المضافة في المملكة العربية السعودية</span>
    <span dir="ltr">{index} / {len(pages)}</span>
  </div>

</div>""")
        first_index += len(page_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    background: #c9ccd2;
    font-family: {_FONTS};
    color: #111;
    -webkit-font-smoothing: antialiased;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  @media print {{
    body {{ background: #fff; }}
    [data-sheet] {{ box-shadow: none !important; margin: 0 !important; }}
  }}
</style>
</head>
<body>
{"".join(sheets)}
</body>
</html>"""


__all__ = ["build_invoice_html_v8"]
