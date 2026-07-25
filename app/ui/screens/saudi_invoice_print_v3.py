"""Printable Saudi tax-invoice document — **third template** (النموذج الثالث).

An *additional* invoice layout offered alongside النموذج الأول
(:mod:`app.ui.screens.saudi_invoice_print`) and النموذج الثاني
(:mod:`app.ui.screens.saudi_invoice_print_v2`). It reproduces the approved
reference print supplied as ``ركن لوجو.pdf``: a dashed-frame bilingual
letterhead with a centred logo, an underlined «فاتورة ضريبية» title, four
dotted-underline meta rows in two columns, a ten-column items table with a
shaded (``#deebf7``) bilingual double header row and a shaded totals row, then a
bottom band carrying the QR, a five-row totals grid and the print footer.

Every coordinate below was measured off the reference PDF (Letter, 612.16 ×
792.06 pt) and converted to CSS pixels at 96 dpi (``pt × 4/3``), which lands on
the 816 × 1056 px design sheet the other two templates already use — so the
existing preview dialog and PDF exporter render it pixel-for-pixel.

Long invoices paginate (user, 2026-07-23): the sheet holds five item rows above
the closing row/totals/QR band, and beyond that the rows run onto further sheets
— each a complete invoice page — with the band printed whole on the last one.
The QR used to be cut off by the sheet's ``overflow:hidden`` instead. See
:mod:`app.ui.screens.invoice_pagination`.

Only :func:`build_invoice_html_v3` lives here — it consumes the very same
``data`` dict produced by
:meth:`SaudiSalesInvoicePage._collect_preview_data`. The preview dialog and the
"save as PDF" helper in :mod:`saudi_invoice_print` are template-agnostic (they
take an ``html_builder``), so they are reused as-is for this layout.

Presentation only: no database access, no ZATCA cryptography — it merely
displays the data the screen already holds (including the QR, rendered as a real
image).
"""

from __future__ import annotations

import datetime
import html
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ui.screens.invoice_pagination import (
    FOOT_H,
    page_foot,
    paginate,
    row_height,
    sheet_style,
)

# Reuse the shared asset/number/QR helpers from النموذج الأول so all three
# templates stay perfectly consistent and there is a single source of truth.
from app.ui.screens.saudi_invoice_print import (
    _asset_data_uri,
    _money,
    _qty,
    company_logo_data_uri,
    QR_PRINT_PX,
    qr_vector_data_uri,
)

# Design tokens measured off the reference PDF.
_GRID = "#242732"  # every table border line
_SHADE = "#deebf7"  # header / totals cell fill

# Items-table column widths in CSS px, summing to exactly 730 (the measured
# table width). Whole numbers on purpose: fractional track widths accumulate
# rounding error across the columns, which drifts the grid lines and lets
# Chromium anti-alias them into a blur.
#
# The reference's الوحدة column is dropped, and the 48px it held went to the
# money columns (now 95px). That is what lets every cell share ONE font size:
# at 13.33px a 95px column holds up to 99,999,999.99 without shrinking.
_COL_WIDTHS = (38, 62, 139, 56, 95, 95, 55, 95, 95)
# The items table's own width — the totals table is pinned to the same number so
# the two blocks are exactly as wide as each other (user request 2026-07-16).
_TABLE_W = sum(_COL_WIDTHS)          # 730

# Totals columns: English label | value | Arabic label. The label columns keep
# their measured widths and the value column absorbs the difference, so widening
# the block to _TABLE_W stretches the shaded value cell, not the labels.
_TOTALS_COLS = (184, _TABLE_W - 184 - 126, 126)

_BLOCK_GAP = 14      # items table -> totals -> QR

# اسم الصنف. Long names wrap onto extra lines and grow the row instead of
# shrinking their own text, so every cell keeps the one uniform font size.
_NAME_COLUMN = 2

# Body font size, shared by every cell in the items table. Matches the
# reference's 10pt.
_BODY_PX = 13.33

# Every cell carries a full border and the table collapses them, so each border
# is drawn exactly once, on a whole pixel, on all four sides of every cell.
_CELL = f"border:1px solid {_GRID}; padding:2px 3px; text-align:center;"

# Horizontal space a cell loses to its own padding + collapsed borders.
_CELL_PAD_PX = 7.0

# Arial Bold advance widths in em. The digit/punctuation values are exact (they
# match the real font metrics), which is what makes _fit_px precise for the
# money columns rather than a guess; _EM_OTHER is a deliberately generous
# fallback for Arabic/Latin words, where over-estimating only costs a slightly
# smaller font and never a clipped glyph.
_EM_DIGIT = 0.556
_EM_PUNCT = 0.278
_EM_PERCENT = 0.889
_EM_OTHER = 0.60

# Absolute floor. Only an absurd figure (≥ 10¹² in the narrowest column) can
# reach it, and even there this keeps every digit on the page: a tiny number is
# recoverable, a clipped one is wrong. Real invoice figures never get near it —
# a 12-million line total still renders at ~9.4px.
_MIN_FONT_PX = 6.0

# --- pagination (user, 2026-07-23) -----------------------------------------
# The sheet is a fixed 1056px with `overflow:hidden` and this template puts the
# QR last, under the totals — so a sixth item used to push the QR off the bottom
# edge and Chromium silently cut it. Rows keep their 27px on every page now, and
# what does not fit runs onto another sheet; the closing (shaded) row, the totals
# grid and the QR travel together and print whole on the last one.
_SHEET_H = 1056
_BLOCK_TOP = 447     # the items block's own `top`
_HEAD_H = 76         # the double header row: 2 × 38
_ROW_H = 27          # one data row, and the shaded closing row
_FOOT_H = FOOT_H     # the «صفحة x من y» strip, charged only when x/y > 1
_TOTALS_H = 5 * 22   # the five-row totals grid

# What the closing row + totals + QR need under the last row of the last page.
_BAND_H = _ROW_H + _BLOCK_GAP + _TOTALS_H + _BLOCK_GAP + QR_PRINT_PX   # 381
_ROW_BUDGET = _SHEET_H - _BLOCK_TOP - _HEAD_H                          # 533 -> 19 rows
_ROW_BUDGET_LAST = _ROW_BUDGET - _BAND_H                               # 152 ->  5 rows

# Name-column metrics for the wrapped-row estimate: the column's width less its
# own cell padding, and the row's padding (2px each) plus its collapsed borders.
_NAME_W = _COL_WIDTHS[_NAME_COLUMN] - _CELL_PAD_PX
_NAME_LINE_H = 1.25
_ROW_CHROME = 6.0


def _row_height_v3(line: dict[str, Any]) -> float:
    """How tall this line's row will really be — see :mod:`invoice_pagination`."""
    return row_height(
        str(line.get("name") or ""),
        base=_ROW_H,
        width_px=_NAME_W,
        font_px=_BODY_PX,
        line_height=_NAME_LINE_H,
        chrome=_ROW_CHROME,
    )


def _text_em(text: str) -> float:
    total = 0.0
    for char in text:
        if char.isdigit():
            total += _EM_DIGIT
        elif char in ",. ":
            total += _EM_PUNCT
        elif char == "%":
            total += _EM_PERCENT
        else:
            total += _EM_OTHER
    return total


def _fit_px(text: str, column_px: int, base_px: float) -> float:
    """Largest font-size ≤ ``base_px`` at which ``text`` fits ``column_px``.

    A tax invoice must never clip a figure: ``2,875,000.00`` needs 77.8px but
    the "إجمالى شامل الضريبة" column is only 67px wide, so at the reference's
    own font size the trailing digits are silently lost. Shrinking that one cell
    keeps every digit on screen and leaves normal-sized numbers untouched.
    """
    em = _text_em(text)
    if em <= 0:
        return base_px
    available = column_px - _CELL_PAD_PX
    # Truncate rather than round: rounding the 2nd decimal *up* nudges the text
    # back over the edge it was just shrunk to clear.
    fitted = math.floor(min(base_px, available / em) * 100) / 100
    return max(_MIN_FONT_PX, fitted)


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _rate(value: Any) -> str:
    """Render a VAT rate the way the reference does, e.g. ``15.00%``."""
    return f"{_dec(value):.2f}%"


def _colgroup() -> str:
    return "".join(f'<col style="width:{w}px;">' for w in _COL_WIDTHS)


def _head_row(labels: tuple[str, ...], *, rtl: bool, height: int) -> str:
    """One band of the double header row (Arabic on top, English beneath)."""
    direction = ' dir="rtl"' if rtl else ""
    cells = "".join(
        f'<th{direction} style="{_CELL} height:{height}px; font-size:13.33px; '
        f'font-weight:700; line-height:1.15;">{label}</th>'
        for label in labels
    )
    return f"<tr>{cells}</tr>"


def _data_row(values: "tuple[str, ...]", *, background: str) -> str:
    """One items-table row. Every cell renders at the same ``_BODY_PX``.

    اسم الصنف wraps and lets the row grow taller. The remaining columns are
    single-line; they are wide enough for any realistic figure, so the
    :func:`_fit_px` guard below is dormant in practice and only ever engages to
    stop a freak value (over 99,999,999.99) from being silently clipped.
    """
    cells = []
    for column, value in enumerate(values):
        if column == _NAME_COLUMN:
            cells.append(
                f'<td dir="rtl" style="{_CELL} font-size:{_BODY_PX}px; '
                f'line-height:1.25; word-wrap:break-word;">{value}</td>'
            )
            continue
        size = _fit_px(value, _COL_WIDTHS[column], _BODY_PX)
        cells.append(
            f'<td style="{_CELL} font-size:{size}px; white-space:nowrap;">{value}</td>'
        )
    return f'<tr style="height:27px; background:{background};">{"".join(cells)}</tr>'


def _item_rows_v3(lines: list[dict[str, Any]], start: int = 1) -> str:
    """The «م.س» column numbers items across the whole invoice, not per page."""
    rows: list[str] = []
    for index, line in enumerate(lines, start=start):
        values = (
            str(index),
            html.escape(str(line.get("code") or "")),
            html.escape(str(line.get("name") or "")),
            _qty(line.get("qty")),
            _money(line.get("price")),
            _money(line.get("before")),
            _rate(line.get("vat_rate")),
            _money(line.get("vat_amount")),
            _money(line.get("total")),
        )
        rows.append(_data_row(values, background="#fff"))
    return "".join(rows)


def _summary_row(lines: list[dict[str, Any]], totals: dict[str, Any]) -> str:
    """The shaded closing row of the items table (column sub-totals)."""
    qty_total = sum((_dec(ln.get("qty")) for ln in lines), Decimal(0))
    values = (
        "",
        "",
        "",
        _qty(qty_total),
        "",
        _money(totals.get("subtotal", 0)),
        "",
        _money(totals.get("vat", 0)),
        _money(totals.get("total", 0)),
    )
    return _data_row(values, background=_SHADE)


def _meta_row(label_en: str, value: str, label_ar: str, *, rtl_value: bool = False) -> str:
    """One dotted-underline meta row: ``EN label … value … : AR label``."""
    direction = ' dir="rtl"' if rtl_value else ""
    return (
        '<div style="height:46.2px;">'
        '<div style="display:flex; align-items:center; height:15px; font-size:13.33px;">'
        f'<span style="white-space:nowrap;">{label_en}</span>'
        f'<span{direction} style="flex:1; text-align:center; padding:0 6px; '
        f'overflow:hidden; white-space:nowrap;">{value}</span>'
        f'<span dir="rtl" style="white-space:nowrap;">{label_ar} :</span>'
        "</div>"
        '<div style="margin-top:4px; border-bottom:1.5px dotted #000;"></div>'
        "</div>"
    )


def build_invoice_html_v3(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML for النموذج الثالث from live data."""
    # Prefer the printed company's own embedded logo; fall back to the bundled
    # design asset only when the company has none.
    logo = company_logo_data_uri(data.get("seller")) or _asset_data_uri("logo.png")
    qr = qr_vector_data_uri(data.get("qr_payload"))

    invoice_number = html.escape(str(data.get("invoice_number") or ""))
    issued = data.get("issue_datetime")
    if isinstance(issued, datetime.datetime):
        issue_date = issued.strftime("%d-%m-%Y")
    else:
        issue_date = html.escape(str(issued or ""))
    payment_label = html.escape(str(data.get("payment_label") or ""))

    seller = data.get("seller") or {}
    customer = data.get("customer") or {}
    totals = data.get("totals") or {}
    lines = data.get("lines") or []

    seller_name_ar = html.escape(str(seller.get("name") or ""))
    seller_name_en = html.escape(str(seller.get("name_en") or seller.get("name") or ""))
    seller_addr_ar = html.escape(str(seller.get("address") or ""))
    seller_addr_en = html.escape(str(seller.get("address_en") or seller.get("address") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))

    customer_name = html.escape(str(customer.get("name") or ""))
    customer_addr = html.escape(str(customer.get("address") or ""))
    customer_vat = html.escape(str(customer.get("vat") or ""))
    # The reference prints the customer's phone here; at the user's request
    # (2026-07-16) every printed phone is replaced by the commercial registration,
    # read from the customer's own CR field.
    customer_cr = html.escape(str(customer.get("cr") or customer.get("commercial_registration") or ""))

    # `discount` is not currently emitted by _collect_preview_data; the reference
    # print shows 0 in that case, which is what the default yields.
    discount = totals.get("discount", 0)
    # (The printed-by / printed-at values the design's footer showed are no longer
    #  read: that footer was removed at the user's request — see the body below.)

    logo_html = (
        f'<img src="{logo}" alt="logo" style="width:86.4px; height:86px; display:block; '
        f'object-fit:contain;" />'
        if logo
        else '<div style="width:86.4px; height:86px;"></div>'
    )

    totals_rows = [
        ("Total", _money(totals.get("subtotal", 0)), "الإجمالى"),
        ("Total Discount", _money(discount), "إجمالى الخصومات"),
        ("Total Taxable Amount", _money(totals.get("subtotal", 0)), "الإجمالى الخاضع للضريبة"),
        ("Total Tax", _money(totals.get("vat", 0)), "إجمالى الضريبة"),
        ("Total With Tax", _money(totals.get("total", 0)), "الإجمالى شامل الضريبة"),
    ]
    totals_html = "".join(
        f'<tr style="height:22px;">'
        f'<td style="border:1px solid {_GRID}; padding:0 5px; font-size:12px;">{en}</td>'
        f'<td style="border:1px solid {_GRID}; background:{_SHADE}; padding:0 5px; '
        f'font-size:12px; text-align:center;">{value}</td>'
        f'<td dir="rtl" style="border:1px solid {_GRID}; padding:0 5px; font-size:12px;">{ar}</td>'
        f"</tr>"
        for en, value, ar in totals_rows
    )

    # ---- the page furniture, identical on every sheet ----------------------
    # A continuation page is a full invoice page — letterhead, title, meta rows —
    # not a bare table, so every sheet of a multi-page invoice stands on its own.
    header_html = f"""
  <!-- ============ DASHED LETTERHEAD FRAME ============ -->
  <div style="position:absolute; left:35.3px; top:35.3px; width:740.2px; height:144.2px; border:1px dashed #000;">
    <div style="position:relative; width:100%; height:100%; padding:12px 14px 0;">

      <!-- centred logo -->
      <div style="position:absolute; left:313.2px; top:10.2px;">{logo_html}</div>

      <!-- left (English) / right (Arabic) letterhead columns -->
      <div style="display:flex; justify-content:space-between; font-size:18.67px; line-height:1.42;">
        <div style="white-space:nowrap;">
          <div>{seller_name_en}</div>
          <div>CR :&nbsp; {seller_cr}</div>
          <div>VAT NO : {seller_vat}</div>
          <div style="margin-top:9px;">Adress :&nbsp; {seller_addr_en}</div>
        </div>
        <div dir="rtl" style="text-align:right; white-space:nowrap;">
          <div>{seller_name_ar}</div>
          <div>سجل تجارى :&nbsp; {seller_cr}</div>
          <div>الرقم الضريبى: {seller_vat}</div>
          <div style="margin-top:9px;">العنوان :&nbsp; {seller_addr_ar}</div>
        </div>
      </div>
    </div>
  </div>

  <!-- ============ TITLE ============ -->
  <div dir="rtl" style="position:absolute; left:0; top:198px; width:816px; text-align:center; font-size:21.33px;">
    <span style="border-bottom:2.24px solid #000; padding-bottom:2px;">فاتورة ضريبية</span>
  </div>

  <!-- ============ PAYMENT METHOD ============ -->
  <div style="position:absolute; left:238.5px; top:246px; width:343.1px;">
    {_meta_row("Payment Method", payment_label, "طريقة الدفع", rtl_value=True)}
  </div>

  <!-- ============ META ROWS — right column (RTL pair) ============ -->
  <div style="position:absolute; left:429.2px; top:294px; width:347.1px;">
    {_meta_row("INvoice No :", invoice_number, "رقم الفاتورة")}
    {_meta_row("Client Name :", customer_name, "اسم العميل", rtl_value=True)}
    {_meta_row("Client Address :", customer_addr, "عنوان العميل", rtl_value=True)}
  </div>

  <!-- ============ META ROWS — left column ============ -->
  <div style="position:absolute; left:44.8px; top:294px; width:347.1px;">
    {_meta_row("Date :", issue_date, "التاريخ")}
    {_meta_row("CR :", customer_cr, "السجل التجارى")}
    {_meta_row("Client Vat  :", customer_vat, "رقم ضريبى للعميل")}
  </div>
"""

    # The closing (shaded) row, the totals grid and the QR are one unit: they are
    # the invoice's result, so they print together, whole, on the last sheet.
    # _ROW_BUDGET_LAST is what reserves the room for them there.
    #
    # The design's own footer strip (printed-at / page x-of-y / printed-by, its
    # black rule, and the address + contact line) is gone at the user's request
    # (2026-07-16); its labels must not reappear anywhere in the emitted HTML.
    band_html = f"""
    <table style="margin-top:{_BLOCK_GAP}px; width:{_TABLE_W}px; border-collapse:collapse; table-layout:fixed;">
      <colgroup>{"".join(f'<col style="width:{w}px;">' for w in _TOTALS_COLS)}</colgroup>
      <tbody>{totals_html}</tbody>
    </table>

    <!-- The QR stands on its own at the shared QR_PRINT_PX size: the design's
         frame around it is gone at the user's request (2026-07-16). -->
    <img src="{qr}" alt="QR" style="margin-top:{_BLOCK_GAP}px; width:{QR_PRINT_PX}px; height:{QR_PRINT_PX}px; display:block; background:#ffffff;" />"""

    pages = paginate(
        lines, _row_height_v3, budget=_ROW_BUDGET, budget_last=_ROW_BUDGET_LAST,
        foot=_FOOT_H,
    )
    sheets = []
    first_index = 1
    for index, page_lines in enumerate(pages, start=1):
        last = index == len(pages)
        foot = page_foot(index, len(pages), sheet_h=_SHEET_H)
        sheets.append(f"""
<div data-sheet style="position:relative; width:816px; height:{_SHEET_H}px; margin:24px auto; background:#fff; font-weight:700; line-height:1.2; overflow:hidden; {sheet_style(last=last)}">
{header_html}
  <!-- ============ ITEMS TABLE + TOTALS + QR ============ -->
  <!-- One flowing column at the user's request (2026-07-16), replacing the
       design's three absolutely-placed blocks: the totals used to be pinned at
       y=806 with the QR beside them and a footer below. Now the totals sit
       directly under the items table however tall it grows, the QR sits under
       the totals, and the footer is gone. The totals table is _TABLE_W wide —
       the items table's own width — so the two line up exactly. -->
  <div style="position:absolute; left:45px; top:{_BLOCK_TOP}px; width:{_TABLE_W}px;">
    <table style="width:{_TABLE_W}px; border-collapse:collapse; table-layout:fixed;">
      <colgroup>{_colgroup()}</colgroup>
      <thead style="background:{_SHADE};">
        {_head_row(("م.س", "رقم الصنف", "اسم الصنف", "الكميه", "السعر",
                    "المبلغ الخاضع للضريبة", "نسبة الضريبة", "مبلغ الضريبة",
                    "إجمالى شامل الضريبة"), rtl=True, height=38)}
        {_head_row(("S.N", "Product Code", "Product Name", "QTY", "Price",
                    "Taxable Amount", "Tax Rate", "Tax Amount", "Total With Tax"),
                   rtl=False, height=38)}
      </thead>
      <tbody>{_item_rows_v3(page_lines, first_index)}{_summary_row(lines, totals) if last else ""}</tbody>
    </table>
{band_html if last else ""}
  </div>
{foot}
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
    background: #53565a;
    font-family: Arial, "Segoe UI", Tahoma, "Arial Unicode MS", sans-serif;
    color: #000;
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


__all__ = ["build_invoice_html_v3"]
