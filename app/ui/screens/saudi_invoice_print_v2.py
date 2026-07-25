"""Printable Saudi tax-invoice document — **second template** (النموذج الثاني).

This is an *additional* invoice layout, offered alongside the original
:mod:`app.ui.screens.saudi_invoice_print` template (النموذج الأول). It faithfully
reproduces the approved Claude-Design mock-up shipped under
``D:\\PHASEINV2\\النموذج الثاني`` (``Tax Invoice.dc.html``): a bilingual
letterhead banner with the establishment info + logo, a yellow-bordered header
frame carrying the customer/seller VAT numbers, a QR, the invoice meta, an items
table with black grid lines, and a totals block ending in the amount spelled out
in Arabic words.

Only :func:`build_invoice_html_v2` lives here — it consumes the very same
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
from decimal import Decimal
from typing import Any

# Reuse the shared asset/number/QR/amount-in-words helpers from النموذج الأول so
# both templates stay perfectly consistent and there is a single source of truth.
from app.ui.screens.saudi_invoice_print import (
    _asset_data_uri,
    _money,
    _qty,
    amount_in_words_ar,
    company_logo_data_uri,
    QR_PRINT_PX,
    qr_vector_data_uri,
)

# Design tokens fixed to the النموذج الثاني mock-up. All frame/table borders use
# the same yellow as the header frame (per user request — no black grid lines).
_YELLOW = "#FFD966"

# The items grid's six tracks, in the mock-up's proportions. `minmax(0, Nfr)` —
# never a bare `Nfr`, whose automatic minimum is min-content: one unbroken item
# name («ممممم…», no spaces to wrap at) then forced the الوصف track wider than the
# whole sheet and drove the row off the paper. With the floor at 0 the track keeps
# its share and the name wraps inside it instead (النموذج الخامس's rule).
_GRID_COLUMNS = "minmax(0,78fr) minmax(0,179fr) minmax(0,64fr) minmax(0,57fr) minmax(0,64fr) minmax(0,75fr)"

# UN/ECE unit code -> Arabic label shown in the "الوحدة / Unit" column.
_UNIT_LABELS = {
    "PCE": "حبة",
    "EA": "حبة",
    "KGM": "كجم",
    "MTR": "متر",
    "LTR": "لتر",
    "BX": "علبة",
    "CT": "كرتون",
}


def _unit_label(code: Any) -> str:
    text = str(code or "").strip()
    return _UNIT_LABELS.get(text.upper(), text)


def _vat_rate_display(subtotal: Any, vat_total: Any, lines: list[dict[str, Any]]) -> str:
    """Best-effort single VAT-rate label for the totals block (e.g. ``15``)."""
    # Prefer an explicit per-line rate when the invoice is single-rate.
    rates = {str(ln.get("vat_rate")) for ln in lines if ln.get("vat_rate") not in (None, "")}
    if len(rates) == 1:
        try:
            return f"{Decimal(next(iter(rates))):g}"
        except Exception:  # noqa: BLE001
            pass
    try:
        base = Decimal(str(subtotal))
        if base > 0:
            return f"{(Decimal(str(vat_total)) / base * 100).quantize(Decimal('1')):g}"
    except Exception:  # noqa: BLE001
        pass
    return "15"


def _item_rows_v2(lines: list[dict[str, Any]]) -> str:
    if not lines:
        return (
            f'<div style="display:grid; grid-template-columns:{_GRID_COLUMNS}; '
            f'border-bottom:1px solid {_YELLOW}; font-weight:700; font-size:14px;">'
            f'<div dir="rtl" style="grid-column:1 / -1; padding:18px 2px; text-align:center; color:#999;">'
            f'لا توجد أصناف</div></div>'
        )
    rows: list[str] = []
    for line in lines:
        no = html.escape(str(line.get("code") or ""))
        desc = html.escape(str(line.get("name") or ""))
        qty = _qty(line.get("qty"))
        unit = html.escape(_unit_label(line.get("unit") or line.get("unit_code")))
        price = _money(line.get("price"))
        # The row "total amount" is the taxable (pre-VAT) line total, matching the
        # mock-up where total == qty × price.
        total = _money(line.get("before"))
        rows.append(f"""
      <div class="keep" style="display:grid; grid-template-columns:{_GRID_COLUMNS}; border-bottom:1px solid {_YELLOW}; font-weight:700; font-size:14px;">
        <div style="padding:4px 2px; text-align:center; border-right:1px solid {_YELLOW};">{no}</div>
        <div dir="rtl" style="padding:4px 2px; text-align:center; border-right:1px solid {_YELLOW}; line-height:1.3; overflow-wrap:anywhere;">{desc}</div>
        <div style="padding:4px 2px; text-align:center; border-right:1px solid {_YELLOW};">{qty}</div>
        <div dir="rtl" style="padding:4px 2px; text-align:center; border-right:1px solid {_YELLOW};">{unit}</div>
        <div style="padding:4px 2px; text-align:center; border-right:1px solid {_YELLOW};">{price}</div>
        <div style="padding:4px 2px; text-align:center;">{total}</div>
      </div>""")
    return "".join(rows)


def build_invoice_html_v2(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML for النموذج الثاني from live data."""
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
    sale_type = html.escape(str(data.get("payment_label") or ""))

    seller = data.get("seller") or {}
    customer = data.get("customer") or {}
    totals = data.get("totals") or {}
    lines = data.get("lines") or []

    seller_name_ar = html.escape(str(seller.get("name") or ""))
    seller_name_en = html.escape(str(seller.get("name_en") or seller.get("name") or ""))
    seller_addr_ar = html.escape(str(seller.get("address") or ""))
    seller_addr_en = html.escape(str(seller.get("address_en") or seller.get("address") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))

    customer_name = html.escape(str(customer.get("name") or ""))
    customer_addr = html.escape(str(customer.get("address") or ""))
    customer_vat = html.escape(str(customer.get("vat") or ""))

    subtotal = totals.get("subtotal", 0)
    vat_total = totals.get("vat", 0)
    grand_total = totals.get("total", 0)
    discount = totals.get("discount", 0)
    vat_rate = _vat_rate_display(subtotal, vat_total, lines)
    words = html.escape(amount_in_words_ar(grand_total))

    logo_html = (
        f'<img src="{logo}" alt="logo" style="height:120px; max-width:230px; width:auto; flex:none; display:block; object-fit:contain;" />'
        if logo
        else '<div style="flex:none;"></div>'
    )

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
  /* This sheet FLOWS (min-height, not height), so a long invoice carries onto a
     second printed page by itself — nothing is ever clipped here. Two things
     were wrong with how it did that (user, 2026-07-23: «ما يجيش مقصوص»):
     the page break could fall *through* an item row or through the totals block,
     printing half of each on two pages; and page 2 began at the paper's very
     edge (measured 0.0mm), inside the ~4mm no office laser can print, so its
     first row came out shaved. `keep` handles the first. The `page` wrapper
     handles the second: the sheet's top and bottom padding move into a
     <thead>/<tfoot> spacer, which Chromium repeats on every printed page. Their
     font-size:0 keeps them exactly as tall as the padding they replace, so page
     1 prints ink-for-ink where it did. See النموذج الأول for the full note. */
  .keep {{ break-inside: avoid; page-break-inside: avoid; }}
  .page {{ width: 100%; table-layout: fixed; border-collapse: collapse; }}
  .page > tbody > tr > td {{ padding: 0; vertical-align: top; }}
  .page > thead > tr > td, .page > tfoot > tr > td {{ padding: 0; font-size: 0; line-height: 0; }}
  .page-top {{ height: 30px; }}
  .page-bottom {{ height: 40px; }}
  @media print {{
    body {{ background: #fff; }}
    [data-sheet] {{ box-shadow: none !important; margin: 0 !important; }}
  }}
</style>
</head>
<body>

<div data-sheet style="width:816px; min-height:1056px; margin:24px auto; background:#fff; padding:0 30px 0 78px; box-shadow:0 6px 30px rgba(0,0,0,.45); font-weight:700; line-height:1.2;">
<table class="page">
  <thead><tr><td class="page-top"></td></tr></thead>
  <tfoot><tr><td class="page-bottom"></td></tr></tfoot>
  <tbody><tr><td>

  <!-- ============ LETTERHEAD BANNER ============ -->
  <div style="display:flex; align-items:center; justify-content:space-between; gap:16px; padding:0 6px 10px;">
    <div style="font-size:13px; font-weight:700; line-height:1.95; white-space:nowrap;">
      <div>{seller_name_en}</div>
      <div>{seller_addr_en}</div>
      <div>Vat No.: {seller_vat}</div>
    </div>
    {logo_html}
    <div dir="rtl" style="font-size:13px; font-weight:700; line-height:1.95; text-align:right; white-space:nowrap;">
      <div>{seller_name_ar}</div>
      <div>{seller_addr_ar}</div>
      <div>الرقم الضريبي: {seller_vat}</div>
    </div>
  </div>

  <!-- ============ HEADER INFO FRAME ============ -->
  <div style="border:1.5px solid {_YELLOW}; padding:5px;">

    <!-- Band 1: Customer VAT | Title | Seller VAT -->
    <div style="display:flex; gap:5px; align-items:stretch;">
      <div style="flex:116; display:flex; align-items:stretch; border:1px solid {_YELLOW}; min-height:46px;">
        <div style="flex:none; width:58px; padding:4px 6px; font-size:10.5px; font-weight:700; line-height:1.25; display:flex; align-items:center;">Customer Vat No:</div>
        <div style="flex:1; padding:4px; font-size:12.5px; font-weight:700; display:flex; align-items:center; justify-content:center; text-align:center;">{customer_vat}</div>
        <div dir="rtl" style="flex:none; width:66px; padding:4px 6px; font-size:10.5px; font-weight:700; line-height:1.25; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">الرقم الضريبي للعميل</div>
      </div>
      <div style="flex:100; display:flex; align-items:center; justify-content:center; border:2px solid {_YELLOW}; padding:6px 4px;">
        <div dir="rtl" style="font-size:21px; font-weight:700; white-space:nowrap;">فاتورة ضريبية</div>
      </div>
      <div style="flex:100; display:flex; align-items:stretch; border:1px solid {_YELLOW}; min-height:46px;">
        <div style="flex:none; width:52px; padding:4px 6px; font-size:10.5px; font-weight:700; line-height:1.25; display:flex; align-items:center;">VAT Reg Cert.:</div>
        <div style="flex:1; padding:4px; font-size:12.5px; font-weight:700; display:flex; align-items:center; justify-content:center; text-align:center;">{seller_vat}</div>
        <div dir="rtl" style="flex:none; width:48px; padding:4px 6px; font-size:10.5px; font-weight:700; line-height:1.25; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">الرقم الضريبي</div>
      </div>
    </div>

    <!-- Band 2: QR + info rows -->
    <div style="display:flex; gap:5px; margin-top:5px; align-items:stretch;">
      <!-- Sized from the QR, not from the design's original 180px cell: the box is
           content-box, so it must be exactly QR_PRINT_PX wide or the code spills
           past the yellow border. The info column beside it is flex:1 and simply
           gives up the difference. -->
      <div style="flex:none; width:{QR_PRINT_PX}px; border:1px solid {_YELLOW}; padding:6px; display:flex; align-items:center; justify-content:center;">
        <img src="{qr}" alt="QR" style="width:{QR_PRINT_PX}px; height:{QR_PRINT_PX}px; display:block; background:#ffffff;" />
      </div>
      <div style="flex:1; display:flex; flex-direction:column; gap:5px;">
        <div style="display:flex; gap:5px; flex:1;">
          <div style="flex:132; display:flex; align-items:stretch; border:1px solid {_YELLOW};">
            <div style="flex:none; width:52px; padding:3px 6px; font-size:11px; font-weight:700; display:flex; align-items:center;">Inv :</div>
            <div style="flex:1; padding:3px; font-size:12.5px; font-weight:700; display:flex; align-items:center; justify-content:center;">{invoice_number}</div>
            <div dir="rtl" style="flex:none; width:74px; padding:3px 6px; font-size:11px; font-weight:700; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">رقم الفاتورة</div>
          </div>
          <div style="flex:118; display:flex; align-items:stretch; border:1px solid {_YELLOW};">
            <div style="flex:none; width:42px; padding:3px 4px; font-size:11px; font-weight:700; display:flex; align-items:center;">Date:.</div>
            <div style="flex:1; padding:3px; font-size:12px; font-weight:700; white-space:nowrap; display:flex; align-items:center; justify-content:center;">{issue_date}</div>
            <div dir="rtl" style="flex:none; width:44px; padding:3px 4px; font-size:11px; font-weight:700; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">التاريخ</div>
          </div>
        </div>
        <div style="display:flex; align-items:stretch; border:1px solid {_YELLOW}; flex:1;">
          <div style="flex:none; width:98px; padding:3px 6px; font-size:11px; font-weight:700; display:flex; align-items:center;">Customer Name:.</div>
          <div dir="rtl" style="flex:1; padding:3px; font-size:12.5px; font-weight:700; display:flex; align-items:center; justify-content:center; text-align:center;">{customer_name}</div>
          <div dir="rtl" style="flex:none; width:66px; padding:3px 6px; font-size:11px; font-weight:700; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">اسم العميل</div>
        </div>
        <div style="display:flex; gap:5px; flex:1;">
          <div style="flex:100; display:flex; align-items:stretch; border:1px solid {_YELLOW};">
            <div style="flex:none; width:66px; padding:3px 6px; font-size:11px; font-weight:700; display:flex; align-items:center;">Salles Type</div>
            <div dir="rtl" style="flex:1; padding:3px; font-size:12.5px; font-weight:700; display:flex; align-items:center; justify-content:center;">{sale_type}</div>
            <div dir="rtl" style="flex:none; width:56px; padding:3px 6px; font-size:11px; font-weight:700; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">نوع البيع</div>
          </div>
          <div style="flex:132; display:flex; align-items:stretch; border:1px solid {_YELLOW};">
            <div dir="rtl" style="flex:1; padding:3px 6px; font-size:11px; font-weight:700; white-space:nowrap; display:flex; align-items:center; justify-content:center; text-align:center;">{customer_addr}</div>
            <div dir="rtl" style="flex:none; width:52px; padding:3px 6px; font-size:11px; font-weight:700; text-align:right; border-left:1px solid {_YELLOW}; display:flex; align-items:center; justify-content:flex-end;">العنوان</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ============ ITEMS TABLE ============ -->
  <div style="margin-top:9px; border:1.5px solid {_YELLOW};">
    <div style="display:grid; grid-template-columns:{_GRID_COLUMNS}; border-bottom:1.5px solid {_YELLOW}; font-weight:700; font-size:12px; line-height:1.2;">
      <div style="padding:5px 2px; text-align:center; border-right:1px solid {_YELLOW};"><div dir="rtl">رقم الصنف</div><div>Item No</div></div>
      <div style="padding:5px 2px; text-align:center; border-right:1px solid {_YELLOW};"><div dir="rtl">البيان</div><div>Description</div></div>
      <div style="padding:5px 2px; text-align:center; border-right:1px solid {_YELLOW};"><div dir="rtl">الكميه</div><div>Quantity</div></div>
      <div style="padding:5px 2px; text-align:center; border-right:1px solid {_YELLOW};"><div dir="rtl">الوحدة</div><div>Unit</div></div>
      <div style="padding:5px 2px; text-align:center; border-right:1px solid {_YELLOW};"><div dir="rtl">السعر</div><div>Price</div></div>
      <div style="padding:5px 2px; text-align:center;"><div dir="rtl">المبلغ الاجمالي</div><div>Total Amount</div></div>
    </div>{_item_rows_v2(lines)}
  </div>

  <!-- ============ TOTALS ============ -->
  <div class="keep" style="display:flex; margin-top:9px; align-items:stretch; border:1px solid {_YELLOW};">
    <div style="flex:265; display:flex; align-items:flex-end; justify-content:center; padding:10px 8px; border-right:1px solid {_YELLOW};">
      <span dir="rtl" style="font-size:11px; font-weight:700; text-align:center; line-height:1.4; white-space:nowrap;">{words}</span>
    </div>
    <div style="flex:251; display:flex; flex-direction:column;">
      <div style="display:flex; align-items:stretch; border-bottom:1px solid {_YELLOW}; min-height:24px;">
        <div style="flex:158; display:flex; align-items:center; padding:4px 7px; border-right:1px solid {_YELLOW}; font-size:11.5px; font-weight:700;"><span style="white-space:nowrap;">Total</span><span style="flex:1;"></span><span dir="rtl" style="white-space:nowrap;">الإجمالى</span></div>
        <div style="flex:93; display:flex; align-items:center; justify-content:center; padding:4px; font-size:11.5px; font-weight:700;">{_money(subtotal)}</div>
      </div>
      <div style="display:flex; align-items:stretch; border-bottom:1px solid {_YELLOW}; min-height:24px;">
        <div style="flex:158; display:flex; align-items:center; padding:4px 7px; border-right:1px solid {_YELLOW}; font-size:11.5px; font-weight:700;"><span style="white-space:nowrap;">Discount</span><span style="flex:1;"></span><span dir="rtl" style="white-space:nowrap;">الخصم</span></div>
        <div style="flex:93; display:flex; align-items:center; justify-content:center; padding:4px; font-size:11.5px; font-weight:700;">{_money(discount)}</div>
      </div>
      <div style="display:flex; align-items:stretch; border-bottom:1px solid {_YELLOW}; min-height:24px;">
        <div style="flex:158; display:flex; align-items:center; padding:4px 6px; border-right:1px solid {_YELLOW}; font-size:10.5px; font-weight:700;"><span style="white-space:nowrap;">Vat</span><span style="flex:1;"></span><span style="margin-inline-end:6px; white-space:nowrap;">{vat_rate}%</span><span dir="rtl" style="white-space:nowrap;">ضريبة القيمة المضافة</span></div>
        <div style="flex:93; display:flex; align-items:center; justify-content:center; padding:4px; font-size:11.5px; font-weight:700;">{_money(vat_total)}</div>
      </div>
      <div style="display:flex; align-items:stretch; min-height:24px;">
        <div style="flex:158; display:flex; align-items:center; padding:4px 7px; border-right:1px solid {_YELLOW}; font-size:11.5px; font-weight:700;"><span style="white-space:nowrap;">Net Total</span><span style="flex:1;"></span><span dir="rtl" style="white-space:nowrap;">الصافى</span></div>
        <div style="flex:93; display:flex; align-items:center; justify-content:center; padding:4px; font-size:11.5px; font-weight:700;">{_money(grand_total)}</div>
      </div>
    </div>
  </div>

  </td></tr></tbody>
</table>
</div>
</body>
</html>"""


__all__ = ["build_invoice_html_v2"]
