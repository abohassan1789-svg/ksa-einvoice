"""Printable Saudi tax-invoice document — **sixth template** (النموذج السادس).

An *additional* invoice layout offered alongside النماذج الأول → الخامس. It ports
the Claude Design handoff bundle at ``D:\\PHASEINV2\\نموذج 6`` (primary design file
``untitled/project/Tax Invoice.dc.html``): a portrait sheet with a bilingual
«Tax Invoice / فاتورة ضريبية» heading over a three-row info table, the logo top
right, dashed rules separating three bands, side-by-side «Seller / Customer»
tables, a six-column bilingual items table, then the QR block bottom-left and a
five-row totals table bottom-right.

Unlike النماذج الثالث/الرابع/الخامس — measured off reference **PDFs** — this
design's medium is HTML/CSS, so every coordinate below is the design's own,
copied from its source rather than recovered by probing. The design already
targets the same 816 × 1056 px sheet the other templates use, so the existing
preview dialog and PDF exporter render it unchanged.

The sheet is **LTR-structured** (as النموذج الأول and الخامس are); Arabic runs
get ``dir="rtl"`` per element.

⚠️ Fonts are set through the ``<style>`` block, never through an inline
``style="… font-family:…"``. The font stacks contain double quotes, which close
an HTML attribute early and silently drop every declaration that follows —
النموذج الرابع still does this and loses its own ``font-size`` as a result.

Long invoices paginate (user, 2026-07-23): the sheet holds five item rows above
the QR/totals block — the design's own capacity, at its own 56px rows — and
beyond that the rows run onto further sheets, with the block printed whole on
the last one. Rows used to be compressed towards 18px instead, which clipped
their text and still ran out. See :mod:`app.ui.screens.invoice_pagination`.

Only :func:`build_invoice_html_v6` lives here — it consumes the very same
``data`` dict produced by
:meth:`SaudiSalesInvoicePage._collect_preview_data`.

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
    company_logo_data_uri,
    QR_PRINT_PX,
    qr_vector_data_uri,
)
from app.ui.screens.saudi_invoice_print_v3 import _dec, _rate

# ---------------------------------------------------------------------------
# Design tokens — the design's own values
# ---------------------------------------------------------------------------
_ACCENT = "#1A2A6C"  # every rule, and the items-table header text
_TITLE = "#0A1864"   # the «Tax Invoice / فاتورة ضريبية» heading
_FONTS = 'Arial, Tahoma, "Helvetica Neue", Helvetica, sans-serif'

# The design hard-codes both parties' country.
_COUNTRY = "المملكة العربية السعودية"

# Column widths (px), left-to-right, exactly as the design lays them out.
_INFO_COLS = (154, 135, 144, 98)
_SELLER_COLS = (106, 202, 67)
# The design gave the customer table a 58px English column against the seller's
# 106px, so the same labels sat at different offsets in the two tables. Both now
# share the label and Arabic widths and the value column absorbs the difference,
# which keeps each table's own total width unchanged.
_CUSTOMER_COLS = (106, 183, 67)
_ITEM_COLS = (269, 67, 106, 77, 96, 125)
_TOTALS_COLS = (192, 135, 193)

# Party-table row heights (px): Name, Address, Country, C.R., VAT. The design's
# «Pincode / الرمز السري» row is dropped at the user's request (2026-07-16); the
# field it printed was always blank.
_PARTY_ROW_H = (38, 29, 24, 24, 24)

# The QR panel and the totals table sit side by side and share their top/bottom
# borders, so their heights must stay equal. The panel is sized FROM the QR — a
# 216px code cannot fit the design's original 213px panel at all — and the totals
# rows are then scaled by the same factor so the two edges still coincide and the
# rows keep their relative proportions.
_QR_PAD_TOP = 30        # clears the "Details Other" caption
_QR_PAD_BOTTOM = 15
_QR_PANEL_H = _QR_PAD_TOP + QR_PRINT_PX + _QR_PAD_BOTTOM        # 261 at 216px
_QR_PANEL_W = 221       # fixed: its right border must land on the totals' left one

_TOTALS_ROW_H_BASE = (41, 38, 57, 38, 38)                       # design's own
_TOTALS_SCALE = (_QR_PANEL_H - 1) / sum(_TOTALS_ROW_H_BASE)     # -1 = border-top
_TOTALS_ROW_H = tuple(round(h * _TOTALS_SCALE, 1) for h in _TOTALS_ROW_H_BASE)

# Flow geometry below the items table, derived from the design's absolutes:
# table top 466 (+1px border +40 header +56 row = bottom 563); divider 562;
# totals 568; QR 569.
_FLOW_TOP = 466
_DIVIDER3_PULL = -1.0   # 562 - 563
_TOTALS_GAP = 4.5       # 568 - (562 + 1.5)

# --- pagination (user, 2026-07-23) -----------------------------------------
# The sheet is a fixed 816 × 1056 with overflow:hidden, so whatever does not fit
# is silently cut off — and what this design puts last is the QR and the totals.
# Everything below the items table (divider, gap, QR/totals block) needs 266px
# and the table's own head 41px, which leaves this much for rows:
_SHEET_H = 1056
_HEAD_H = 1 + 40
_BAND_H = (_DIVIDER3_PULL + 1.5) + _TOTALS_GAP + _QR_PANEL_H     # 266
_FOOT_H = FOOT_H     # the «صفحة x من y» strip, charged only when x/y > 1
_ROW_BUDGET = _SHEET_H - _FLOW_TOP - _HEAD_H                     # 549 -> 9 rows
_ROW_BUDGET_LAST = _ROW_BUDGET - _BAND_H                         # 283 -> 5 rows

_ITEM_ROW_H = 56        # the design's own row height, kept on every page

# Rows used to be *compressed* instead — down to 18px, at which point the text
# clipped anyway and a 20-item invoice was unreadable. Nothing is compressed
# now: five items share the page with the totals exactly as the design draws
# them, and a longer invoice runs onto more sheets (النموذج السادس was the one
# whose totals fell off the page first, which is why this was written).
_ITEM_ROW_MIN = _ITEM_ROW_H     # kept as a name: rows no longer shrink at all

# Name-column metrics for the wrapped-row estimate: the cell's 269px less its
# 6px padding each side, its 13px/1.2 text, and the row's 1px bottom rule.
_NAME_W = _ITEM_COLS[0] - 12
_NAME_FONT = 13
_NAME_LINE_H = 1.2
_ROW_CHROME = 1.0


def _row_height_v6(line: dict[str, Any]) -> float:
    """How tall this line's row will really be — see :mod:`invoice_pagination`."""
    return row_height(
        str(line.get("name") or ""),
        base=_ITEM_ROW_H,
        width_px=_NAME_W,
        font_px=_NAME_FONT,
        line_height=_NAME_LINE_H,
        chrome=_ROW_CHROME,
    )


# Every cell pins its line-height. The design leaves it `normal`, which is safe
# only because it also pins each row's `height` and lets any overflow be clamped.
# We use `min-height` instead (so a real address or product name wraps and grows
# the row rather than spilling across the rules), and under `normal` the Arabic
# fallback font's line box — nearly 1.9em — silently inflated every row by ~5px.
# Pinning it keeps the design's geometry exactly while preserving the growth.
_LINE_H = "1.2"


def _cell(content: str, width: int, *, align: str = "center", size: int = 13,
          rtl: bool = False, pad: str = "0 5px", color: str = "",
          line_height: str = _LINE_H, nowrap: bool = False,
          wrap_anywhere: bool = False) -> str:
    """One table cell — the design's shape: fixed width, right+bottom rule.

    ``nowrap`` mirrors the design's own ``white-space: nowrap``, which it pins on
    every fixed label so none of them ever breaks across two lines. Values are
    left wrappable so that a real one, longer than the design's sample, wraps at
    a space and grows its row.

    ``wrap_anywhere`` is for the one value that has no space to wrap at: an item
    name typed as a single unbroken token. Two things are needed and neither works
    without the other —

    * ``min-width:0`` — a flex item's automatic minimum is ``min-content``, so
      ``flex:0 0 {width}px`` is **not** a width ceiling: the token pushed the cell
      past its declared px and shoved the whole row sideways;
    * ``overflow-wrap:anywhere`` rather than ``break-word`` — only ``anywhere``
      also *reduces min-content*, which is what the flex minimum above is measured
      against. ``break-word`` alone breaks the line but leaves the cell wide.
    """
    attrs = ' dir="rtl" lang="ar"' if rtl else ""
    extra = f" color:{color};" if color else ""
    extra += " white-space:nowrap;" if nowrap else ""
    extra += " min-width:0; overflow-wrap:anywhere;" if wrap_anywhere else ""
    return (
        f'<div{attrs} style="flex:0 0 {width}px; border-right:1px solid {_ACCENT}; '
        f'border-bottom:1px solid {_ACCENT}; display:flex; flex-direction:column; '
        f'justify-content:center; text-align:{align}; font-weight:700; '
        f'font-size:{size}px; line-height:{line_height}; padding:{pad};{extra}">'
        f"{content}</div>"
    )


def _row(cells: str, height: int) -> str:
    """A table row — ``min-height`` so over-long values grow it (see _LINE_H)."""
    return f'<div style="display:flex; min-height:{height}px;">{cells}</div>'


def _table(rows: str, *, left: int, top: int, width: int) -> str:
    return (
        f'<div style="position:absolute; left:{left}px; top:{top}px; '
        f'width:{width}px; border-top:1px solid {_ACCENT}; '
        f'border-left:1px solid {_ACCENT};">{rows}</div>'
    )


def _divider(left: int, top: int, width: int) -> str:
    return (
        f'<div style="position:absolute; left:{left}px; top:{top}px; '
        f'width:{width}px; border-top:1.5px dashed {_ACCENT};"></div>'
    )


def _party_table(rows: "tuple[tuple[str, str, str], ...]", cols: "tuple[int, int, int]",
                 *, left: int, top: int, width: int, label_pad: str) -> str:
    """A Seller / Customer table: English label | value | Arabic label."""
    out = []
    for index, (english, value, arabic) in enumerate(rows):
        out.append(_row(
            _cell(english, cols[0], align="left", pad=label_pad, nowrap=True)
            + _cell(value, cols[1], align="center", rtl=True)
            + _cell(arabic, cols[2], align="right", rtl=True, nowrap=True),
            _PARTY_ROW_H[index],
        ))
    return _table("".join(out), left=left, top=top, width=width)


def _items_header() -> str:
    labels = (
        ("Name of Products", "اسم المنتج"),
        ("Quantity", "الكميه"),
        ("Rate", "السعر"),
        ("Tax Rate", "نسبة الضريبة"),
        ("VAT Amount", "مبلغ الضريبة"),
        ("Amount", "المبلغ"),
    )
    cells = "".join(
        _cell(
            f'{en}<br /><span dir="rtl" lang="ar">{ar}</span>',
            _ITEM_COLS[i], pad="0 3px", color=_ACCENT, line_height="1.2",
        )
        for i, (en, ar) in enumerate(labels)
    )
    return _row(cells, 40)


def _item_rows_v6(lines: list[dict[str, Any]]) -> str:
    height = _ITEM_ROW_H
    rows = []
    for line in lines:
        values = (
            html.escape(str(line.get("name") or "")),
            _qty(line.get("qty")),
            _money(line.get("price")),
            _rate(line.get("vat_rate")),
            # The design's own sample row prints «8,550.0» and «65550» — unformatted
            # slips, contradicted by its own totals table («8,550.00», «65,550.00»).
            # Money goes through _money here, like every other template.
            _money(line.get("vat_amount")),
            _money(line.get("total")),
        )
        cells = _cell(values[0], _ITEM_COLS[0], rtl=True, pad="0 6px", wrap_anywhere=True)
        cells += "".join(
            _cell(v, _ITEM_COLS[i + 1], pad="0 3px", nowrap=True)
            for i, v in enumerate(values[1:])
        )
        rows.append(_row(cells, height))
    return "".join(rows)


def _totals_table(rows: "tuple[tuple[str, str, str], ...]") -> str:
    out = []
    for index, (english, arabic, value) in enumerate(rows):
        out.append(_row(
            _cell(english, _TOTALS_COLS[0], size=14, pad="0 6px", line_height="1.25")
            + _cell(arabic, _TOTALS_COLS[1], size=14, rtl=True, pad="0 6px",
                    line_height="1.25")
            + _cell(value, _TOTALS_COLS[2], size=14, align="right", pad="0 8px",
                    nowrap=True),
            _TOTALS_ROW_H[index],
        ))
    return (
        f'<div style="position:absolute; left:256px; top:0; width:521px; '
        f'border-top:1px solid {_ACCENT}; border-left:1px solid {_ACCENT};">'
        f'{"".join(out)}</div>'
    )


def build_invoice_html_v6(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML for النموذج السادس from live data."""
    logo = company_logo_data_uri(data.get("seller")) or _asset_data_uri("logo.png")
    qr = qr_vector_data_uri(data.get("qr_payload"))
    seller_label = _asset_data_uri("seller-label.png")
    customer_label = _asset_data_uri("customer-label.png")

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
    # No «discount» key is produced by _collect_preview_data today, so this
    # prints 0.00 and the taxable total equals the total — the same open point
    # the second, third and fifth templates carry (see the handoff, section 8.5).
    discount = _dec(totals.get("discount"))
    vat_total = _dec(totals.get("vat"))
    grand_total = _dec(totals.get("total"))
    taxable = subtotal - discount

    logo_html = (
        f'<img src="{logo}" alt="logo" style="position:absolute; left:584px; '
        f'top:45px; width:192px; height:134px; object-fit:contain;" />'
        if logo
        else ""
    )

    # The design shows the same value in both middle columns («Inv-10120 |
    # Inv-10120»), which its own source document does too — kept at the user's
    # explicit confirmation rather than merged.
    info_rows = (
        ("Invoice Number", invoice_number, invoice_number, "رقم الفاتورة"),
        ("Date Invoice", issue_date, issue_date, "تاريخ الفاتورة"),
        # The design's third row, «Date of Supply / تاريخ التوريد», is dropped at
        # the user's request (2026-07-16): `data` carries no supply date, so it
        # only ever printed blank.
    )
    info_html = "".join(
        _row(
            _cell(en, _INFO_COLS[0], size=15, pad="0 4px", nowrap=True)
            + _cell(v1, _INFO_COLS[1], size=15, pad="0 4px", nowrap=True)
            + _cell(v2, _INFO_COLS[2], size=15, pad="0 4px", nowrap=True)
            + _cell(ar, _INFO_COLS[3], size=15, rtl=True, pad="0 4px", nowrap=True),
            29,
        )
        for en, v1, v2, ar in info_rows
    )

    def party_rows(party: dict[str, Any]) -> "tuple[tuple[str, str, str], ...]":
        return (
            ("Name", html.escape(str(party.get("name") or "")), "اسم"),
            ("Address", html.escape(str(party.get("address") or "")), "العنوان"),
            ("Country", _COUNTRY, "الدولة"),
            ("C.R.&nbsp;No.", html.escape(str(party.get("cr") or "")), "سجل تجارية"),
            ("VAT", html.escape(str(party.get("vat") or "")), "رقم الضريبة"),
        )

    # ---- the page furniture, identical on every sheet ----------------------
    # A continuation page is a full invoice page — heading, info table, both
    # party tables — not a bare items list, so every sheet of a multi-page
    # invoice stands on its own.
    page_top = f"""
  <!-- ============ HEADING ============ -->
  <div style="position:absolute; left:36px; top:43px; width:530px; height:40px; display:flex; align-items:center; justify-content:space-between;">
    <span style="font-weight:700; font-size:27px; letter-spacing:0.3px; color:{_TITLE};">Tax Invoice</span>
    <span dir="rtl" lang="ar" style="font-weight:700; font-size:27px; color:{_TITLE};">فاتورة ضريبية</span>
  </div>

  <!-- ============ LOGO ============ -->
  {logo_html}

  <!-- ============ INFO TABLE ============ -->
  {_table(info_html, left=35, top=92, width=532)}

  <!-- ============ DIVIDER 1 ============ -->
  {_divider(37, 203, 746)}

  <!-- ============ SELLER / CUSTOMER LABELS ============ -->
  <img src="{seller_label}" alt="Seller" style="position:absolute; left:36px; top:208px; height:38px;" />
  <img src="{customer_label}" alt="Customer" style="position:absolute; left:421px; top:215px; height:24px;" />

  <!-- ============ SELLER TABLE ============ -->
  {_party_table(party_rows(seller), _SELLER_COLS, left=35, top=255, width=376,
                label_pad="0 5px")}

  <!-- ============ CUSTOMER TABLE ============ -->
  {_party_table(party_rows(customer), _CUSTOMER_COLS, left=420, top=255, width=357,
                label_pad="0 5px")}

  <!-- ============ DIVIDER 2 ============ -->
  {_divider(37, 447, 746)}
"""

    # DIVIDER 3 + QR + TOTALS: the invoice's result, so it prints on the LAST
    # sheet only — whole. _ROW_BUDGET_LAST is what reserves its room there.
    band_html = f"""
    <div style="margin-left:37px; margin-top:{_DIVIDER3_PULL}px; width:738px; border-top:1.5px dashed {_ACCENT};"></div>

    <div style="position:relative; margin-top:{_TOTALS_GAP}px; height:{_QR_PANEL_H}px;">
      <!-- The QR panel is boxed at the user's request (2026-07-15); the design
           leaves it open. It is 221px — not 220 — so its right border lands on
           256..257, exactly where the totals table draws its own border-left, and
           the shared edge stays one 1px rule instead of two touching ones. Its
           top/bottom borders coincide with the totals block's for the same reason.
           The QR moves 55 -> 54 because absolutely positioned children resolve
           against the *padding* box, which the new 1px border shifts right by 1:
           the QR must stay at sheet x=91, which is what matches the design. -->
      <div style="position:absolute; left:36px; top:0; width:{_QR_PANEL_W}px; height:{_QR_PANEL_H}px; border:1px solid {_ACCENT};">
        <div style="position:absolute; top:8px; right:2px; font-weight:700; font-size:11px; white-space:nowrap;">Details Other / <span dir="rtl" lang="ar">تفاصيل أخرى</span></div>
        <!-- Centred horizontally in the panel; the panel's height is derived from
             the QR (see _QR_PANEL_H) rather than the other way round, because the
             code no longer fits the design's original 213px box. The panel width
             is pinned at 221 so its right border keeps meeting the totals table's
             left one, which leaves only a couple of px each side — visually fine,
             since the QR carries its own 4-module white quiet zone inside. -->
        <img src="{qr}" alt="QR" style="position:absolute; left:{(_QR_PANEL_W - 2 - QR_PRINT_PX) / 2}px; top:{_QR_PAD_TOP}px; width:{QR_PRINT_PX}px; height:{QR_PRINT_PX}px; background:#ffffff;" />
      </div>
      {_totals_table((
          ("Total (Excluding VAT)", "الإجمالي ( غير شاملة ضريبة القيمة المضافة )", _money(subtotal)),
          ("Discount", "مجموع الخصومات", _money(discount)),
          ("Total Taxable Amount<br />(Excluding Tax)", "الإجمالي الخاضع للضريبية ( غير شاملة ضريبة القيمة المضافة )", _money(taxable)),
          ("Total VAT", "مجموع ضريبة القيمة المضافة", _money(vat_total)),
          ("Total Amount Due", "إجمالي المبلغ المستحق", _money(grand_total)),
      ))}
    </div>"""

    pages = paginate(
        lines, _row_height_v6, budget=_ROW_BUDGET, budget_last=_ROW_BUDGET_LAST,
        foot=_FOOT_H,
    )
    sheets = []
    for index, page_lines in enumerate(pages, start=1):
        last = index == len(pages)
        foot = page_foot(index, len(pages), sheet_h=_SHEET_H, colour=_ACCENT)
        sheets.append(f"""
<div data-sheet dir="ltr" style="position:relative; width:816px; height:{_SHEET_H}px; margin:24px auto; background:#ffffff; box-shadow:0 2px 14px rgba(0,0,0,0.28); overflow:hidden; color:#000; {sheet_style(last=last)}">
{page_top}
  <!-- ============ ITEMS + DIVIDER 3 + QR + TOTALS ============ -->
  <!-- In flow, so extra item rows push the rules and totals down instead of
       being overrun by them. With the design's single row this is identical. -->
  <div style="position:absolute; left:0; top:{_FLOW_TOP}px; width:816px;">

    <div style="margin-left:36px; width:741px; border-top:1px solid {_ACCENT}; border-left:1px solid {_ACCENT};">
      {_items_header()}
      {_item_rows_v6(page_lines)}
    </div>
{band_html if last else ""}
  </div>
{foot}
</div>""")

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


__all__ = ["build_invoice_html_v6"]
