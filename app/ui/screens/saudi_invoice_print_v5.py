"""Printable Saudi tax-invoice document — **fifth template** (النموذج الخامس).

An *additional* invoice layout offered alongside النموذج الأول
(:mod:`app.ui.screens.saudi_invoice_print`), النموذج الثاني
(:mod:`app.ui.screens.saudi_invoice_print_v2`), النموذج الثالث
(:mod:`app.ui.screens.saudi_invoice_print_v3`) and النموذج الرابع
(:mod:`app.ui.screens.saudi_invoice_print_v4`). It reproduces the reference
print supplied as ``نموذج جديد 3.pdf``: a bilingual half-page layout — the
seller's details in English (left) and Arabic (right) flanking a centred logo,
the QR at the left with a two-line «فاتورة ضريبية / فاتورة مبيعات» title beside
it, two three-row meta boxes, a grey «اسم العميل» bar, a seven-column bilingual
items table, a five-row totals grid carrying the amount in words, and the
signature footer.

Every coordinate below was measured off the reference PDF (Letter, 612.16 ×
792.06 pt) and converted to CSS pixels at 96 dpi (``pt × 4/3``), landing on the
same 816 × 1056 px design sheet the other templates use — so the existing
preview dialog and PDF exporter render it unchanged.

Unlike النموذج الرابع the sheet is **LTR-structured** (as النموذج الأول is):
the reference's own items table reads Sn → Total left-to-right, so every block
is placed by its ``left`` offset and Arabic runs get ``dir="rtl"`` per element.
That sidesteps the RTL ``margin-right`` trap documented for النموذج الرابع.

⚠️ Fonts are set through the ``<style>`` block, never through an inline
``style="… font-family:…"``. The font stacks contain double quotes, which close
an HTML attribute early and silently drop every declaration that follows —
النموذج الرابع still does this and loses its own ``font-size`` as a result.

Long invoices paginate (user, 2026-07-23): the sheet holds nine item rows above
the totals grid, and beyond that the rows run onto further sheets — each a
complete invoice page, QR included — with the totals printed whole on the last
one. They used to be cut off by the sheet's ``overflow:hidden`` instead. See
:mod:`app.ui.screens.invoice_pagination`.

Only :func:`build_invoice_html_v5` lives here — it consumes the very same
``data`` dict produced by
:meth:`SaudiSalesInvoicePage._collect_preview_data`.

Presentation only: no database access, no ZATCA cryptography.
"""

from __future__ import annotations

import datetime
import html
from decimal import Decimal
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
    amount_in_words_ar,
    company_logo_data_uri,
    QR_PRINT_PX,
    qr_vector_data_uri,
)
from app.ui.screens.saudi_invoice_print_v2 import _vat_rate_display
from app.ui.screens.saudi_invoice_print_v3 import _dec, _fit_px

# ---------------------------------------------------------------------------
# Design tokens measured off the reference PDF
# ---------------------------------------------------------------------------
_GRID = "#242732"    # items-table + totals-grid rules  (fill 0.141/0.153/0.196)
_HAIRLINE = "#000"   # the two meta boxes + the name bar (fill 0/0/0)
_BAR = "#a6a6a6"     # «اسم العميل» bar fill             (fill 0.6509…)

_ARIAL = 'Arial, "Segoe UI", Tahoma, "Arial Unicode MS", sans-serif'
_SERIF = '"Times New Roman", Times, serif'

# Reference font sizes, pt × 4/3.
_HEAD_PX = 18.63   # 13.97pt — seller block
_META_PX = 13.32   # 9.99pt  — the two meta boxes
_BAR_PX = 15.97    # 11.98pt — «اسم العميل» bar
_THEAD_PX = 14.67  # 11.00pt — items-table header
_BODY_PX = 13.35   # 10.01pt — items-table body
_TOTAL_PX = 11.97  # 8.98pt  — totals grid + amount in words

# Items-table columns, left-to-right as the reference orders them. Widths are
# whole px (fractional tracks blur the rules — see the handoff) summing to 707,
# the measured table width; every boundary lands within 0.4pt of the reference.
_COLUMNS: "tuple[tuple[str, str, int], ...]" = (
    ("الرقم", "Sn", 67),
    ("رقم الصنف", "No", 70),
    ("اسم الصنف", "", 245),
    ("الكميه", "Qty", 69),
    ("سعر الوحدة", "Price", 75),
    ("الضريبة", "Vat", 67),
    ("القيمة الإجمالى", "Total", 114),
)
_NAME_COLUMN = 2  # اسم الصنف — wraps and grows the row instead of shrinking

# Measured block geometry, in CSS px on the 816 × 1056 sheet. Under
# border-collapse Chromium centres the outer rule on the table edge, so the
# table is inset by half a border to put its ink where the reference's is.
# --- breathing room around the 168px QR (user request 2026-07-16) ------------
# The enlarged QR ended up wedged between the seller's «Adress» line above and
# the meta boxes below (it even overlapped their top border by ~6px). The design
# has no slack above it — «Adress» sits directly on top and the row above that is
# only 16px further up — so the room is made in three small moves instead of one:
#   * the «Adress» row rises by _ADDRESS_LIFT (it had the block's widest gap),
#   * the QR rises by _QR_LIFT (less than the address, so a real gap opens above),
#   * everything below the QR drops by _BELOW_SHIFT.
# The sheet had ~285px of unused height at the bottom (now more, with the
# signature strip gone), so the drop costs nothing.
_ADDRESS_LIFT = 8.0
_QR_LIFT = 5.0

# _BELOW_SHIFT is DERIVED from the QR, not a literal: it was tuned by hand for a
# 168px code and silently let the meta boxes ride up under the QR when it grew to
# 216 (the boxes' top border ended up ~22px inside the code). Computing it means
# the clearance survives the next size change too.
_QR_TOP = 153 - _QR_LIFT
_QR_BOTTOM = _QR_TOP + QR_PRINT_PX
_QR_CLEARANCE = 8.0          # visible gap between the QR and the meta boxes
_BOX_TOP_BASE = 314.7        # the reference's own meta-box top
_BELOW_SHIFT = max(27.3, _QR_BOTTOM + _QR_CLEARANCE - _BOX_TOP_BASE)

_TABLE_LEFT = 38.5
_TABLE_WIDTH = 707
_TABLE_TOP = 474.7 + _BELOW_SHIFT
_THEAD_H = 56.6
_ROW_H = 34.4

# The totals grid is NOT flush with the items table: the reference sets its left
# rule 0.6pt further left (39.5px ink vs 40.3px). That is below one device pixel
# at print resolution, so it is reproduced rather than "tidied".
_TOTALS_LEFT = 39
_TOTALS_COLUMNS = (481, 88, 139)   # value | English label | Arabic label
_TOTALS_ROW_H = (22, 23, 23, 22, 30)
_TOTALS_GAP = 13.7   # items-table bottom -> totals top
_FOOTER_GAP = 14.1   # totals bottom -> footer top

# --- pagination (user, 2026-07-23) -----------------------------------------
# The sheet is a fixed 1056px with `overflow:hidden`, so a tenth item used to
# push the totals grid off the bottom edge and Chromium cut it without a word.
# Rows keep the reference's 34.4px on every page now; the surplus runs onto
# further sheets and the totals grid prints whole on the last one.
_SHEET_H = 1056
_FOOT_H = FOOT_H     # the «صفحة x من y» strip, charged only when x/y > 1
_BAND_H = _TOTALS_GAP + sum(_TOTALS_ROW_H)                    # 133.7
_ROW_BUDGET = _SHEET_H - _TABLE_TOP - _THEAD_H                # 467.4 -> 13 rows
_ROW_BUDGET_LAST = _ROW_BUDGET - _BAND_H                      # 333.7 ->  9 rows

# Name-column metrics for the wrapped-row estimate: the column's 245px less its
# 3px padding each side and its 3px collapsed border, and the row's own vertical
# padding (2 top / 10.3 bottom) plus that border.
_NAME_W = _COLUMNS[_NAME_COLUMN][2] - 9
_NAME_LINE_H = 1.3
_ROW_CHROME = 2 + 10.3 + 3

# The meta boxes are placed by their measured ink centres (315.2 / 349.73 /
# 383.6 / 417.73 abs px). Row heights are explicit rather than uniform: the
# reference's own three rows differ (25.9 / 25.4 / 25.6 pt), and the container's
# 1px border shifts its absolutely-positioned children by a further 1px, which
# a uniform height silently accumulated into a 1.4pt drift on the last rule.
_BOX_TOP = _BOX_TOP_BASE + _BELOW_SHIFT
_BOX_H = 103.53
_BOX_ROW_H = (33.53, 33.87, 34.13)

# The reference does not centre text in its rows — every line rides ~2.6pt above
# the row's centre (its generator top-aligns within a padded cell). Centring
# alone left this block 2.3-3.2pt low, so each row carries the measured lift.
# Same story in the name bar and the totals grid below.
_BOX_RISE = (3.03, 3.73, 4.21)
_BAR_RISE = 1.87
_TOTALS_RISE = (1.93, 1.60, 1.59, 2.57, 4.25)
# Items-table body: the cells are vertical-align:middle, so lifting the text
# means padding the bottom by twice the wanted rise (3.1pt = 4.13px).
_BODY_PAD_BOTTOM = 10.3

# Each meta row gets its own value box, because the free space between its two
# fixed labels differs per row (the reference's «Payment Method» is far wider
# than its «Issue Date», and «عنوان العميل :» far shorter than
# «الرقم الضريبى للعميل :»). A single shared box starved the address row and
# _fit_px shrank it to 6pt.
#
# The two boxes anchor their values differently, which is what the reference
# actually does: all three right-box values sit on one centre line (431.0pt),
# while the left box's values start at one left edge (~123.4pt).
_LEFT_BOX = dict(left=36.1, width=355.8)
_RIGHT_BOX = dict(left=390.8, width=352.6)
_LEFT_VALUE_X = 164.5      # abs px; values are left-aligned here
_RIGHT_VALUE_CX = 574.67   # abs px; values are centred here


def _rate1(value: Any) -> str:
    """VAT rate as the reference prints it — one decimal, e.g. ``15.0%``."""
    try:
        return f"{Decimal(str(value)):.1f}%"
    except Exception:  # noqa: BLE001 - defensive formatting
        return f"{value}%"


def _fit_ascii(text: str, width_px: int, base_px: float) -> float:
    """``_fit_px``, but only for figures — never for Arabic prose.

    ``_fit_px`` measures with a digit-tuned em table, which badly over-estimates
    Arabic: it sized the reference's own 150px address at 7.2pt inside a 158px
    box. Numbers still get the anti-clipping guard; Arabic text is left alone.
    """
    if not text.isascii():
        return base_px
    return _fit_px(text, width_px, base_px)


def _meta_box(box: dict[str, float], rows: "tuple[tuple[str, str, str, float], ...]",
              *, centre: float | None) -> str:
    """One three-row meta box: English label left, value, Arabic label right.

    ``rows`` carries each row's ``limit`` — the absolute x of the Arabic label's
    left edge, i.e. how far that row's value may run before it would collide.
    """
    cells = []
    for index, (english, arabic, value, limit) in enumerate(rows):
        top = sum(_BOX_ROW_H[:index])
        border = "" if index == 0 else f"border-top:1px solid {_HAIRLINE};"
        if centre is None:
            left, width, align = _LEFT_VALUE_X, limit - _LEFT_VALUE_X, "left"
        else:
            half = limit - centre
            left, width, align = centre - half, half * 2, "center"
        size = _fit_ascii(value, int(width), _META_PX) if value else _META_PX
        mid = f"top:50%; transform:translateY(calc(-50% - {_BOX_RISE[index]}px));"
        cells.append(
            f'<div style="position:absolute; left:0; top:{top}px; '
            f'width:{box["width"]}px; height:{_BOX_ROW_H[index]}px; {border}">'
            f'<div style="position:absolute; left:5.03px; {mid} '
            f'white-space:nowrap;">{english}</div>'
            f'<div style="position:absolute; right:4.4px; {mid} '
            f'white-space:nowrap;" dir="rtl">{arabic}</div>'
            f'<div style="position:absolute; left:{left - box["left"]}px; {mid} '
            f'width:{width}px; text-align:{align}; '
            f'font-size:{size}px; white-space:nowrap;" dir="rtl">{value}</div>'
            f"</div>"
        )
    return (
        f'<div style="position:absolute; left:{box["left"]}px; top:{_BOX_TOP}px; '
        f'width:{box["width"]}px; height:{_BOX_H}px; '
        f'border:1px solid {_HAIRLINE}; font-size:{_META_PX}px;">{"".join(cells)}</div>'
    )


def _header_row() -> str:
    """The items-table header — Arabic over English, both centred."""
    cells = []
    for arabic, english, _ in _COLUMNS:
        second = f'<div style="margin-top:1px;">{english}</div>' if english else ""
        cells.append(
            f'<th class="th" style="font-size:{_THEAD_PX}px;">'
            f'<div dir="rtl">{arabic}</div>{second}</th>'
        )
    return f'<tr style="height:{_THEAD_H}px;">{"".join(cells)}</tr>'


def _row_height_v5(line: dict[str, Any]) -> float:
    """How tall this line's row will really be — see :mod:`invoice_pagination`."""
    return row_height(
        str(line.get("name") or ""),
        base=_ROW_H,
        width_px=_NAME_W,
        font_px=_BODY_PX,
        line_height=_NAME_LINE_H,
        chrome=_ROW_CHROME,
    )


def _item_rows_v5(lines: list[dict[str, Any]], start: int = 1) -> str:
    """«الرقم» numbers items across the whole invoice, not per page."""
    rows: list[str] = []
    for index, line in enumerate(lines, start=start):
        values = (
            str(index),
            html.escape(str(line.get("code") or "")),
            html.escape(str(line.get("name") or "")),
            _qty(line.get("qty")),
            _money(line.get("price")),
            _rate1(line.get("vat_rate")),
            _money(line.get("total")),
        )
        cells = []
        for column, value in enumerate(values):
            width = _COLUMNS[column][2]
            if column == _NAME_COLUMN:
                # Long names wrap and grow the row rather than shrinking their
                # own text, so the whole table keeps one font size.
                style = f"font-size:{_BODY_PX}px; line-height:1.3; word-wrap:break-word;"
            else:
                # Wide enough for any realistic figure; this guard only ever
                # engages to stop a freak value being silently clipped.
                style = f"font-size:{_fit_px(value, width, _BODY_PX)}px; white-space:nowrap;"
            cells.append(f'<td class="td" style="{style}" dir="rtl">{value}</td>')
        rows.append(f'<tr style="height:{_ROW_H}px; background:#fff;">{"".join(cells)}</tr>')
    return "".join(rows)


def _totals_grid(rows: "tuple[tuple[str, str, str], ...]", words: str) -> str:
    """The five-row totals grid; the last row also carries the amount in words."""
    out = []
    for index, (arabic, english, value) in enumerate(rows):
        last = index == len(rows) - 1
        # The amount in words sits in its own white box inside the last row's
        # value cell, right-aligned well clear of the figure.
        words_html = (
            f'<div style="position:absolute; left:6.8px; top:4.3px; width:340.9px; '
            f'height:22.3px; background:#fff; text-align:right; '
            f'white-space:nowrap; overflow:hidden;" dir="rtl">{words}</div>'
            if last and words
            else ""
        )
        mid = f"top:50%; transform:translateY(calc(-50% - {_TOTALS_RISE[index]}px));"
        out.append(
            f'<tr style="height:{_TOTALS_ROW_H[index]}px;">'
            f'<td class="tt" style="position:relative;">{words_html}'
            f'<div style="position:absolute; right:1.0px; {mid} '
            f'white-space:nowrap;">{value}</div></td>'
            f'<td class="tt" style="position:relative;">'
            f'<div style="position:absolute; left:1.9px; {mid} '
            f'white-space:nowrap;">{english}</div></td>'
            f'<td class="tt" style="position:relative;">'
            f'<div style="position:absolute; right:0.9px; {mid} '
            f'white-space:nowrap;" dir="rtl">{arabic}</div></td>'
            f"</tr>"
        )
    cols = "".join('<col style="width:%dpx;">' % w for w in _TOTALS_COLUMNS)
    return (
        f'<table style="margin-left:{_TOTALS_LEFT}px; margin-top:{_TOTALS_GAP}px; '
        f'width:{_TABLE_WIDTH}px; border-collapse:collapse; table-layout:fixed; '
        f'font-size:{_TOTAL_PX}px;">'
        f"<colgroup>{cols}</colgroup><tbody>{''.join(out)}</tbody></table>"
    )


def _footer() -> str:
    """No footer: the signature strip is gone at the user's request (2026-07-16).

    The reference prints «توقيع المستلم / Received By» and «توقيع المسؤول /
    Authorized Signture» under the totals. The user does not want them, so the
    invoice simply ends at the totals grid. Kept as a function (rather than
    deleting the call site) so the reference's own layout stays documented and
    the strip is one return away should it ever be wanted back.
    """
    return ""


def build_invoice_html_v5(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML for النموذج الخامس from live data."""
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

    payment = html.escape(str(data.get("payment_label") or ""))
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_name_en = html.escape(str(seller.get("name_en") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))
    seller_address_en = html.escape(str(seller.get("address_en") or ""))

    customer_name = html.escape(str(customer.get("name") or ""))
    customer_vat = html.escape(str(customer.get("vat") or ""))
    customer_address = html.escape(str(customer.get("address") or ""))
    # The reference prints a «رقم جوال العميل» row here. At the user's request
    # (2026-07-16) every printed phone is replaced by the commercial registration,
    # read from the customer's own CR field — which, unlike the phone, is part of
    # what _collect_preview_data carries, so the row is no longer always blank.
    customer_cr = html.escape(str(customer.get("cr") or customer.get("commercial_registration") or ""))

    subtotal = _dec(totals.get("subtotal"))
    # No «discount» key is produced by _collect_preview_data today, so this
    # prints 0.00 and الصافى equals الإجمالى قبل الضريبة — the same open point
    # the second and third templates carry (see the handoff, section 8.5).
    discount = _dec(totals.get("discount"))
    vat_total = _dec(totals.get("vat"))
    grand_total = _dec(totals.get("total"))
    net = subtotal - discount
    vat_rate = _vat_rate_display(subtotal, vat_total, lines)

    logo_html = (
        f'<img src="{logo}" alt="logo" style="position:absolute; left:334.0px; '
        f'top:35.9px; width:125.1px; height:95.6px; object-fit:contain;" />'
        if logo
        else ""
    )

    # The address row rises by _ADDRESS_LIFT to open the gap above the QR; the
    # rows above it keep their measured tops (it had the block's widest gap —
    # 36.9px vs ~27 — so it can give 8px up and still clear «VAT NO»).
    address_top = 134.0 - _ADDRESS_LIFT
    en_rows = (
        (41.7, seller_name_en),
        (68.4, f"CR :&nbsp;&nbsp;{seller_cr}"),
        (97.1, f"VAT NO : {seller_vat}"),
        (address_top, f"Adress : {seller_address_en}"),
    )
    ar_rows = (
        (41.7, seller_name),
        (68.4, f"سجل تجارى : {seller_cr}"),
        (97.1, f"الرقم الضريبى : {seller_vat}"),
        (address_top, f"العنوان : {seller_address}"),
    )
    header_html = "".join(
        f'<div style="position:absolute; left:36.7px; top:{top}px; '
        f'white-space:nowrap;">{text}</div>'
        for top, text in en_rows
    ) + "".join(
        f'<div style="position:absolute; right:70.3px; top:{top}px; '
        f'white-space:nowrap;" dir="rtl">{text}</div>'
        for top, text in ar_rows
    )

    # ---- the page furniture, identical on every sheet ----------------------
    # A continuation page is a full invoice page — seller block, QR, title, meta
    # boxes, customer bar — not a bare table, so any sheet of a multi-page
    # invoice identifies the invoice it belongs to. The QR is part of it and so
    # prints on every page, as it did before pagination existed.
    page_top = f"""
  <!-- ============ SELLER BLOCK + LOGO ============ -->
  <div style="font-size:{_HEAD_PX}px; line-height:1;">{header_html}</div>
  {logo_html}

  <!-- ============ QR + TITLE ============ -->
  <!-- The QR does not fit the design's own gap, so it was lifted from 179.5 and
       the blocks around it were moved away from it — see _QR_LIFT /
       _ADDRESS_LIFT / _BELOW_SHIFT. The title sits at x≥324, well clear of it. -->
  <img src="{qr}" alt="QR" style="position:absolute; left:45.5px; top:{_QR_TOP}px; width:{QR_PRINT_PX}px; height:{QR_PRINT_PX}px; background:#ffffff;" />
  <div style="position:absolute; right:379.7px; top:239.2px; text-align:right; font-size:23.55px; line-height:1; white-space:nowrap;" dir="rtl">فاتورة ضريبية</div>
  <div style="position:absolute; right:379.7px; top:270.6px; text-align:right; font-size:20.1px; line-height:1; white-space:nowrap;" dir="rtl">فاتورة&nbsp; مبيعات</div>

  <!-- ============ META BOXES ============ -->
  {_meta_box(_LEFT_BOX, (
      ("Customer VatNo", "الرقم الضريبى للعميل :", customer_vat, 288.8),
      ("CR", "السجل التجارى للعميل :", customer_cr, 309.7),
      ("Customer Adress", "عنوان العميل :", customer_address, 322.8),
  ), centre=None)}
  {_meta_box(_RIGHT_BOX, (
      ("Issue Date", "تاريخ الإصدار :", issue_date, 670.8),
      ("INvoice No", "رقم الفاتورة :", invoice_number, 679.3),
      ("Payment Method", "طريقة الدفع :", payment, 680.3),
  ), centre=_RIGHT_VALUE_CX)}

  <!-- ============ CUSTOMER NAME BAR ============ -->
  <div style="position:absolute; left:40.4px; top:{432.3 + _BELOW_SHIFT}px; width:703.3px; height:34.9px; background:{_BAR}; border:1px solid {_HAIRLINE}; font-size:{_BAR_PX}px;">
    <!-- Offsets resolve against this bar's padding box (right edge 742.7px abs),
         not the sheet. -->
    <div style="position:absolute; left:2.3px; top:50%; transform:translateY(calc(-50% - 1.87px)); white-space:nowrap;">Customer</div>
    <div style="position:absolute; right:7.1px; top:50%; transform:translateY(calc(-50% - 1.87px)); white-space:nowrap;" dir="rtl">اسم العميل:</div>
    <!-- Right-anchored, not centred: the reference has a single sample, and an
         over-long name must grow leftwards into empty space rather than into
         the «اسم العميل:» label. Reproduces the reference exactly for its own. -->
    <div style="position:absolute; right:99.5px; top:50%; transform:translateY(calc(-50% - 1.87px)); text-align:right; white-space:nowrap;" dir="rtl">{customer_name}</div>
  </div>
"""

    # The totals grid closes the invoice, so it prints on the LAST sheet only —
    # whole. _ROW_BUDGET_LAST is what keeps that sheet's rows off it.
    band_html = f"""
    {_totals_grid((
        ("الإجمالى قبل الضريبة", "Total Vat.Excl", _money(subtotal)),
        ("الخصم", "Discount", _money(discount)),
        ("الصافى", "Net Amount", _money(net)),
        ("ضريبة القيمة المضافة", f"Vat ({vat_rate}%)", _money(vat_total)),
        ("الإجمالى النهائى", "Balance Duo", _money(grand_total)),
    ), html.escape(amount_in_words_ar(grand_total)))}
"""

    pages = paginate(
        lines, _row_height_v5, budget=_ROW_BUDGET, budget_last=_ROW_BUDGET_LAST,
        foot=_FOOT_H,
    )
    sheets = []
    first_index = 1
    for index, page_lines in enumerate(pages, start=1):
        last = index == len(pages)
        foot = page_foot(index, len(pages), sheet_h=_SHEET_H)
        sheets.append(f"""
<div data-sheet dir="ltr" style="position:relative; width:816px; height:{_SHEET_H}px; margin:24px auto; background:#fff; font-weight:700; overflow:hidden; {sheet_style(last=last)}">
{page_top}
  <!-- ============ ITEMS TABLE + TOTALS + FOOTER ============ -->
  <div style="position:absolute; left:0; top:{_TABLE_TOP}px; width:816px;">

    <table style="margin-left:{_TABLE_LEFT}px; width:{_TABLE_WIDTH}px; border-collapse:collapse; table-layout:fixed;">
      <colgroup>{"".join(f'<col style="width:{w}px;">' for _, _, w in _COLUMNS)}</colgroup>
      <thead>{_header_row()}</thead>
      <tbody>{_item_rows_v5(page_lines, first_index)}</tbody>
    </table>
{band_html if last else ""}
    {_footer()}
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
    font-family: {_ARIAL};
    color: #000;
    -webkit-font-smoothing: antialiased;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  .serif {{ font-family: {_SERIF}; }}
  .th {{
    border: 3px solid {_GRID}; padding: 1px 3px; text-align: center;
    font-weight: 700; line-height: 1.15;
  }}
  .td {{
    border: 3px solid {_GRID}; padding: 2px 3px {_BODY_PAD_BOTTOM}px;
    text-align: center;
  }}
  .tt {{ border: 1px solid {_GRID}; padding: 0; }}
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


__all__ = ["build_invoice_html_v5"]
