"""Printable Saudi tax-invoice document — **seventh template** (النموذج السابع).

An *additional* invoice layout offered alongside النماذج الأول → السادس. Its
reference is the user's own production document,
``C:\\Users\\esraa\\OneDrive\\Desktop\\المستندات\\النموذج السابع 1025.pdf``.

⚠️ **This template was rebuilt from that PDF (2026-07-15).** It was first built
from the Claude Design bundle at ``D:\\PHASEINV2\\النموذج السابع``
(``untitled/project/Ferio Tax Invoice.dc.html``) and matched *that mock-up* at
0.00px — but the mock-up turned out to be a loose trace of the real document, not
a copy of it. Probing proved the point: the reference PDF is **geometrically
identical** (all 105 rects) to ``uploads/نموذج فيريو.pdf``, which was sitting in
the design bundle all along — same generator, same template, only the data
differs. The mock-up got the item table **mirrored**, the fonts (Cairo, not
Arial), the sizes, the colours and the outer box wrong. The PDF wins; the mock-up
is no longer a reference for anything.

So this follows the **PDF method** (see the handoff, section 6.1): every constant
below was recovered with PyMuPDF from the reference, and the target is ≤1.2pt —
not the 0.00px that only applies when the reference is HTML we can re-render.

The sheet is LTR-structured (as النماذج الأول/الخامس/السادس are); Arabic runs get
``dir="rtl"`` per element. Party-box rows are **absolutely positioned** at their
measured tops, because the reference's own rows are (they overlap slightly and
sit on no regular rhythm — a flow layout cannot reproduce them).

⚠️ Fonts are set through the ``<style>`` block, never through an inline
``style="… font-family:…"``. Font stacks contain quotes, which close an HTML
attribute early and silently drop every declaration that follows — the trap that
cost النموذج الرابع its whole typography (see the handoff, section 5.3).

Long invoices paginate (user, 2026-07-23): the sheet holds nine item rows above
the totals block and twelve without it, and beyond that the rows run onto further
sheets — each a complete invoice page, QR included — with the totals whole on the
last. The table used to be clamped and its surplus rows silently discarded. See
:mod:`app.ui.screens.invoice_pagination`.

Only :func:`build_invoice_html_v7` lives here — it consumes the very same ``data``
dict produced by :meth:`SaudiSalesInvoicePage._collect_preview_data`.

Presentation only: no database access, no ZATCA cryptography.
"""

from __future__ import annotations

import datetime
import html
from typing import Any

from app.ui.screens.invoice_pagination import (
    FOOT_H,
    page_foot,
    paginate,
    row_height,
    sheet_style,
)
from app.ui.screens.saudi_invoice_print import (
    _asset_data_uri,
    _money,
    _qty,
    _rate,
    amount_in_words_ar,
    company_logo_data_uri,
    QR_PRINT_PX,
    qr_vector_data_uri,
)
from app.ui.screens.saudi_invoice_print_v3 import _dec

# ---------------------------------------------------------------------------
# Design tokens — every value measured off the reference PDF
# ---------------------------------------------------------------------------
_LABEL = "#2f3699"    # every «اسم المورد:» style label, and the items header text
_ACCENT = "#903c39"   # the values the reference picks out: names, numbers, نقدى
_INK = "#000000"      # ordinary values, the title, the items rows, the totals
_RULE = "#000000"     # the header box and the two party boxes
_GRID = "#232731"     # the items / totals table rules
_HEAD_BG = "#7cf7f0"  # items-table header band
_DUE_BG = "#efd3d2"   # the totals table's last («المستحق») row
_FONTS = "Arial, 'Segoe UI', Tahoma, sans-serif"

# Font sizes (px). The reference is authored in points — 14/12/11/9pt — which is
# why these are not round numbers: px = pt × 4/3.
_SZ_INFO = 18.63    # 14pt — the title, every party-box row, the date
_SZ_HEAD = 16.01    # 12pt — items-table header
_SZ_ROW = 14.66     # 11pt — items-table body
_SZ_TOTALS = 11.97  #  9pt — the totals table
_SZ_MIN = 11.0      # never shrink a value below this; see _fit_font

# Sheet width, so a measured right edge can be expressed as a CSS `right`.
_SHEET_W = 816

# --- header box + logo + QR -------------------------------------------------
# The reference's header box is 133px tall and holds a 121px QR. The shared QR
# (QR_PRINT_PX) does not fit that, so the box is sized FROM the QR and everything
# between it and the totals moves down by _HEAD_GROWTH, keeping the reference's
# relative geometry intact. The slack comes from the sheet's empty middle (the
# items table ends ~175px above the totals block, which is pinned near the bottom
# and does NOT move), so nothing is pushed off the page.
#
# Derived, not hard-coded, so the box tracks any future QR size automatically —
# it was a literal 43 while the QR was 168px and silently stopped fitting when the
# QR grew to 216.
_HEAD_MARGIN = 4                             # inset above and below the QR
_HEAD_BOX_H = QR_PRINT_PX + 2 * _HEAD_MARGIN  # 224 at 216px
_HEAD_GROWTH = _HEAD_BOX_H - 133              # 91 at 216px; was 43 at 168px
_BOX = (35, 39, 743, _HEAD_BOX_H)             # measured 35.3/38.9/742.9/133.4
_LOGO = (46, 46, 134, 105)       # measured 45.7 / 45.5 / 134.5 / 105.3
# Keeps the reference's right edge (761) and stays vertically centred in the box.
_QR = (761 - QR_PRINT_PX, 39 + (_HEAD_BOX_H - QR_PRINT_PX) / 2,
       QR_PRINT_PX, QR_PRINT_PX)

# --- title / date -----------------------------------------------------------
_TITLE_TOP = 180.58 + _HEAD_GROWTH
_TITLE_MID = 386.3               # measured centre of «فاتورة ضريبية»
_DATE_TOP = 192.43 + _HEAD_GROWTH
_DATE_LABEL_RIGHT = 217.35
_DATE_VALUE_RIGHT = 157.08

# --- party boxes ------------------------------------------------------------
# left/customer measured 42.6..407.4 × 223.2..454.0; right/supplier 410.2..775.0.
_PARTY_TOP = 223 + _HEAD_GROWTH
_PARTY_H = 231
_CUSTOMER_BOX_LEFT = 43
_SUPPLIER_BOX_LEFT = 410
_PARTY_W = 365

# Labels are right-aligned on one x; values are right-aligned on another. The
# gap between them is not fixed — each label is simply as wide as its own text.
_SUP_LABEL_RIGHT = 763.0
_SUP_VALUE_RIGHT = 659.9
_SUP_VALUE_W = 243.0     # bounded by the reference itself: the seller address fits
                         # on one line at 14pt (241.3px) while the full seller name
                         # (≈245px) wraps — so the box is ≥241.3 and <245.
_CUS_LABEL_RIGHT = 395.1
_CUS_VALUE_RIGHT = 279.5
_CUS_VALUE_W = 230.0     # the supplier box's value inset (6.8px from the border),
                         # mirrored onto the customer box's own left edge.

# Row tops (= the text's bbox top, which is where a line box with _LINE_H starts).
_SUP_ROWS = tuple(v + _HEAD_GROWTH for v in (233.11, 266.88, 296.12, 324.84, 363.12, 395.66))
_CUS_ROWS = tuple(v + _HEAD_GROWTH for v in (232.82, 255.90, 278.23, 303.76))

# Arial's ascent+descent. Pinning line-height to it makes a div's top very nearly
# the text's bbox top, so the measured tops above can be used almost verbatim —
# Chromium still lands ~0.85px high (its line box is not exactly ascent+descent),
# which this puts back. Measured, not guessed: without it every row sat 0.5–1.4px
# above the reference; with it they land within ±0.4px.
_LINE_H = 1.117
_TOP_NUDGE = 0.85

# --- items table ------------------------------------------------------------
# Measured rule centres: 40.0 | 87.5 | 503.5 | 558.5 | 645.0 | 774.5 (x) and
# 485.75 | 511.95 | 545.55 (y). With a 1px border + 1px gap grid at left=40 every
# rule lands within 1.5px (1.13pt) of the reference — inside the ≤1.2pt target,
# and on whole pixels, which fractional widths would not be (Chromium blurs or
# drops half-pixel rules).
_ITEMS_LEFT = 40
_ITEMS_TOP = 485 + _HEAD_GROWTH
_ITEM_COLS = (47, 415, 54, 86, 128)   # LTR: م | الوصف | الكميه | سعر الوحدة | الإجمالى
_HEAD_H = 25          # measured 24.8
_ROW_H = 32           # measured 32.0
# The grid's own box: the five columns, the four 1px gaps between them, and its
# 1px border each side. The totals block is cut to this same width — see below.
_ITEMS_W = sum(_ITEM_COLS) + (len(_ITEM_COLS) - 1) + 2   # 736

# --- totals table -----------------------------------------------------------
# The reference draws five *overlapping* boxes, each with its own border, and the
# later ones paint over the earlier ones' borders. That is why the visible rows
# are an irregular 23/19/22/19/31px and not a clean rhythm. Reproducing the same
# five overlapping boxes in DOM order reproduces the same visible result exactly.
#
# Two departures from the reference, both at the user's request (2026-07-17):
#
# 1. The block **flows directly under the items table** instead of being pinned
#    near the foot of the sheet. The reference left 313pt of white between the two
#    on a one-item invoice, and being absolute, the block could not follow the
#    table as it grew — with six items the gap was still 189pt. The rows keep
#    their measured overlaps by staying absolute *within the block*, so only the
#    block moves.
# 2. The block is cut to the items table's own box. It shipped 1px narrower and
#    1px to its left, which printed as a 0.75pt step between two blocks that are
#    meant to read as one.
_TOTALS_ROWS = (                 # (top *within the block*, height) — the measured
    (0.0, 23.8),                 # sheet tops (907.6 …) less the first one, so the
    (22.3, 23.9),                # overlaps survive the move verbatim
    (41.3, 24.0),
    (63.7, 24.0),
    (82.9, 31.8),
)
_TOTALS_H = _TOTALS_ROWS[-1][0] + _TOTALS_ROWS[-1][1]   # 114.7
_TOTALS_GAP = 12                 # items-table bottom -> totals top
_TOTALS_TEXT_RISE = (3.45, 3.45, 3.5, 3.5, 5.6)   # text bbox top − row top

# Sheet x of the totals' three interior anchors. They are expressed against
# _ITEMS_LEFT (the block's left edge) so that moving the block leaves every one of
# them exactly where it already prints: this change moves the block's *edges*, and
# nothing else.
_TOTALS_SPLIT = 600.45           # the label/value divider's centre
_TOTALS_VALUE_MID = 687.25       # values are *centred* in their column
# 556.66 is where the labels' right edge *actually prints*, measured off the PDF.
# It is deliberately not the 599.16 this constant used to hold, which was the
# reference's «labels hug the divider» measurement but never reached the page: the
# CSS read `right: _SHEET_W - _TOTALS_LABEL_RIGHT`, i.e. it subtracted from the
# **sheet's** width while the row — not the sheet — was the containing block, so it
# resolved 42.5px short. Recorded here as the truth so this move reproduces the
# approved output exactly. Whether the labels *should* hug the divider is a
# separate question for the user, and a separate change.
_TOTALS_LABEL_RIGHT = 556.66     # the labels' right edge, as printed

# --- pagination (user, 2026-07-23) -----------------------------------------
# The sheet is a fixed 1056px with `overflow:hidden`. This template's QR is up in
# the header box, so what a long invoice used to lose was **item rows**: the
# items grid carried `max-height: _ITEMS_MAX_H; overflow:hidden`, which kept the
# totals on the page by throwing away every row past the seventeenth — silently,
# and rows had already been compressed to 17px to get that far.
#
# Neither happens now. Rows keep the reference's 32px on every page and a long
# invoice runs onto further sheets, with the totals block whole on the last one.
# The grid's max-height is gone with the compression: the budgets below are what
# keeps the table off the totals, and they are computed from the same numbers.
_SHEET_H = 1056
_SHEET_BOTTOM_MARGIN = 10
_ROW_UNIT = _ROW_H + 1           # the row plus the grid's own 1px gap under it
_ITEMS_MAX_H = _SHEET_H - _SHEET_BOTTOM_MARGIN - _TOTALS_H - _TOTALS_GAP - _ITEMS_TOP
_ROW_BUDGET = _SHEET_H - _SHEET_BOTTOM_MARGIN - _ITEMS_TOP - 2 - _HEAD_H   # 443 -> 13
_ROW_BUDGET_LAST = _ITEMS_MAX_H - 2 - _HEAD_H                              # 316 ->  9
_ITEM_ROW_BUDGET = _ROW_BUDGET_LAST      # the old name, kept for the layout test
_FOOT_H = FOOT_H     # the «صفحة x من y» strip, charged only when x/y > 1

# Name-column metrics for the wrapped-row estimate: the cell's 415px less its 4px
# padding each side, at the grid's own 1.15 line-height, plus the 1px row gap.
_NAME_W = _ITEM_COLS[1] - 8
_NAME_LINE_H = 1.15
_ROW_CHROME = 1.0

# Character widths in ems for Arial Bold, used only by _fit_font. The ASCII
# figures are exact (the handoff's table, section 6.1 step 4); the Arabic one is
# an average taken off the reference's own four Arabic values (0.357–0.423 em),
# so it is an estimate — which is all a guard needs.
_EM_DIGIT = 0.556
_EM_THIN = 0.278      # , and .
_EM_SPACE = 0.278
_EM_ASCII = 0.6       # any other latin character, rounded up
_EM_ARABIC = 0.40

_RTL_ATTRS = ' dir="rtl" lang="ar"'


def _est_em(text: str) -> float:
    """Estimated width of ``text`` in ems at Arial Bold."""
    total = 0.0
    for ch in text:
        if ch.isdigit():
            total += _EM_DIGIT
        elif ch in ",.":
            total += _EM_THIN
        elif ch == " ":
            total += _EM_SPACE
        elif "؀" <= ch <= "ۿ" or "ﭐ" <= ch <= "﻿":
            total += _EM_ARABIC
        else:
            total += _EM_ASCII
    return total


def _fit_font(text: str, width: float, base: float = _SZ_INFO) -> float:
    """Shrink ``text`` until it fits ``width`` on one line.

    The reference does shrink long values — it prints the seller name at 12pt and
    the customer address at 11pt while everything else is 14pt — but by rules that
    could not be recovered: the customer address fits at 14pt and was shrunk
    anyway, and the seller name was shrunk *and* still wrapped. Rather than guess
    at that, values here keep 14pt and shrink only when they genuinely do not fit.

    This matters because the rows are absolutely positioned: a value that wrapped
    would run into the row below it, which is a real collision rather than a
    cosmetic difference.
    """
    em = _est_em(text)
    if em <= 0:
        return base
    return max(_SZ_MIN, min(base, width / em))


def _row_height_v7(line: dict[str, Any]) -> float:
    """How tall this line's row will really be — see :mod:`invoice_pagination`.

    Rows are no longer compressed to fit (they used to shrink towards 17px, at
    which point الوصف clipped and the surplus rows were thrown away anyway):
    every row keeps the reference's 32px and the invoice paginates instead.
    """
    return row_height(
        str(line.get("name") or ""),
        base=_ROW_UNIT,
        width_px=_NAME_W,
        font_px=_SZ_ROW,
        line_height=_NAME_LINE_H,
        chrome=_ROW_CHROME,
    )


def _info_row(top: float, label: str, value: str, *, label_right: float,
              value_right: float, value_w: float, colour: str = _INK,
              underline: bool = False) -> str:
    """One «label : value» line inside a party box, at its measured top.

    The colon belongs to the label in the reference (it right-aligns «اسم المورد:»
    as one run), so it is part of the label string here too.
    """
    size = _fit_font(value, value_w) if value else _SZ_INFO
    cls = "val ul" if underline else "val"
    top += _TOP_NUDGE
    return (
        f'<div dir="rtl" lang="ar" class="lbl" style="top:{top}px; '
        f'right:{_SHEET_W - label_right}px">{label}</div>'
        f'<div dir="rtl" lang="ar" class="{cls}" style="top:{top}px; '
        f'right:{_SHEET_W - value_right}px; max-width:{value_w}px; '
        f'font-size:{size:.2f}px; color:{colour}">{value}</div>'
    )


def _items_html(lines: list[dict[str, Any]], start: int = 1) -> str:
    """The header band plus this page's rows; «م» numbers across the invoice."""
    height = _ROW_H
    head = "".join(
        f'<div dir="rtl" lang="ar" class="hcell">{t}</div>'
        for t in ("م", "الوصف", "الكميه", "سعر الوحدة", "الإجمالى")
    )
    body = []
    for index, line in enumerate(lines, start=start):
        # «الإجمالى» is the line total *before* VAT: the reference's own sample
        # (891 × 59.00 = 52,569.00) feeds its totals grid's «الإجمالى», which VAT
        # is then added to. The table carries no per-line tax column.
        cells = (
            (str(index), False),
            (html.escape(str(line.get("name") or "")), True),
            (_qty(line.get("qty")), False),
            (_money(line.get("price")), False),
            (_money(line.get("before")), False),
        )
        body.append("".join(
            f'<div{_RTL_ATTRS if rtl else ""} class="cell" '
            f'style="min-height:{height}px">{text}</div>'
            for text, rtl in cells
        ))
    return head + "".join(body)


def _totals_html(rows: "tuple[tuple[str, str, bool], ...]") -> str:
    out = []
    for index, (label, value, due) in enumerate(rows):
        top, height = _TOTALS_ROWS[index]
        rise = _TOTALS_TEXT_RISE[index]
        bg = _DUE_BG if due else "#ffffff"
        out.append(
            f'<div class="trow" style="top:{top}px; height:{height}px; background:{bg}">'
            f'<div class="tsplit"></div>'
            f'<div dir="rtl" lang="ar" class="tlabel" style="top:{rise}px; '
            f'right:{_ITEMS_LEFT + _ITEMS_W - 1 - _TOTALS_LABEL_RIGHT}px">{label}</div>'
            f'<div class="tvalue" style="top:{rise}px">{value}</div>'
            f"</div>"
        )
    return "".join(out)


def _vat_rate_label(lines: list[dict[str, Any]]) -> str:
    """«ضريبة القيمة المضافة 15%» — the reference's wording, with the live rate.

    The reference hard-codes 15%. Real invoices carry a rate per line, so the rate
    is named only when every line agrees on one (which every ordinary invoice
    does); a mixed-rate invoice drops the number rather than print a wrong one.
    """
    rates = {str(_dec(line.get("vat_rate"))) for line in lines}
    if len(rates) == 1:
        return f"ضريبة القيمة المضافة {_rate(lines[0].get('vat_rate'))}"
    return "ضريبة القيمة المضافة"


def build_invoice_html_v7(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML for النموذج السابع from live data."""
    logo = company_logo_data_uri(data.get("seller")) or _asset_data_uri("logo.png")
    qr = qr_vector_data_uri(data.get("qr_payload"))

    seller = data.get("seller") or {}
    customer = data.get("customer") or {}
    totals = data.get("totals") or {}
    lines = data.get("lines") or []

    issued = data.get("issue_datetime")
    if isinstance(issued, datetime.datetime):
        issue_date = issued.strftime("%Y-%m-%d")   # the reference's own format
    else:
        issue_date = html.escape(str(issued or ""))

    subtotal = _dec(totals.get("subtotal"))
    # No «discount» key is produced by _collect_preview_data today, so this prints
    # 0.00 and «الإجمالي بعد الخصم» equals «الإجمالى» — the same open point every
    # other template carries (see the handoff, section 8.5).
    discount = _dec(totals.get("discount"))
    vat_total = _dec(totals.get("vat"))
    grand_total = _dec(totals.get("total"))

    esc = lambda v: html.escape(str(v or ""))  # noqa: E731

    logo_html = (
        f'<img src="{logo}" alt="logo" style="position:absolute; left:{_LOGO[0]}px; '
        f'top:{_LOGO[1]}px; width:{_LOGO[2]}px; height:{_LOGO[3]}px; '
        f'object-fit:contain" />'
        if logo
        else ""
    )

    supplier = (
        _info_row(_SUP_ROWS[0], "اسم المورد :", esc(seller.get("name")),
                  label_right=_SUP_LABEL_RIGHT, value_right=_SUP_VALUE_RIGHT,
                  value_w=_SUP_VALUE_W, colour=_ACCENT)
        + _info_row(_SUP_ROWS[1], "الرقم الضريبى :", esc(seller.get("vat")),
                    label_right=_SUP_LABEL_RIGHT, value_right=_SUP_VALUE_RIGHT,
                    value_w=_SUP_VALUE_W)
        + _info_row(_SUP_ROWS[2], "السجل التجارى :", esc(seller.get("cr")),
                    label_right=_SUP_LABEL_RIGHT, value_right=_SUP_VALUE_RIGHT,
                    value_w=_SUP_VALUE_W)
        + _info_row(_SUP_ROWS[3], "العنوان :", esc(seller.get("address")),
                    label_right=_SUP_LABEL_RIGHT, value_right=_SUP_VALUE_RIGHT,
                    value_w=_SUP_VALUE_W)
        + _info_row(_SUP_ROWS[4], "رقم الفاتورة :", esc(data.get("invoice_number")),
                    label_right=_SUP_LABEL_RIGHT, value_right=_SUP_VALUE_RIGHT,
                    value_w=_SUP_VALUE_W, colour=_ACCENT, underline=True)
        # «نقدي» / «آجل», straight from the screen's payment combo.
        + _info_row(_SUP_ROWS[5], "نوع الدفع :", esc(data.get("payment_label")),
                    label_right=_SUP_LABEL_RIGHT, value_right=_SUP_VALUE_RIGHT,
                    value_w=_SUP_VALUE_W, colour=_ACCENT, underline=True)
    )

    customer_html = (
        # The reference prints the customer's master-record number («22»).
        _info_row(_CUS_ROWS[0], "رقم العميل :", esc(customer.get("code")),
                  label_right=_CUS_LABEL_RIGHT, value_right=_CUS_VALUE_RIGHT,
                  value_w=_CUS_VALUE_W, colour=_ACCENT)
        + _info_row(_CUS_ROWS[1], "اسم العميل :", esc(customer.get("name")),
                    label_right=_CUS_LABEL_RIGHT, value_right=_CUS_VALUE_RIGHT,
                    value_w=_CUS_VALUE_W, colour=_ACCENT)
        # The reference prints the customer's VAT in the label's own blue while the
        # seller's is black. It is almost certainly a slip in their generator, but
        # it is what the document does, so it is what this prints.
        + _info_row(_CUS_ROWS[2], "الرقم الضريبى :", esc(customer.get("vat")),
                    label_right=_CUS_LABEL_RIGHT, value_right=_CUS_VALUE_RIGHT,
                    value_w=_CUS_VALUE_W, colour=_LABEL)
        + _info_row(_CUS_ROWS[3], "عنوان العميل :", esc(customer.get("address")),
                    label_right=_CUS_LABEL_RIGHT, value_right=_CUS_VALUE_RIGHT,
                    value_w=_CUS_VALUE_W)
    )

    totals_html = _totals_html((
        ("الإجمالى", _money(subtotal), False),
        ("الخصم", _money(discount), False),
        ("الإجمالي بعد الخصم", _money(subtotal - discount), False),
        (_vat_rate_label(lines), _money(vat_total), False),
        # The reference writes «فقط … ريال»; the shared helper's wording
        # («… ريالاً لا غير») is used instead, as in every other template.
        (f"المستحق : {html.escape(amount_in_words_ar(grand_total))}",
         _money(grand_total), True),
    ))

    grid_cols = " ".join(f"{c}px" for c in _ITEM_COLS)

    # ---- the page furniture, identical on every sheet ----------------------
    # A continuation page is a full invoice page — header box, logo, QR, title,
    # date and both party boxes — so every sheet stands on its own.
    page_top = f"""
  <!-- ============ HEADER BOX (logo + QR only) ============ -->
  <div class="box" style="left:{_BOX[0]}px; top:{_BOX[1]}px; width:{_BOX[2]}px; height:{_BOX[3]}px"></div>
  {logo_html}
  <img src="{qr}" alt="qr" style="position:absolute; left:{_QR[0]}px; top:{_QR[1]}px; width:{_QR[2]}px; height:{_QR[3]}px; object-fit:contain; background:#ffffff;" />

  <!-- ============ TITLE / DATE ============ -->
  <div dir="rtl" lang="ar" style="position:absolute; top:{_TITLE_TOP + _TOP_NUDGE}px; left:0; width:{_TITLE_MID * 2}px; text-align:center; font-size:{_SZ_INFO}px; line-height:{_LINE_H}">فاتورة ضريبية</div>
  <div dir="rtl" lang="ar" class="lbl" style="top:{_DATE_TOP + _TOP_NUDGE}px; right:{_SHEET_W - _DATE_LABEL_RIGHT}px">التاريخ :</div>
  <div class="val" style="top:{_DATE_TOP + _TOP_NUDGE}px; right:{_SHEET_W - _DATE_VALUE_RIGHT}px">{issue_date}</div>

  <!-- ============ SUPPLIER (right) / CUSTOMER (left) ============ -->
  <div class="box" style="left:{_SUPPLIER_BOX_LEFT}px; top:{_PARTY_TOP}px; width:{_PARTY_W}px; height:{_PARTY_H}px"></div>
  <div class="box" style="left:{_CUSTOMER_BOX_LEFT}px; top:{_PARTY_TOP}px; width:{_PARTY_W}px; height:{_PARTY_H}px"></div>
  {supplier}
  {customer_html}
"""

    pages = paginate(
        lines, _row_height_v7, budget=_ROW_BUDGET, budget_last=_ROW_BUDGET_LAST,
        foot=_FOOT_H,
    )
    sheets = []
    first_index = 1
    for index, page_lines in enumerate(pages, start=1):
        last = index == len(pages)
        foot = page_foot(index, len(pages), sheet_h=_SHEET_H, sheet_w=_SHEET_W,
                         colour=_LABEL)
        # The totals block flows under the table (user, 2026-07-17), so on the
        # last sheet it follows that sheet's last row; the other sheets end at
        # the table, and _ROW_BUDGET keeps them clear of the page's foot.
        sheets.append(f"""
<div data-sheet dir="ltr" style="position:relative; width:{_SHEET_W}px; height:{_SHEET_H}px; margin:24px auto; background:#fff; box-shadow:0 2px 18px rgba(0,0,0,.18); overflow:hidden; {sheet_style(last=last)}">
{page_top}
  <!-- ============ ITEMS + TOTALS ============ -->
  <div class="block">
    <div class="items">{_items_html(page_lines, first_index)}</div>
    {f'<div class="totals">{totals_html}</div>' if last else ""}
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
    background: #ececec;
    font-family: {_FONTS};
    font-weight: 700;
    color: {_INK};
    -webkit-font-smoothing: antialiased;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  @media print {{
    body {{ background: #fff; }}
    [data-sheet] {{ box-shadow: none !important; margin: 0 !important; }}
  }}
  .lbl, .val {{
    position: absolute; line-height: {_LINE_H}; white-space: nowrap;
    font-size: {_SZ_INFO}px;
  }}
  .lbl {{ color: {_LABEL}; }}
  .val {{ overflow: hidden; text-overflow: clip; }}
  .ul {{
    text-decoration: underline; text-decoration-color: {_ACCENT};
    text-decoration-thickness: 1.92px; text-underline-offset: 2px;
  }}
  .box {{ position: absolute; border: 1px solid {_RULE}; }}
  /* Items + totals are ONE positioned block: the totals flow under the table, so
     they follow it however many rows it has, and both are cut to the same box. */
  .block {{
    position: absolute; left: {_ITEMS_LEFT}px; top: {_ITEMS_TOP}px;
    width: {_ITEMS_W}px;
  }}
  /* No `max-height` / `overflow:hidden` any more: the grid used to be clamped to
     _ITEMS_MAX_H, which kept the totals on the page by silently discarding every
     row past it. The invoice paginates instead — see _ROW_BUDGET above. */
  .items {{
    display: grid; grid-template-columns: {grid_cols}; gap: 1px;
    background: {_GRID}; border: 1px solid {_GRID}; line-height: 1.15;
  }}
  .totals {{ position: relative; height: {_TOTALS_H}px; margin-top: {_TOTALS_GAP}px; }}
  .hcell {{
    background: {_HEAD_BG}; color: {_LABEL}; font-size: {_SZ_HEAD}px;
    height: {_HEAD_H}px; display: flex; align-items: center;
    justify-content: center; text-align: center;
  }}
  /* `anywhere`, not `break-word`: only `anywhere` also shrinks min-content, and
     min-content is exactly what the flex/grid automatic minimum is measured
     against — with `break-word` (what this shipped with) an item name typed as
     one unbroken token still forced the cell wider than its px track and ran out
     over the columns beside it. `min-width:0` releases that same minimum on the
     cell itself. Together: the name wraps inside its column and grows the row. */
  .cell {{
    background: #fff; color: {_INK}; font-size: {_SZ_ROW}px;
    display: flex; align-items: center; justify-content: center;
    text-align: center; padding: 0 4px;
    min-width: 0; overflow-wrap: anywhere;
  }}
  /* Each row spans the block, so the totals and the table share both edges. The
     three interior anchors below subtract _ITEMS_LEFT (the block's left) and the
     row's own 1px border, which is what keeps them on the same sheet x they were
     measured at while the block's edges move. */
  .trow {{
    position: absolute; left: 0; width: 100%;
    border: 1px solid {_GRID}; font-size: {_SZ_TOTALS}px; line-height: {_LINE_H};
  }}
  .tsplit {{
    position: absolute; top: -1px; bottom: -1px;
    left: {_TOTALS_SPLIT - _ITEMS_LEFT - 0.65}px; width: 1.3px; background: {_GRID};
  }}
  .tlabel {{ position: absolute; white-space: nowrap; }}
  .tvalue {{
    position: absolute; white-space: nowrap;
    left: {_TOTALS_VALUE_MID - _ITEMS_LEFT}px; transform: translateX(-50%);
  }}
</style>
</head>
<body>
{"".join(sheets)}
</body>
</html>"""


__all__ = ["build_invoice_html_v7"]
