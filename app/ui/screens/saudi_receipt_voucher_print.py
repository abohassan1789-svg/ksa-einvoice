"""Printable cash-receipt voucher (سند قبض / ايصال إستلام نقديه) — preview + PDF.

Recreates, pixel-for-pixel, the approved Claude-Design mock-up
(``استنساخ التصميم الموجود`` / ``سند قبض.dc.html``): an A4 sheet with a
top-right seller header (name + CR + VAT + address), a boxed bilingual title, a
red serial number, and a bordered frame holding the amount box, the ``تحرير في``
date row, the ``استلمنا من السيد`` payer row, the amount-in-words row and the
``وذلك قيمة`` purpose row.

Every layout used to close with two captioned signature blocks (and النموذج
الرابع with a «ختم الشركة» ring beside them). All six dropped them at the user's
request (2026-07-17) for a single «التوقيع: .........» line at the bottom-left,
built by the shared :func:`~app.ui.screens.saudi_invoice_print.signature_line_html`
so the wording cannot drift between layouts.

Like the tax-invoice printout, the mock-up is HTML/CSS (heavy on absolute
positioning), so the most faithful reproduction is to render the very same
markup in a ``QWebEngineView`` and let Chromium print it to PDF. This module owns:

* :func:`build_receipt_voucher_html` — the template, populated from live data;
* :class:`SaudiReceiptVoucherPreviewDialog` — the on-screen preview (with its own
  export / print / close buttons);
* :func:`export_receipt_voucher_to_pdf` — a direct "save as PDF" helper.

It is presentation only: it never touches the database. The Arabic
amount-in-words reuses :func:`app.ui.screens.saudi_invoice_print.amount_in_words_ar`
so both documents spell numbers identically.
"""

from __future__ import annotations

import datetime
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
    amount_in_words_ar,
    company_logo_data_uri,
    signature_line_html,
)

# The design tokens are fixed to the approved mock-up's defaults.
INK = "#242732"
HILITE = "#bdd7ee"  # pale-blue label fill
DASH = "#262626"

# Design tokens for النموذج الثاني (the modern green-accented layout).
V2_GREEN = "#607C61"       # header bar + amount-box border (system accent)
V2_GREEN_SOFT = "#EAF3DE"  # pale-green badge / label fill
V2_GREEN_TINT = "#f6faf2"  # amount-box background wash
V2_GREEN_DK = "#173404"    # darkest green — values on soft-green fills
V2_GREEN_MD = "#3B6D11"    # mid green — labels on soft-green fills
V2_HEADER_SUB = "#dce7dc"  # muted text on the green header bar

# Design tokens for النموذج الثالث (bilingual formal bordered document).
V3_TEAL = "#12665e"        # accent — outer frame, dividers, label fills
V3_TEAL_SOFT = "#e7f1ef"   # pale-teal label-cell background
V3_INK = "#20272e"         # primary text / title box border
V3_MUTED = "#5b6570"       # secondary header lines
V3_LINE = "#cfe0dd"        # hairline borders between cells

# Design tokens for النموذج الرابع (the banking / cheque-style secure voucher).
V4_NAVY = "#14304a"        # frame, security strip, amount panel, dotted rules
V4_NAVY_SOFT = "#e8eef3"   # security-strip gaps + the currency cell
V4_WASH = "#f4f8fb"        # amount-panel background
V4_RED = "#c0392b"         # the serial block — the one accent, as on a cheque
V4_INK = "#1c2b38"         # values written on the dotted lines
V4_MUTED = "#4a5560"       # the header's CR / VAT / address lines

# Design tokens for النموذج الخامس (the symmetric bilingual letterhead).
V5_INK = "#1c2b38"         # rules, title box, amount box, primary text
V5_MUTED = "#5b6570"       # the CR / VAT / address lines and the row labels
V5_RED = "#c0392b"         # the serial — the one accent on the page
V5_RULE = "#cfcfcf"        # the grid's outer border + the logo placeholder ring
V5_LINE = "#e2e2e2"        # hairlines between grid rows
V5_FILL = "#f1efe8"        # label-cell fill
V5_SIGN = "#9aa3ac"        # the footer note under the grid

# Design tokens for النموذج السادس (the side band + tear-off stub).
V6_GREEN = "#0F6E56"       # the band, the amount, the stub's amount
V6_GREEN_SOFT = "#9FE1CB"  # the band's «RECEIPT VOUCHER» caption
V6_INK = "#2C2C2A"         # values on the ledger lines
V6_MUTED = "#5F5E5A"       # labels / header meta
V6_LINE = "#ECEAE3"        # the ledger underlines
V6_SIGN = "#B4B2A9"        # the stub's perforation

# Design tokens for النموذج السابع (monochrome, double-framed) — B/W per user
# request 2026-07-23: white field backgrounds, clear dark text, no colour accent.
V7_GOLD = "#222222"        # the double frame + dividers + amount-box border (was gold)
V7_GOLD_DK = "#333333"     # the bilingual title + field labels (clear dark)
V7_INK = "#222222"         # primary values — the clear dark («الأسمر الواضح»)
V7_MUTED = "#5a5a5a"       # the CR / VAT / address meta lines
V7_WASH = "#ffffff"        # the amount box is plain white now (was a pale-gold wash)

# Design tokens for النموذج الثامن (monochrome split side panel) — B/W 2026-07-23.
V8_PANEL = "#ffffff"       # the side panel background — white now (was solid indigo)
V8_PANEL_SOFT = "#666666"  # panel captions / row labels (grey, was light indigo)
V8_PANEL_CAP = "#888888"   # the panel's English caption (grey, was light indigo)
V8_INK = "#222222"         # primary text — the clear dark
V8_MUTED = "#5a5a5a"       # company meta lines
V8_LINE = "#e5e5e5"        # the row separators

# Design tokens for النموذج التاسع (classic monochrome ledger) — white paper 2026-07-23.
V9_INK = "#1f1f1f"         # text + the double rules
V9_LINE = "#222222"        # the table borders
V9_CREAM = "#ffffff"       # white paper (was a warm cream)
V9_FILL = "#f2f2f2"        # the label-cell fill — a neutral light grey (was a tan tint)
V9_MUTED = "#444444"       # secondary meta


# ======================================================================
# Formatting helpers
# ======================================================================
def _money(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:  # noqa: BLE001 - defensive formatting
        return "0.00"


def _date(value: Any) -> str:
    """Format the ``تحرير في`` date as ``dd-mm-yyyy`` (matches the mock-up)."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%d-%m-%Y")
    return html.escape(str(value or ""))


# ======================================================================
# HTML template
# ======================================================================
def build_receipt_voucher_html(data: dict[str, Any]) -> str:
    """Build the full printable receipt-voucher HTML from a live-data ``data`` dict.

    Expected keys: ``number`` (serial / invoice number), ``issue_date``,
    ``amount``, ``received_from``, ``purpose`` and a ``seller`` mapping with
    ``name`` / ``cr`` / ``vat`` / ``address``.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    # Company logo (top-left, opposite the seller header) when the company has one.
    logo = company_logo_data_uri(seller)
    logo_block = (
        f'<div style="position:absolute;top:20px;left:29px;">'
        f'<img src="{logo}" alt="logo" style="max-height:120px;max-width:230px;width:auto;height:auto;display:block;object-fit:contain;" />'
        f'</div>'
        if logo
        else ""
    )

    # Left-aligned with the amount box above it (both at left:18px), low in the
    # 357px-tall frame. Absolute like everything else on this sheet.
    signature = signature_line_html(
        font_size="15px", color="#000",
        extra_style="position:absolute;left:18px;top:300px;",
    )

    # NOTE: the sheet mirrors the approved mock-up exactly — a fixed 794×1123 A4
    # page whose children are absolutely positioned. Arabic shaping/alignment is
    # handled per-element via ``direction:rtl`` — never a page-level RTL that
    # would mirror the whole layout.
    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; height: 1123px; position: relative; background: #ffffff; font-family: 'Noto Sans Arabic', 'Tahoma', 'Arial', sans-serif; color: #000000; overflow: hidden; box-sizing: border-box; margin: 0 auto; box-shadow: 0 1px 6px rgba(0,0,0,0.12); }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    /* On screen the sheet is a full A4 page (1123px). For print, all content
       lives in the top ~592px, so shrink the sheet to it: a fixed sheet exactly
       one A4 tall rounds up into a trailing blank 2nd page — this keeps it to a
       single page. overflow:hidden already clips the unused lower area. */
    .sheet {{ margin: 0; box-shadow: none; height: 600px; page-break-inside: avoid; break-inside: avoid; }}
  }}
</style>
</head>
<body>
  <div class="sheet">
    {logo_block}

    <!-- ===== HEADER (top-right) ===== -->
    <div style="position:absolute;top:26px;right:29px;text-align:right;direction:rtl;line-height:1">
      <div style="font-size:19px;font-weight:800;line-height:26px;white-space:nowrap">{seller_name}</div>
      <div style="font-size:17px;font-weight:700;line-height:27px;white-space:nowrap">سجل تجارى&nbsp;:&nbsp; <span style="font-family:'Arimo','Arial',sans-serif;font-weight:700">{seller_cr}</span></div>
      <div style="font-size:17px;font-weight:700;line-height:27px;white-space:nowrap">الرقم الضريبى&nbsp;:<span style="font-family:'Arimo','Arial',sans-serif;font-weight:700">{seller_vat}</span></div>
      <div style="font-size:17px;font-weight:700;line-height:27px;white-space:nowrap">العنوان&nbsp;:&nbsp; {seller_address}</div>
    </div>

    <!-- ===== TITLE ===== -->
    <div style="position:absolute;left:295px;top:167px;width:184px;height:31px;border:1.5px solid {INK};background:#ffffff;display:flex;align-items:center;justify-content:center;direction:rtl;box-sizing:border-box">
      <span style="font-size:20px;font-weight:800;color:#000">ايصال إستلام نقديه</span>
    </div>

    <!-- ===== SERIAL NO ===== -->
    <div style="position:absolute;left:316px;top:206px;font-family:'Arimo','Arial',sans-serif;font-size:16px;font-weight:700;color:#ff0000;letter-spacing:.3px">No&nbsp;:&nbsp; {number}</div>

    <!-- ===== FRAME ===== -->
    <div style="position:absolute;left:36px;top:235px;width:722px;height:357px;border:1.5px solid {INK};box-sizing:border-box">

      <!-- Amount box (left) — cell heights sum to the inner content box (58px)
           and overflow is clipped so the white value bg never paints over the
           box's bottom border: all four sides stay visible. -->
      <div style="position:absolute;left:18px;top:18px;width:136px;height:60px;border:1px solid {INK};box-sizing:border-box;direction:rtl;overflow:hidden">
        <div style="height:30px;box-sizing:border-box;background:{HILITE};border-bottom:1px solid {INK};display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;color:#000">المبلغ</div>
        <div style="height:28px;background:#ffffff;display:flex;align-items:center;justify-content:center;font-family:'Arimo','Arial',sans-serif;font-size:15px;font-weight:700;color:#000">{amount}</div>
      </div>

      <!-- تحرير في row -->
      <div style="position:absolute;right:16px;top:56px;background:{HILITE};padding:1px 5px;direction:rtl;font-size:15px;font-weight:700;color:#000;white-space:nowrap">تحرير في&nbsp;:</div>
      <div style="position:absolute;right:100px;top:58px;font-family:'Arimo','Arial',sans-serif;font-size:15px;font-weight:700;color:#000;direction:ltr">{issue_date}</div>

      <!-- استلمنا من السيد row -->
      <div style="position:absolute;right:16px;top:85px;background:{HILITE};padding:1px 5px;direction:rtl;font-size:15px;font-weight:700;color:#000;white-space:nowrap">استلمنا من السيد&nbsp;:</div>
      <div style="position:absolute;right:158px;top:87px;font-size:15px;font-weight:700;color:#000;direction:rtl;white-space:nowrap">{received_from}</div>

      <!-- dashed line 1 -->
      <div style="position:absolute;left:6px;top:121px;width:598px;height:0;border-top:2px dashed {DASH}"></div>

      <!-- مبلغ وقدره row -->
      <div style="position:absolute;right:16px;top:133px;background:{HILITE};padding:1px 5px;direction:rtl;font-size:15px;font-weight:700;color:#000;white-space:nowrap">مبلغ وقدره&nbsp;:</div>
      <div style="position:absolute;right:120px;top:135px;font-size:14px;font-weight:700;color:#000;direction:rtl;white-space:nowrap">{words}</div>

      <!-- dashed line 2 -->
      <div style="position:absolute;left:6px;top:169px;width:598px;height:0;border-top:2px dashed {DASH}"></div>

      <!-- وذلك قيمة row -->
      <div style="position:absolute;right:16px;top:181px;background:{HILITE};padding:1px 5px;direction:rtl;font-size:15px;font-weight:700;color:#000;white-space:nowrap">وذلك قيمة&nbsp;:</div>
      <div style="position:absolute;right:110px;top:183px;font-size:15px;font-weight:700;color:#000;direction:rtl;white-space:nowrap">{purpose}</div>

      <!-- dashed line 3 -->
      <div style="position:absolute;left:6px;top:217px;width:598px;height:0;border-top:2px dashed {DASH}"></div>

      <!-- signature — one line, bottom-left (the mock-up's two captioned blocks
           and their dotted rules were dropped at the user's request) -->
      {signature}

    </div>
  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج الثاني (modern green layout)
# ======================================================================
def build_receipt_voucher_html_v2(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج الثاني (modern).

    Consumes the very same ``data`` dict as :func:`build_receipt_voucher_html`
    (keys: ``number``, ``issue_date``, ``amount``, ``received_from``,
    ``purpose`` and a ``seller`` mapping with ``name`` / ``cr`` / ``vat`` /
    ``address``) — only the visual design differs: a green header bar, pale-green
    badges, a bold amount box, clean hairline rows, and the signature line.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="16px", color="#333")

    # Company logo (on a white chip so any logo reads on the green bar).
    logo = company_logo_data_uri(seller)
    logo_html = (
        f'<div style="background:#ffffff; border-radius:8px; padding:7px; margin-bottom:10px; '
        f'display:inline-flex; align-items:center; justify-content:center;">'
        f'<img src="{logo}" alt="logo" style="max-height:88px; max-width:190px; width:auto; height:auto; display:block; object-fit:contain;" />'
        f'</div>'
        if logo
        else ""
    )

    # A single flowing document (no absolute positioning): the layout is short so
    # it prints on one page. Arabic direction is set on the sheet; Latin runs
    # (amount, VAT/CR, date) are wrapped in dir="ltr" spans so they never mirror.
    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; min-height: 1123px; position: relative; background: #ffffff; font-family: 'Noto Sans Arabic', 'Tahoma', 'Arial', sans-serif; color: #1a1a1a; direction: rtl; box-sizing: border-box; margin: 0 auto; box-shadow: 0 1px 6px rgba(0,0,0,0.12); overflow: hidden; }}
  .ltr {{ direction: ltr; font-family: 'Arimo', 'Arial', sans-serif; unicode-bidi: isolate; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    .sheet {{ margin: 0; box-shadow: none; min-height: 0; page-break-inside: avoid; break-inside: avoid; }}
  }}
</style>
</head>
<body>
  <div class="sheet">

    <!-- ===== GREEN HEADER BAR ===== -->
    <div style="background:{V2_GREEN}; color:#ffffff; padding:26px 40px; display:flex; justify-content:space-between; align-items:flex-start; gap:24px;">
      <div style="text-align:right; line-height:1.7;">
        <div style="font-size:24px; font-weight:800;">{seller_name}</div>
        <div style="font-size:14px; color:{V2_HEADER_SUB};">سجل تجاري: <span class="ltr">{seller_cr}</span> &nbsp;•&nbsp; الرقم الضريبي: <span class="ltr">{seller_vat}</span></div>
        <div style="font-size:14px; color:{V2_HEADER_SUB};">العنوان: {seller_address}</div>
      </div>
      <div style="text-align:center; flex:none;">
        {logo_html}
        <div style="font-size:23px; font-weight:800; white-space:nowrap;">سند قبض</div>
        <div style="font-size:11px; letter-spacing:2px; color:{V2_HEADER_SUB};">RECEIPT VOUCHER</div>
      </div>
    </div>

    <!-- ===== BADGES: number + date ===== -->
    <div style="display:flex; gap:16px; padding:28px 40px 0;">
      <div style="flex:1; background:{V2_GREEN_SOFT}; border-radius:10px; padding:12px 18px; text-align:center;">
        <div style="font-size:13px; color:{V2_GREEN_MD};">رقم السند</div>
        <div style="font-size:20px; font-weight:700; color:{V2_GREEN_DK};" class="ltr">{number}</div>
      </div>
      <div style="flex:1; background:{V2_GREEN_SOFT}; border-radius:10px; padding:12px 18px; text-align:center;">
        <div style="font-size:13px; color:{V2_GREEN_MD};">التاريخ</div>
        <div style="font-size:20px; font-weight:700; color:{V2_GREEN_DK};" class="ltr">{issue_date}</div>
      </div>
    </div>

    <!-- ===== AMOUNT BOX ===== -->
    <div style="margin:24px 40px; border:2px solid {V2_GREEN}; border-radius:14px; padding:22px 20px; background:{V2_GREEN_TINT}; text-align:center;">
      <div style="font-size:14px; color:{V2_GREEN_MD};">المبلغ المستلَم</div>
      <div style="font-size:44px; font-weight:800; color:{V2_GREEN_DK}; line-height:1.2;"><span class="ltr">{amount}</span> <span style="font-size:22px;">ر.س</span></div>
      <div style="font-size:16px; color:#444; margin-top:8px;">{words}</div>
    </div>

    <!-- ===== DETAIL ROWS ===== -->
    <div style="padding:0 40px;">
      <div style="display:flex; align-items:baseline; padding:15px 0; border-bottom:1px solid #e3e3e3;">
        <div style="width:170px; flex:none; color:{V2_GREEN}; font-size:16px; font-weight:700;">استلمنا من السيد</div>
        <div style="flex:1; font-size:17px; font-weight:700; color:#1a1a1a;">{received_from}</div>
      </div>
      <div style="display:flex; align-items:baseline; padding:15px 0; border-bottom:1px solid #e3e3e3;">
        <div style="width:170px; flex:none; color:{V2_GREEN}; font-size:16px; font-weight:700;">وذلك قيمة</div>
        <div style="flex:1; font-size:17px; font-weight:700; color:#1a1a1a;">{purpose}</div>
      </div>
    </div>

    <!-- ===== SIGNATURE ===== -->
    <div style="padding:60px 70px 0;">{signature}</div>

  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج الثالث (bilingual formal bordered document)
# ======================================================================
def build_receipt_voucher_html_v3(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج الثالث (bilingual).

    Consumes the same ``data`` dict as the other builders. The ``seller`` mapping
    may additionally carry ``name_en`` / ``address_en`` for the left-hand English
    letterhead; both gracefully fall back to the Arabic values when absent. Every
    field label is bilingual (Arabic / English) and the whole document is framed.
    """
    seller = data.get("seller") or {}
    seller_name_ar = html.escape(str(seller.get("name") or ""))
    seller_name_en = html.escape(str(seller.get("name_en") or seller.get("name") or ""))
    seller_addr_ar = html.escape(str(seller.get("address") or ""))
    seller_addr_en = html.escape(str(seller.get("address_en") or seller.get("address") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="13px", color="#4b5560")

    # Company logo → a real (large, uncropped) image when present, otherwise the
    # small teal "شعار" placeholder circle.
    logo = company_logo_data_uri(seller)
    emblem = (
        f'<div style="flex:none; display:flex; align-items:center; justify-content:center;">'
        f'<img src="{logo}" alt="logo" style="max-width:150px; max-height:100px; '
        f'width:auto; height:auto; display:block; object-fit:contain;" /></div>'
        if logo
        else (
            f'<div style="width:56px; height:56px; border:1.5px solid {V3_TEAL}; '
            f'border-radius:50%; flex:none; display:flex; align-items:center; '
            f'justify-content:center; color:{V3_TEAL}; font-size:10px;">شعار</div>'
        )
    )

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; min-height: 1123px; position: relative; background: #ffffff; font-family: 'Noto Sans Arabic', 'Tahoma', 'Arial', sans-serif; color: {V3_INK}; box-sizing: border-box; margin: 0 auto; box-shadow: 0 1px 6px rgba(0,0,0,0.12); }}
  .frame {{ margin: 30px; border: 2px solid {V3_TEAL}; border-radius: 5px; overflow: hidden; }}
  .en {{ font-family: 'Arimo', 'Arial', sans-serif; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    .sheet {{ margin: 0; box-shadow: none; min-height: 0; page-break-inside: avoid; break-inside: avoid; }}
  }}
</style>
</head>
<body>
  <div class="sheet">
   <div class="frame">

    <!-- ===== BILINGUAL LETTERHEAD (Arabic right / English left) ===== -->
    <div style="display:flex; justify-content:space-between; align-items:flex-start; padding:18px 20px; gap:16px;">
      <div dir="ltr" class="en" style="text-align:left; line-height:1.55; flex:1;">
        <div style="font-size:16px; font-weight:700; color:{V3_TEAL};">{seller_name_en}</div>
        <div style="font-size:11.5px; color:{V3_MUTED};">{seller_addr_en}</div>
        <div style="font-size:11.5px; color:{V3_MUTED};">VAT: {seller_vat}</div>
        <div style="font-size:11.5px; color:{V3_MUTED};">CR: {seller_cr}</div>
      </div>
      {emblem}
      <div dir="rtl" style="text-align:right; line-height:1.55; flex:1;">
        <div style="font-size:17px; font-weight:700; color:{V3_TEAL};">{seller_name_ar}</div>
        <div style="font-size:12px; color:{V3_MUTED};">{seller_addr_ar}</div>
        <div style="font-size:12px; color:{V3_MUTED};">الرقم الضريبي: <span class="en">{seller_vat}</span></div>
        <div style="font-size:12px; color:{V3_MUTED};">سجل تجاري: <span class="en">{seller_cr}</span></div>
      </div>
    </div>

    <div style="height:3px; background:{V3_TEAL};"></div>

    <!-- ===== BILINGUAL TITLE ===== -->
    <div style="text-align:center; padding:14px;">
      <div style="display:inline-block; border:1.5px solid {V3_INK}; border-radius:5px; padding:6px 28px;">
        <span style="font-size:20px; font-weight:700;">سند قبض</span>
        <span class="en" style="font-size:11px; letter-spacing:2px; color:{V3_MUTED}; margin-right:10px;">RECEIPT VOUCHER</span>
      </div>
    </div>

    <!-- ===== FIELD CELLS ===== -->
    <div dir="rtl" style="padding:2px 20px 18px;">

      <div style="display:flex; border:1px solid {V3_LINE}; border-radius:5px; overflow:hidden; margin-bottom:10px;">
        <div style="flex:1; display:flex;">
          <div style="background:{V3_TEAL_SOFT}; color:{V3_TEAL}; padding:9px 13px; font-size:12.5px; font-weight:700; white-space:nowrap;">رقم السند / No.</div>
          <div class="en" style="flex:1; padding:9px 13px; font-size:13px; font-weight:700;">{number}</div>
        </div>
        <div style="flex:1; display:flex; border-right:1px solid {V3_LINE};">
          <div style="background:{V3_TEAL_SOFT}; color:{V3_TEAL}; padding:9px 13px; font-size:12.5px; font-weight:700; white-space:nowrap;">التاريخ / Date</div>
          <div class="en" style="flex:1; padding:9px 13px; font-size:13px; font-weight:700;">{issue_date}</div>
        </div>
      </div>

      <div style="display:flex; border:1.5px solid {V3_TEAL}; border-radius:5px; overflow:hidden; margin-bottom:10px;">
        <div style="background:{V3_TEAL}; color:#ffffff; padding:12px 15px; font-size:13.5px; font-weight:700; white-space:nowrap; display:flex; align-items:center;">المبلغ / Amount</div>
        <div class="en" style="flex:1; padding:12px 15px; font-size:21px; font-weight:700; color:{V3_TEAL}; display:flex; align-items:center;">{amount} <span style="font-size:13px; margin-right:7px;">SAR</span></div>
      </div>

      <div style="display:flex; border:1px solid {V3_LINE}; border-radius:5px; overflow:hidden; margin-bottom:10px;">
        <div style="background:{V3_TEAL_SOFT}; color:{V3_TEAL}; padding:10px 13px; font-size:12.5px; font-weight:700; white-space:nowrap; display:flex; align-items:center;">استلمنا من / Received from</div>
        <div style="flex:1; padding:10px 13px; font-size:14px; font-weight:700; display:flex; align-items:center;">{received_from}</div>
      </div>

      <div style="display:flex; border:1px solid {V3_LINE}; border-radius:5px; overflow:hidden; margin-bottom:10px;">
        <div style="background:{V3_TEAL_SOFT}; color:{V3_TEAL}; padding:10px 13px; font-size:12.5px; font-weight:700; white-space:nowrap; display:flex; align-items:center;">مبلغ وقدره / The sum of</div>
        <div style="flex:1; padding:10px 13px; font-size:13px; font-weight:700; display:flex; align-items:center;">{words}</div>
      </div>

      <div style="display:flex; border:1px solid {V3_LINE}; border-radius:5px; overflow:hidden;">
        <div style="background:{V3_TEAL_SOFT}; color:{V3_TEAL}; padding:10px 13px; font-size:12.5px; font-weight:700; white-space:nowrap; display:flex; align-items:center;">وذلك قيمة / Being</div>
        <div style="flex:1; padding:10px 13px; font-size:14px; font-weight:700; display:flex; align-items:center;">{purpose}</div>
      </div>
    </div>

    <!-- ===== SIGNATURE ===== -->
    <div style="padding:34px 55px 26px;">{signature}</div>

   </div>
  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج الرابع (banking / cheque-style secure voucher)
# ======================================================================
def build_receipt_voucher_html_v4(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج الرابع (cheque style).

    Ported from the mock-up the user approved (``نماذج السند المقترحة/3-بنكي-شيكي.pdf``):
    a heavy navy frame closed top and bottom by a striped security band, the
    serial in a red block, a boxed amount panel and dotted leader lines for the
    payer / words / purpose / payment rows. The mock-up's stamp ring and its two
    signature blocks were dropped at the user's request (2026-07-17) for the one
    shared signature line.

    ⚠️ The amount-in-words already ends with «لا غير» (:func:`amount_in_words_ar`
    appends it), so this must NOT add a terminator of its own — the first cut of
    the mock-up printed «لا غير» twice.

    Reads the same ``data`` dict as the other three, plus the optional
    ``payment_type`` label. Presentation only: no database, no ZATCA.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    payment_type = html.escape(str(data.get("payment_type") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    # `.ft` held the stamp ring and the two signature blocks; it now carries the
    # signature line alone, with the ring's height kept as padding so the frame
    # keeps the proportions the rest of the layout was measured against.
    signature = signature_line_html(font_size="13px", color=V4_NAVY)

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; height: 1123px; position: relative; background: #ffffff; overflow: hidden;
            box-sizing: border-box; margin: 0 auto; font-family: 'Noto Sans Arabic','Tahoma','Arial',sans-serif;
            color: #111111; box-shadow: 0 1px 6px rgba(0,0,0,.12); }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    /* A sheet exactly one A4 tall rounds up into a trailing blank second page
       (see النموذج الأول). That one pins a magic pixel height; this cannot —
       the frame grows with its content — so the sheet just follows the frame.
       No number to keep in step, and nothing can be clipped. */
    .sheet {{ margin: 0; box-shadow: none; height: auto; page-break-inside: avoid; break-inside: avoid; }}
  }}
  .ltr {{ direction: ltr; font-family: 'Arimo','Arial',sans-serif; unicode-bidi: isolate; }}
  /* min-height, not height: the mock-up this was traced from fell back to Tahoma
     (it linked no webfont), while this template loads Noto Sans Arabic like the
     other three — whose taller line boxes grew the content ~120px and pushed the
     signatures straight through the bottom security strip. A frame that grows
     with its content cannot spill, whatever the font resolves to. `.in` is in
     normal flow for the same reason; absolute top/bottom would clamp it again. */
  /* In flow (not absolute) so the sheet's own height:auto can follow it in print. */
  .frame {{ position: relative; margin: 26px; min-height: 660px;
            border: 2.5px solid {V4_NAVY}; box-sizing: border-box; direction: rtl;
            padding: 9px 0; }}
  .sec, .secb {{ position: absolute; left: 0; right: 0; height: 9px;
                 background: repeating-linear-gradient(135deg,{V4_NAVY} 0 6px,{V4_NAVY_SOFT} 6px 12px); }}
  .sec {{ top: 0; }} .secb {{ bottom: 0; }}
  .in {{ padding: 20px 26px; }}
  .hd {{ display: flex; justify-content: space-between; align-items: flex-start;
         border-bottom: 2px solid {V4_NAVY}; padding-bottom: 12px; }}
  .co-n {{ font-size: 20px; font-weight: 800; color: {V4_NAVY}; }}
  .co-m {{ font-size: 11.5px; color: {V4_MUTED}; margin-top: 3px; }}
  .snbox {{ background: {V4_RED}; color: #ffffff; padding: 8px 16px; border-radius: 3px; text-align: center; }}
  .sn-k {{ font-size: 9px; letter-spacing: 1.4px; font-weight: 700; opacity: .85; }}
  .sn-v {{ font-size: 19px; font-weight: 800; letter-spacing: .6px; }}
  .ttl {{ text-align: center; margin-top: 16px; }}
  .ttl-ar {{ font-size: 26px; font-weight: 800; color: {V4_NAVY}; letter-spacing: 1px; }}
  .ttl-en {{ font-size: 10px; font-weight: 700; letter-spacing: 3px; color: #7b8590; margin-top: 2px; }}
  .pay {{ display: flex; margin-top: 18px; border: 2px solid {V4_NAVY}; }}
  .pay-k {{ flex: none; width: 104px; background: {V4_NAVY}; color: #ffffff; font-size: 12.5px; font-weight: 800;
            display: flex; align-items: center; justify-content: center; }}
  .pay-v {{ flex: 1; background: {V4_WASH}; font-size: 32px; font-weight: 800; color: {V4_NAVY};
            letter-spacing: 1px; display: flex; align-items: center; justify-content: center; padding: 12px 0; }}
  .pay-c {{ flex: none; width: 70px; background: {V4_NAVY_SOFT}; font-size: 13px; font-weight: 800;
            color: {V4_NAVY}; display: flex; align-items: center; justify-content: center;
            border-right: 2px solid {V4_NAVY}; }}
  .ln {{ display: flex; align-items: flex-end; gap: 9px; margin-top: 20px; }}
  .ln-k {{ flex: none; font-size: 13px; font-weight: 800; color: {V4_NAVY}; padding-bottom: 2px; }}
  .ln-v {{ flex: 1; border-bottom: 1.5px dotted {V4_NAVY}; font-size: 15px; font-weight: 700;
           color: {V4_INK}; padding-bottom: 3px; text-align: center; }}
  .ft {{ margin-top: 26px; padding-bottom: 52px; }}
</style>
</head>
<body>
  <div class="sheet">
    <div class="frame">
      <div class="sec"></div><div class="secb"></div>
      <div class="in">
        <div class="hd">
          <div>
            <div class="co-n">{seller_name}</div>
            <div class="co-m">سجل تجارى : <span class="ltr">{seller_cr}</span></div>
            <div class="co-m">الرقم الضريبى : <span class="ltr">{seller_vat}</span></div>
            <div class="co-m">{seller_address}</div>
          </div>
          <div class="snbox"><div class="sn-k">No</div><div class="sn-v ltr">{number}</div></div>
        </div>
        <div class="ttl"><div class="ttl-ar">سند قبض</div><div class="ttl-en">RECEIPT VOUCHER</div></div>
        <div class="pay">
          <div class="pay-k">المبلغ</div>
          <div class="pay-v ltr">{amount}</div>
          <div class="pay-c">ر.س</div>
        </div>
        <div class="ln"><span class="ln-k">استلمنا من السيد</span><span class="ln-v">{received_from}</span></div>
        <div class="ln"><span class="ln-k">مبلغ وقدره</span><span class="ln-v">{words}</span></div>
        <div class="ln"><span class="ln-k">وذلك قيمة</span><span class="ln-v">{purpose}</span></div>
        <div class="ln"><span class="ln-k">طريقة الدفع</span><span class="ln-v">{payment_type}</span>
          <span class="ln-k" style="margin-right:20px">تحرير فى</span>
          <span class="ln-v ltr" style="max-width:150px">{issue_date}</span></div>
        <div class="ft">{signature}</div>
      </div>
    </div>
  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج الخامس (symmetric bilingual letterhead)
# ======================================================================
def build_receipt_voucher_html_v5(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج الخامس (symmetric).

    Ported from the mock-up the user approved in chat: the company letterhead is
    mirrored — English block on the left, Arabic block on the right, the logo a
    circle centred between them — closed by a thick/thin rule pair. Below it sit
    the bilingual title box with the red serial opposite it, a hairline grid of
    label/value rows, and the amount box beside the two signature lines.

    Consumes the same ``data`` dict as the other builders. ``seller['name_en']``
    and ``seller['address_en']`` feed the English half and fall back to the
    Arabic values when the company record has none — this is the only template
    where that fallback is *visible* (the two halves then read identically), so
    it is deliberate rather than a silent default. Presentation only: no
    database, no ZATCA.
    """
    seller = data.get("seller") or {}
    seller_name_ar = html.escape(str(seller.get("name") or ""))
    seller_name_en = html.escape(str(seller.get("name_en") or seller.get("name") or ""))
    seller_addr_ar = html.escape(str(seller.get("address") or ""))
    seller_addr_en = html.escape(str(seller.get("address_en") or seller.get("address") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    payment_type = html.escape(str(data.get("payment_type") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="12px", color=V5_MUTED)

    # The logo is the header's centre column, so it always occupies it: a real
    # image when the company has one, else the outlined circle from the mock-up.
    # Falling back to nothing would collapse the symmetry the design is built on.
    logo = company_logo_data_uri(seller)
    emblem = (
        f'<img src="{logo}" alt="logo" style="max-width:96px; max-height:96px; '
        f'width:auto; height:auto; display:block; margin:0 auto; object-fit:contain;" />'
        if logo
        else (
            f'<div style="width:92px; height:92px; border:1px solid {V5_RULE}; '
            f'border-radius:50%; margin:0 auto; display:flex; align-items:center; '
            f'justify-content:center; color:{V5_MUTED}; font-size:11px;">شعار الشركة</div>'
        )
    )

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; min-height: 1123px; background: #ffffff; box-sizing: border-box;
            margin: 0 auto; padding: 40px 44px; font-family: 'Noto Sans Arabic','Tahoma','Arial',sans-serif;
            color: {V5_INK}; box-shadow: 0 1px 6px rgba(0,0,0,.12); }}
  /* Fonts live here, never in an inline style="" — the stacks carry quotes that
     would end the attribute and silently drop every declaration after them
     (invoice handoff §5.3). */
  .en {{ font-family: 'Arimo','Arial',sans-serif; }}
  .ltr {{ direction: ltr; font-family: 'Arimo','Arial',sans-serif; unicode-bidi: isolate; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    /* Flowing layout, so the sheet simply follows its content: a sheet pinned to
       exactly one A4 (1123px) rounds up into a trailing blank page. */
    .sheet {{ margin: 0; box-shadow: none; min-height: 0; page-break-inside: avoid; break-inside: avoid; }}
  }}
  .hd {{ display: grid; grid-template-columns: 1fr 96px 1fr; align-items: center; gap: 14px; }}
  .hd-en {{ direction: ltr; text-align: left; }}
  .hd-ar {{ direction: rtl; text-align: right; }}
  .co-n {{ font-size: 19px; font-weight: 800; }}
  .co-m {{ font-size: 13px; color: {V5_MUTED}; margin-top: 3px; line-height: 1.55; }}
  .rule {{ height: 2.5px; background: {V5_INK}; margin-top: 18px; }}
  .rule2 {{ height: 1px; background: {V5_INK}; margin: 2.5px 0 20px; }}
  .ttlrow {{ display: flex; align-items: center; justify-content: space-between; direction: rtl; }}
  .ttl {{ border: 1.5px solid {V5_INK}; border-radius: 4px; padding: 8px 26px; text-align: center; }}
  .ttl-ar {{ font-size: 21px; font-weight: 800; }}
  .ttl-en {{ font-size: 11px; letter-spacing: 2.5px; color: {V5_MUTED}; }}
  .sn-k {{ font-size: 12px; color: {V5_MUTED}; }}
  .sn-v {{ font-size: 24px; font-weight: 800; color: {V5_RED}; }}
  .grid {{ border: 1px solid {V5_RULE}; border-radius: 6px; margin-top: 20px; direction: rtl; overflow: hidden; }}
  .row {{ display: flex; border-bottom: 1px solid {V5_LINE}; }}
  .row:last-child {{ border-bottom: 0; }}
  .k {{ flex: none; width: 150px; background: {V5_FILL}; color: {V5_MUTED}; font-size: 13.5px;
        padding: 11px 14px; box-sizing: border-box; }}
  .k2 {{ border-right: 1px solid {V5_LINE}; }}
  /* The cell stays RTL and only the Latin run inside it is isolated: setting
     direction:ltr on the cell itself parks its text against the *far* edge,
     away from the label it belongs to and up against the next one. */
  .v {{ flex: 1; padding: 11px 14px; font-size: 15px; font-weight: 700; min-width: 0; }}
  .v-s {{ flex: none; width: 140px; }}
  .ft {{ display: flex; align-items: stretch; justify-content: space-between; gap: 18px;
         margin-top: 20px; direction: rtl; }}
  .amt {{ flex: none; border: 2px solid {V5_INK}; border-radius: 6px; display: flex;
          align-items: center; overflow: hidden; }}
  .amt-k {{ background: {V5_INK}; color: #ffffff; font-size: 13px; font-weight: 700; padding: 14px 15px; }}
  .amt-v {{ font-size: 27px; font-weight: 800; padding: 14px 24px; }}
  .amt-c {{ background: {V5_FILL}; color: {V5_MUTED}; font-size: 13px; font-weight: 700; padding: 14px 13px; }}
  /* .ft is RTL, so the amount box takes the right and this the left. A column
     that ends at flex-end keeps the signature on the baseline of the amount box;
     stretching (not shrink-to-fit) is what lets its own text-align:left bite. */
  .sgs {{ flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
          padding-bottom: 6px; }}
  .note {{ margin-top: 22px; border-top: 1px solid {V5_LINE}; padding-top: 8px; display: flex;
           justify-content: space-between; direction: rtl; font-size: 11px; color: {V5_SIGN}; }}
</style>
</head>
<body>
  <div class="sheet">

    <div class="hd">
      <div class="hd-en">
        <div class="co-n en">{seller_name_en}</div>
        <div class="co-m en">C.R. {seller_cr}</div>
        <div class="co-m en" style="margin-top:0">VAT No. {seller_vat}</div>
        <div class="co-m en" style="margin-top:0">{seller_addr_en}</div>
      </div>
      <div>{emblem}</div>
      <div class="hd-ar">
        <div class="co-n">{seller_name_ar}</div>
        <div class="co-m">سجل تجارى : <span class="ltr">{seller_cr}</span></div>
        <div class="co-m" style="margin-top:0">الرقم الضريبى : <span class="ltr">{seller_vat}</span></div>
        <div class="co-m" style="margin-top:0">{seller_addr_ar}</div>
      </div>
    </div>

    <div class="rule"></div><div class="rule2"></div>

    <div class="ttlrow">
      <div class="ttl">
        <div class="ttl-ar">سند قبض</div>
        <div class="ttl-en en">RECEIPT VOUCHER</div>
      </div>
      <div style="text-align:left">
        <!-- «No.» is isolated: unwrapped, its trailing full stop is neutral and
             the RTL paragraph reorders it to the far left («.No / رقم السند»). -->
        <div class="sn-k">رقم السند / <span class="ltr">No.</span></div>
        <div class="sn-v ltr">{number}</div>
      </div>
    </div>

    <div class="grid">
      <div class="row">
        <div class="k">تحرير فى</div>
        <div class="v"><span class="ltr">{issue_date}</span></div>
        <div class="k k2">طريقة الدفع</div>
        <div class="v v-s">{payment_type}</div>
      </div>
      <div class="row"><div class="k">استلمنا من السيد</div><div class="v">{received_from}</div></div>
      <div class="row"><div class="k">مبلغ وقدره</div><div class="v">{words}</div></div>
      <div class="row"><div class="k">وذلك قيمة</div><div class="v">{purpose}</div></div>
    </div>

    <div class="ft">
      <div class="amt">
        <div class="amt-k">المبلغ</div>
        <div class="amt-v ltr">{amount}</div>
        <div class="amt-c">ر.س</div>
      </div>
      <div class="sgs">{signature}</div>
    </div>

    <div class="note">
      <span>هذا السند لا يُعد فاتورة ضريبية</span>
      <span class="en">PHASEINV2</span>
    </div>

  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج السادس (side band + accounting stub)
# ======================================================================
def build_receipt_voucher_html_v6(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج السادس (side band).

    Ported from the mock-up the user approved in chat, and deliberately the odd
    one out of the six: no outer frame and no boxed grid. A green band runs down
    the left edge carrying the serial set vertically (readable while the voucher
    sits in a file), the amount is a 40px hero number rather than a table cell,
    the fields are underlined ledger lines, and a perforated stub across the foot
    repeats the serial / date / amount to be torn off and filed.

    Consumes the same ``data`` dict as the other builders, plus the optional
    ``payment_type``. Presentation only: no database, no ZATCA.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    payment_type = html.escape(str(data.get("payment_type") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="13px", color=V6_MUTED)

    logo = company_logo_data_uri(seller)
    logo_html = (
        f'<img src="{logo}" alt="logo" style="max-width:120px; max-height:64px; '
        f'width:auto; height:auto; display:block; object-fit:contain;" />'
        if logo
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  /* Height follows the content — no A4 pin, on screen or in print. The band is
     a flex child, so `stretch` matches it to whatever the content comes to, and
     the sheet ends just under the stub exactly as the approved mock-up does.
     Pinning a full A4 instead was tried and is wrong twice over: it rounds up
     into a trailing blank page (see النموذج الأول), and pushing the stub to the
     foot with margin-top:auto opened ~450px of white between the signatures and
     the stub — measured on the render, invisible in the markup. */
  .sheet {{ width: 794px; background: #ffffff; box-sizing: border-box;
            margin: 0 auto; display: flex; overflow: hidden;
            font-family: 'Noto Sans Arabic','Tahoma','Arial',sans-serif; color: {V6_INK};
            box-shadow: 0 1px 6px rgba(0,0,0,.12); }}
  .en {{ font-family: 'Arimo','Arial',sans-serif; }}
  .ltr {{ direction: ltr; font-family: 'Arimo','Arial',sans-serif; unicode-bidi: isolate; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    .sheet {{ margin: 0; box-shadow: none; page-break-inside: avoid; break-inside: avoid; }}
  }}
  .band {{ flex: none; width: 64px; background: {V6_GREEN}; color: #ffffff; min-height: 700px;
           display: flex; align-items: center; justify-content: center; }}
  /* vertical-rl + rotate(180deg) reads bottom-to-top, as the mock-up does. */
  .band-in {{ writing-mode: vertical-rl; transform: rotate(180deg); text-align: center; }}
  .band-k {{ font-size: 12px; letter-spacing: 3px; color: {V6_GREEN_SOFT}; }}
  .band-v {{ font-size: 23px; font-weight: 800; letter-spacing: 1.5px; margin-right: 20px; }}
  .body {{ flex: 1; min-width: 0; padding: 34px 38px 0; direction: rtl; }}
  .hd {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }}
  .co-n {{ font-size: 19px; font-weight: 800; }}
  .co-m {{ font-size: 13px; color: {V6_MUTED}; margin-top: 3px; }}
  .amt-k {{ font-size: 13px; color: {V6_MUTED}; letter-spacing: 1px; }}
  .amt-v {{ font-size: 48px; font-weight: 800; color: {V6_GREEN}; line-height: 1.15; }}
  .amt-c {{ font-size: 17px; color: {V6_MUTED}; font-weight: 400; }}
  .amt-w {{ font-size: 16px; margin-top: 5px; }}
  .amt-u {{ height: 3px; background: {V6_GREEN}; width: 210px; margin-top: 14px; }}
  .row {{ display: flex; padding: 14px 0; border-bottom: 1px solid {V6_LINE}; }}
  .k {{ flex: none; width: 140px; font-size: 14px; color: {V6_MUTED}; }}
  /* RTL cell + an isolated Latin span inside — never direction:ltr on the cell,
     which parks the date at the cell's far edge, flush against the next label
     («15-07-2026طريقة الدفع» on the first render). */
  .v {{ flex: 1; font-size: 16px; font-weight: 700; min-width: 0; }}
  .v-d {{ flex: none; width: 170px; }}
  .k-s {{ flex: none; width: 100px; font-size: 14px; color: {V6_MUTED}; }}
  .sgs {{ margin-top: 44px; padding-bottom: 34px; }}
  .stub {{ margin-top: 30px; border-top: 2px dotted {V6_SIGN}; display: flex;
           align-items: center; justify-content: space-between; padding: 14px 0 26px; }}
  .stub-k {{ font-size: 13px; color: {V6_MUTED}; }}
  .stub-cs {{ display: flex; gap: 26px; }}
  .stub-c {{ text-align: center; }}
  .stub-c-k {{ font-size: 11px; color: {V6_MUTED}; }}
  .stub-c-v {{ font-size: 14px; font-weight: 700; }}
  .stub-c-a {{ color: {V6_GREEN}; }}
</style>
</head>
<body>
  <div class="sheet">

    <div class="band">
      <div class="band-in">
        <span class="band-k en">RECEIPT VOUCHER</span>
        <span class="band-v ltr">{number}</span>
      </div>
    </div>

    <div class="body">

      <div class="hd">
        <div>
          <div class="co-n">{seller_name}</div>
          <div class="co-m">سجل تجارى : <span class="ltr">{seller_cr}</span> &nbsp;·&nbsp; الرقم الضريبى : <span class="ltr">{seller_vat}</span></div>
          <div class="co-m" style="margin-top:0">{seller_address}</div>
        </div>
        <div style="flex:none">{logo_html}</div>
      </div>

      <div style="margin-top:34px">
        <div class="amt-k">المبلغ المستلَم</div>
        <div class="amt-v"><span class="ltr">{amount}</span> <span class="amt-c">ريال سعودي</span></div>
        <div class="amt-w">{words}</div>
        <div class="amt-u"></div>
      </div>

      <div style="margin-top:30px">
        <div class="row"><div class="k">استلمنا من السيد</div><div class="v">{received_from}</div></div>
        <div class="row"><div class="k">وذلك قيمة</div><div class="v">{purpose}</div></div>
        <div class="row">
          <div class="k">تحرير فى</div>
          <div class="v v-d"><span class="ltr">{issue_date}</span></div>
          <div class="k-s">طريقة الدفع</div>
          <div class="v">{payment_type}</div>
        </div>
      </div>

      <div class="sgs">{signature}</div>

      <div class="stub">
        <div class="stub-k">كعب المحاسبة — يُحفظ بالدفتر</div>
        <div class="stub-cs">
          <div class="stub-c"><div class="stub-c-k">رقم السند</div><div class="stub-c-v ltr">{number}</div></div>
          <div class="stub-c"><div class="stub-c-k">التاريخ</div><div class="stub-c-v ltr">{issue_date}</div></div>
          <div class="stub-c"><div class="stub-c-k">المبلغ</div><div class="stub-c-v stub-c-a ltr">{amount}</div></div>
        </div>
      </div>

    </div>
  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج السابع (luxury gold, double-framed)
# ======================================================================
def build_receipt_voucher_html_v7(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج السابع (monochrome, framed).

    A white sheet inside a double dark frame (2px outer + 1px inner), a faint grey
    watermark of the seller name behind the content, a dark-ruled header with the
    bilingual title opposite the company block, a white bordered amount box,
    hairline detail rows and the shared signature line. Black-and-white by request
    (2026-07-23): white field backgrounds, clear dark text, no colour accent.
    Consumes the same ``data`` dict as the other builders (plus ``payment_type``).

    ⚠️ Fonts live only in the ``<style>`` classes (``.sheet`` / ``.en`` /
    ``.ltr``), never inline — a quoted stack inside ``style="…"`` ends the
    attribute and drops every later declaration. Latin runs are isolated in
    ``.ltr`` spans so the RTL paragraph never reorders them. The amount-in-words
    already ends with «لا غير», so nothing appends a terminator. Presentation
    only: no database, no ZATCA.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    payment_type = html.escape(str(data.get("payment_type") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="13px", color=V7_MUTED)

    # The watermark is decorative only (falls back to the title), so it never
    # carries a value the reader depends on — overflow:hidden clips a long name.
    watermark = seller_name or "سند قبض"

    logo = company_logo_data_uri(seller)
    logo_html = (
        f'<img src="{logo}" alt="logo" style="max-width:120px; max-height:70px; '
        f'width:auto; height:auto; display:block; margin:0 auto 6px; object-fit:contain;" />'
        if logo
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; min-height: 1123px; background: #ffffff; box-sizing: border-box;
            margin: 0 auto; padding: 38px 40px; font-family: 'Noto Sans Arabic','Tahoma','Arial',sans-serif;
            color: {V7_INK}; box-shadow: 0 1px 6px rgba(0,0,0,.12); }}
  .en {{ font-family: 'Arimo','Arial',sans-serif; }}
  .ltr {{ direction: ltr; font-family: 'Arimo','Arial',sans-serif; unicode-bidi: isolate; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    /* Flowing layout: the sheet follows its content, so a page pinned to exactly
       one A4 (1123px) never rounds up into a trailing blank page. */
    .sheet {{ margin: 0; box-shadow: none; min-height: 0; page-break-inside: avoid; break-inside: avoid; }}
  }}
  .frame {{ border: 2px solid {V7_GOLD}; padding: 5px; }}
  .card {{ border: 1px solid {V7_GOLD}; padding: 30px 28px; position: relative; overflow: hidden; direction: rtl; }}
  .wm {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
         font-size: 84px; font-weight: 800; color: rgba(0,0,0,.05); white-space: nowrap; }}
  .content {{ position: relative; }}
  .hd {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 18px;
         border-bottom: 1px solid {V7_GOLD}; padding-bottom: 14px; }}
  .co-n {{ font-size: 20px; font-weight: 800; }}
  .co-m {{ font-size: 12px; color: {V7_MUTED}; margin-top: 3px; line-height: 1.7; }}
  .ttl {{ text-align: center; flex: none; }}
  .ttl-ar {{ font-size: 24px; font-weight: 800; color: {V7_GOLD_DK}; }}
  .ttl-en {{ font-size: 11px; letter-spacing: 3px; color: {V7_GOLD_DK}; }}
  .meta {{ display: flex; justify-content: space-between; margin-top: 18px; font-size: 13px; color: {V7_MUTED}; }}
  .meta b {{ color: {V7_INK}; font-weight: 700; }}
  .amt {{ margin-top: 16px; border: 1.5px solid {V7_GOLD}; border-radius: 6px; background: {V7_WASH};
          text-align: center; padding: 16px; }}
  .amt-k {{ font-size: 12px; color: {V7_GOLD_DK}; letter-spacing: 1px; }}
  .amt-v {{ font-size: 34px; font-weight: 800; color: {V7_INK}; line-height: 1.2; }}
  .amt-c {{ font-size: 16px; color: {V7_GOLD_DK}; }}
  .amt-w {{ font-size: 14px; color: #555555; margin-top: 4px; }}
  .row {{ display: flex; padding: 12px 2px; border-bottom: 1px solid #e5e5e5; font-size: 14px; }}
  .row:last-child {{ border-bottom: 0; }}
  .k {{ flex: none; width: 150px; color: {V7_GOLD_DK}; font-weight: 700; }}
  .v {{ flex: 1; color: {V7_INK}; font-weight: 700; min-width: 0; }}
</style>
</head>
<body>
  <div class="sheet">
    <div class="frame">
      <div class="card">
        <div class="wm">{watermark}</div>
        <div class="content">

          <div class="hd">
            <div>
              <div class="co-n">{seller_name}</div>
              <div class="co-m">سجل تجارى : <span class="ltr">{seller_cr}</span></div>
              <div class="co-m" style="margin-top:0">الرقم الضريبى : <span class="ltr">{seller_vat}</span></div>
              <div class="co-m" style="margin-top:0">{seller_address}</div>
            </div>
            <div class="ttl">
              {logo_html}
              <div class="ttl-ar">سند قبض</div>
              <div class="ttl-en en">RECEIPT VOUCHER</div>
            </div>
          </div>

          <div class="meta">
            <span>رقم السند : <b class="ltr">{number}</b></span>
            <span>التاريخ : <b class="ltr">{issue_date}</b></span>
          </div>

          <div class="amt">
            <div class="amt-k">المبلغ المستلَم</div>
            <div class="amt-v"><span class="ltr">{amount}</span> <span class="amt-c">ر.س</span></div>
            <div class="amt-w">{words}</div>
          </div>

          <div style="margin-top:16px">
            <div class="row"><div class="k">استلمنا من السيد</div><div class="v">{received_from}</div></div>
            <div class="row"><div class="k">وذلك قيمة</div><div class="v">{purpose}</div></div>
            <div class="row"><div class="k">طريقة الدفع</div><div class="v">{payment_type}</div></div>
          </div>

          <div style="margin-top:26px">{signature}</div>

        </div>
      </div>
    </div>
  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج الثامن (split colour side panel)
# ======================================================================
def build_receipt_voucher_html_v8(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج الثامن (monochrome panel).

    The sheet is split into two columns divided by a dark rule: a side panel —
    which leads on an RTL page, so it sits on the right — carrying the bilingual
    title, the hero amount and the serial; and a column with the company block,
    the hairline detail rows and the shared signature line. Black-and-white by
    request (2026-07-23): white backgrounds, clear dark text, the panel set off by
    a rule rather than a fill. Same ``data`` dict as the others (plus ``payment_type``).

    ⚠️ Same house rules as the rest: fonts in ``<style>`` classes only, Latin
    runs isolated in ``.ltr`` spans, no «لا غير» terminator added, and a flowing
    (min-height, not pinned) sheet so it never spills a blank second page. The
    panel is a flex child, so ``align-items:stretch`` matches its height to the
    white column. Presentation only: no database, no ZATCA.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    payment_type = html.escape(str(data.get("payment_type") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="13px", color=V8_MUTED)

    # Logo goes on a white chip so any artwork reads on the indigo panel.
    logo = company_logo_data_uri(seller)
    logo_html = (
        f'<div class="chip"><img src="{logo}" alt="logo" style="max-height:56px; max-width:150px; '
        f'width:auto; height:auto; display:block; object-fit:contain;" /></div>'
        if logo
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; min-height: 1123px; background: #ffffff; box-sizing: border-box; margin: 0 auto;
            display: flex; direction: rtl; overflow: hidden;
            font-family: 'Noto Sans Arabic','Tahoma','Arial',sans-serif; color: {V8_INK};
            box-shadow: 0 1px 6px rgba(0,0,0,.12); }}
  .en {{ font-family: 'Arimo','Arial',sans-serif; }}
  .ltr {{ direction: ltr; font-family: 'Arimo','Arial',sans-serif; unicode-bidi: isolate; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    .sheet {{ margin: 0; box-shadow: none; min-height: 0; page-break-inside: avoid; break-inside: avoid; }}
  }}
  .panel {{ flex: none; width: 252px; background: {V8_PANEL}; color: {V8_INK};
            padding: 40px 26px; box-sizing: border-box; border-left: 2px solid {V8_INK}; }}
  .chip {{ background: #ffffff; border-radius: 8px; padding: 6px 8px; display: inline-flex; margin-bottom: 20px; }}
  .p-en {{ font-size: 11px; letter-spacing: 3px; color: {V8_PANEL_CAP}; }}
  .p-ttl {{ font-size: 27px; font-weight: 800; margin-top: 2px; }}
  .p-k {{ font-size: 12px; color: {V8_PANEL_SOFT}; margin-top: 30px; }}
  .p-amt {{ font-size: 32px; font-weight: 800; line-height: 1.15; }}
  .p-cur {{ font-size: 13px; color: {V8_PANEL_SOFT}; }}
  .p-num {{ font-size: 22px; font-weight: 700; }}
  .main {{ flex: 1; min-width: 0; padding: 40px 34px; box-sizing: border-box; }}
  .co-n {{ font-size: 20px; font-weight: 800; color: {V8_INK}; }}
  .co-m {{ font-size: 12px; color: {V8_MUTED}; margin-top: 4px; line-height: 1.7; }}
  .sep {{ height: 1px; background: {V8_LINE}; margin: 22px 0; }}
  .row {{ display: flex; padding: 14px 0; border-bottom: 1px solid {V8_LINE}; font-size: 14px; }}
  .k {{ flex: none; width: 140px; color: {V8_MUTED}; }}
  .v {{ flex: 1; font-weight: 700; color: {V8_INK}; min-width: 0; }}
</style>
</head>
<body>
  <div class="sheet">

    <div class="panel">
      {logo_html}
      <div class="p-en en">RECEIPT VOUCHER</div>
      <div class="p-ttl">سند قبض</div>

      <div class="p-k">المبلغ المستلَم</div>
      <div class="p-amt"><span class="ltr">{amount}</span></div>
      <div class="p-cur">ريال سعودي</div>

      <div class="p-k">رقم السند</div>
      <div class="p-num ltr">{number}</div>

      <div class="p-k">التاريخ</div>
      <div class="p-num ltr" style="font-size:17px">{issue_date}</div>
    </div>

    <div class="main">
      <div class="co-n">{seller_name}</div>
      <div class="co-m">سجل تجارى : <span class="ltr">{seller_cr}</span></div>
      <div class="co-m" style="margin-top:0">الرقم الضريبى : <span class="ltr">{seller_vat}</span></div>
      <div class="co-m" style="margin-top:0">{seller_address}</div>

      <div class="sep"></div>

      <div class="row"><div class="k">استلمنا من السيد</div><div class="v">{received_from}</div></div>
      <div class="row"><div class="k">مبلغ وقدره</div><div class="v">{words}</div></div>
      <div class="row"><div class="k">وذلك قيمة</div><div class="v">{purpose}</div></div>
      <div class="row"><div class="k">طريقة الدفع</div><div class="v">{payment_type}</div></div>

      <div style="margin-top:64px">{signature}</div>
    </div>

  </div>
</body>
</html>"""


# ======================================================================
# HTML template — النموذج التاسع (classic black-on-cream ledger)
# ======================================================================
def build_receipt_voucher_html_v9(data: dict[str, Any]) -> str:
    """Build the printable receipt-voucher HTML for النموذج التاسع (classic ledger).

    A deliberately ink-frugal, colourless layout: black text on a white sheet, a
    double-ruled letterhead, a centred title and a single double-bordered table
    of label/value cells, closed by the shared signature line. The traditional
    bookkeeping character is carried by the rules alone — the only tint is the
    neutral light-grey label-cell fill; there is no accent colour. Consumes the same
    ``data`` dict as the other builders (plus the optional ``payment_type``).

    ⚠️ Same house rules: fonts declared only in ``<style>`` classes, Latin runs
    isolated in ``.ltr`` spans, no «لا غير» terminator added, and a flowing sheet
    (min-height) so it never rounds up into a blank second page. Presentation
    only: no database, no ZATCA.
    """
    seller = data.get("seller") or {}
    seller_name = html.escape(str(seller.get("name") or ""))
    seller_cr = html.escape(str(seller.get("cr") or ""))
    seller_vat = html.escape(str(seller.get("vat") or ""))
    seller_address = html.escape(str(seller.get("address") or ""))

    number = html.escape(str(data.get("number") or ""))
    amount = _money(data.get("amount"))
    issue_date = _date(data.get("issue_date"))
    received_from = html.escape(str(data.get("received_from") or ""))
    purpose = html.escape(str(data.get("purpose") or ""))
    payment_type = html.escape(str(data.get("payment_type") or ""))
    words = html.escape(amount_in_words_ar(data.get("amount")))

    signature = signature_line_html(font_size="13px", color=V9_MUTED)

    logo = company_logo_data_uri(seller)
    logo_html = (
        f'<img src="{logo}" alt="logo" style="max-height:60px; max-width:160px; '
        f'width:auto; height:auto; display:block; margin:0 auto 8px; object-fit:contain;" />'
        if logo
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;500;600;700;800&family=Arimo:wght@400;700&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; background: #e9eaec; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ width: 794px; min-height: 1123px; background: {V9_CREAM}; box-sizing: border-box; margin: 0 auto;
            padding: 40px 44px; font-family: 'Noto Sans Arabic','Tahoma','Arial',sans-serif; color: {V9_INK};
            box-shadow: 0 1px 6px rgba(0,0,0,.12); direction: rtl; }}
  .en {{ font-family: 'Arimo','Arial',sans-serif; }}
  .ltr {{ direction: ltr; font-family: 'Arimo','Arial',sans-serif; unicode-bidi: isolate; }}
  @page {{ size: A4; margin: 0; }}
  @media print {{
    html, body {{ background: #ffffff; }}
    .sheet {{ margin: 0; box-shadow: none; min-height: 0; page-break-inside: avoid; break-inside: avoid; }}
  }}
  .hd {{ text-align: center; border-bottom: 3px double {V9_LINE}; padding-bottom: 12px; }}
  .co-n {{ font-size: 20px; font-weight: 800; }}
  .co-m {{ font-size: 12px; color: {V9_MUTED}; margin-top: 4px; }}
  .ttl {{ text-align: center; font-size: 20px; font-weight: 800; letter-spacing: 2px; margin: 16px 0; }}
  table.led {{ width: 100%; border-collapse: collapse; }}
  table.led td {{ border: 1px solid {V9_LINE}; padding: 9px 12px; font-size: 14px; vertical-align: middle; }}
  td.k {{ background: {V9_FILL}; font-weight: 700; width: 150px; white-space: nowrap; }}
  td.big {{ font-size: 20px; font-weight: 800; }}
  .note {{ text-align: center; margin-top: 12px; font-size: 11px; color: #555555; }}
</style>
</head>
<body>
  <div class="sheet">

    <div class="hd">
      {logo_html}
      <div class="co-n">{seller_name}</div>
      <div class="co-m">سجل تجارى <span class="ltr">{seller_cr}</span> — الرقم الضريبى <span class="ltr">{seller_vat}</span></div>
      <div class="co-m">{seller_address}</div>
    </div>

    <div class="ttl">سنـد قبـض</div>

    <table class="led">
      <tr>
        <td class="k">رقم السند</td><td class="ltr">{number}</td>
        <td class="k">التاريخ</td><td class="ltr">{issue_date}</td>
      </tr>
      <tr><td class="k">استلمنا من السيد</td><td colspan="3">{received_from}</td></tr>
      <tr><td class="k">مبلغ وقدره</td><td colspan="3">{words}</td></tr>
      <tr><td class="k">وذلك قيمة</td><td colspan="3">{purpose}</td></tr>
      <tr><td class="k">طريقة الدفع</td><td colspan="3">{payment_type}</td></tr>
      <tr><td class="k">المبلغ</td><td class="big" colspan="3"><span class="ltr">{amount}</span> ريال</td></tr>
    </table>

    <div style="margin-top:26px">{signature}</div>
    <div class="note">هذا السند لا يُعد فاتورة ضريبية</div>

  </div>
</body>
</html>"""


# ======================================================================
# Template picker — ONE dispatch for every screen that prints a voucher
# ======================================================================
VOUCHER_TEMPLATE_OPTIONS: tuple[str, ...] = (
    "النموذج الأول",
    "النموذج الثاني",
    "النموذج الثالث",
    "النموذج الرابع",
    "النموذج الخامس",
    "النموذج السادس",
    "النموذج السابع",
    "النموذج الثامن",
    "النموذج التاسع",
)


def choose_voucher_builder(
    parent: "QWidget | None", action_label: str = "معاينة سند القبض"
) -> "Callable[[dict[str, Any]], str] | None":
    """Ask which voucher layout to use; return its HTML builder, or ``None``.

    Both the vouchers screen and the sales-invoice screen print this document,
    and each used to open-code its own copy of the option list *and* the builder
    map — two places to keep in step, silently. That is the exact shape of the
    bug that once sent every invoice choice past the second one to the wrong
    builder (see the invoice handoff, section 5.2), so the list and the map live
    here, once. Adding a fifth layout is one line in each.
    """
    from app.ui.screens.saudi_invoice_print import choose_invoice_template

    choice = choose_invoice_template(parent, action_label, VOUCHER_TEMPLATE_OPTIONS)
    if choice is None:
        return None
    return {
        1: build_receipt_voucher_html,
        2: build_receipt_voucher_html_v2,
        3: build_receipt_voucher_html_v3,
        4: build_receipt_voucher_html_v4,
        5: build_receipt_voucher_html_v5,
        6: build_receipt_voucher_html_v6,
        7: build_receipt_voucher_html_v7,
        8: build_receipt_voucher_html_v8,
        9: build_receipt_voucher_html_v9,
    }.get(choice, build_receipt_voucher_html)

# ======================================================================
# PDF page layout
# ======================================================================
def _page_layout() -> QPageLayout:
    """A4 portrait with zero margins — matches the 794×1123 design sheet."""
    return QPageLayout(
        QPageSize(QPageSize.A4),
        QPageLayout.Portrait,
        QMarginsF(0, 0, 0, 0),
    )


def _default_pdf_name(data: dict[str, Any]) -> str:
    number = str(data.get("number") or "").strip() or "receipt"
    safe = "".join(ch for ch in number if ch.isalnum() or ch in ("-", "_")) or "receipt"
    return f"سند_قبض_{safe}.pdf"


# ======================================================================
# Preview dialog
# ======================================================================
class SaudiReceiptVoucherPreviewDialog(QDialog):
    """On-screen preview of the printable voucher, with export/print/close."""

    def __init__(
        self,
        data: dict[str, Any],
        parent: QWidget | None = None,
        html_builder: Callable[[dict[str, Any]], str] = build_receipt_voucher_html,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._html_builder = html_builder
        self.setWindowTitle("معاينة سند القبض")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(900, 940)

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
        self.view.setStyleSheet("background:#e9eaec; border:1px solid #E5E7EB; border-radius:8px;")
        self.view.setHtml(html_builder(data))
        root.addWidget(self.view, 1)

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "حفظ سند القبض PDF", _default_pdf_name(self._data), "PDF (*.pdf)"
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
                QMessageBox.information(self, "تم", f"تم تصدير السند إلى:\n{file_path}")
            else:
                QMessageBox.warning(self, "تعذّر التصدير", "حدث خطأ أثناء إنشاء ملف PDF.")

        self.view.page().pdfPrintingFinished.connect(_finished)
        self.view.page().printToPdf(path, _page_layout())

    def _on_print(self) -> None:
        try:
            from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        except Exception:  # noqa: BLE001
            QMessageBox.warning(self, "الطباعة", "خدمة الطباعة غير متاحة.")
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.A4))
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QPrintDialog.Accepted:
            return
        self._printer = printer  # keep a reference during async print

        def _done(success: bool) -> None:  # noqa: ARG001
            try:
                self.view.printFinished.disconnect(_done)
            except (RuntimeError, TypeError):
                pass

        self.view.printFinished.connect(_done)
        self.view.print(printer)


# ======================================================================
# Direct PDF export (no preview window)
# ======================================================================
def export_receipt_voucher_to_pdf(
    parent: QWidget,
    data: dict[str, Any],
    html_builder: Callable[[dict[str, Any]], str] = build_receipt_voucher_html,
) -> None:
    """Ask for a path and render the voucher straight to a PDF file.

    Renders the chosen template's markup off-screen in a ``QWebEnginePage`` and
    prints it once loading settles. The page is parented so it lives until the
    async PDF write completes.
    """
    path, _ = QFileDialog.getSaveFileName(
        parent, "حفظ سند القبض PDF", _default_pdf_name(data), "PDF (*.pdf)"
    )
    if not path:
        return

    from PySide6.QtWebEngineCore import QWebEnginePage

    page = QWebEnginePage(parent)
    # Hold a reference on the parent so the page is not garbage-collected mid-print.
    holder = getattr(parent, "_voucher_pdf_export_pages", None)
    if holder is None:
        holder = []
        parent._voucher_pdf_export_pages = holder  # type: ignore[attr-defined]
    holder.append(page)

    def _cleanup() -> None:
        try:
            holder.remove(page)
        except ValueError:
            pass
        page.deleteLater()

    def _on_pdf(file_path: str, success: bool) -> None:
        if success:
            QMessageBox.information(parent, "تم", f"تم تصدير السند إلى:\n{file_path}")
        else:
            QMessageBox.warning(parent, "تعذّر التصدير", "حدث خطأ أثناء إنشاء ملف PDF.")
        _cleanup()

    def _on_load(ok: bool) -> None:
        if not ok:
            QMessageBox.warning(parent, "تعذّر التصدير", "تعذّر تجهيز مستند السند.")
            _cleanup()
            return
        # Give fonts/layout a beat to settle before printing.
        QTimer.singleShot(150, lambda: page.printToPdf(path, _page_layout()))

    page.pdfPrintingFinished.connect(_on_pdf)
    page.loadFinished.connect(_on_load)
    page.setHtml(html_builder(data))


__all__ = [
    "build_receipt_voucher_html",
    "build_receipt_voucher_html_v2",
    "build_receipt_voucher_html_v3",
    "build_receipt_voucher_html_v4",
    "build_receipt_voucher_html_v5",
    "build_receipt_voucher_html_v6",
    "build_receipt_voucher_html_v7",
    "build_receipt_voucher_html_v8",
    "build_receipt_voucher_html_v9",
    "VOUCHER_TEMPLATE_OPTIONS",
    "choose_voucher_builder",
    "SaudiReceiptVoucherPreviewDialog",
    "export_receipt_voucher_to_pdf",
]
