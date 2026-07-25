"""Printable Saudi tax-invoice document — preview + PDF export.

Recreates, pixel-for-pixel, the approved Claude-Design mock-up
(``استخراج كود التصميم`` / ``Invoice.dc.html``): an A4/Letter tax-invoice sheet
with a logo + bilingual title + QR header, ``Bill From`` / ``Bill To`` cards, an
invoice-meta strip, an items table with a green header, and a totals panel with
the amount spelled out in Arabic words.

The mock-up is HTML/CSS, so the most faithful reproduction (it leans heavily on
CSS grid/flex) is to render the very same markup in a ``QWebEngineView`` and let
Chromium print it to PDF. This module owns:

* :func:`build_invoice_html` — the template, populated from live invoice data;
* :class:`SaudiInvoicePreviewDialog` — the on-screen preview (with its own
  export / print / close buttons);
* :func:`export_invoice_to_pdf` — a direct "save as PDF" for the toolbar button.

It is presentation only: it never touches the database and never generates any
ZATCA cryptographic material — it merely *displays* the data the screen already
holds (including the non-cryptographic TLV QR, rendered as a real QR image).
"""

from __future__ import annotations

import base64
import datetime
import html
import io
from decimal import Decimal
from pathlib import Path
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

# The design tokens are fixed to the approved mock-up's defaults.
ACCENT = "#607C61"
LINE = "#cfcfcf"

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "saudi_invoice"
_ASSET_CACHE: dict[str, str] = {}


# ======================================================================
# Assets / numbers / QR helpers
# ======================================================================
def _asset_data_uri(name: str) -> str:
    """Return an embedded ``data:`` URI for a bundled PNG asset (cached)."""
    if name in _ASSET_CACHE:
        return _ASSET_CACHE[name]
    path = _ASSETS_DIR / name
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        uri = f"data:image/png;base64,{encoded}"
    except OSError:
        uri = ""
    _ASSET_CACHE[name] = uri
    return uri


def company_logo_data_uri(seller: dict[str, Any] | None) -> str:
    """Build a ``data:`` URI for a company's embedded logo, or ``""`` if none.

    Accepts the ``seller`` mapping used by every print template. The logo is read
    from ``seller["logo"]`` (raw image bytes stored in the database) with its type
    from ``seller["logo_mime"]`` (defaulting to ``image/png``). Returns an empty
    string when the company has no logo, so callers fall back to a placeholder.
    """
    if not isinstance(seller, dict):
        return ""
    blob = seller.get("logo")
    if not blob:
        return ""
    try:
        raw = bytes(blob)
    except (TypeError, ValueError):
        return ""
    if not raw:
        return ""
    mime = str(seller.get("logo_mime") or "image/png").strip() or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _money(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:  # noqa: BLE001 - defensive formatting
        return "0.00"


def _qty(value: Any) -> str:
    try:
        normalized = Decimal(str(value)).normalize()
        text = format(normalized, "f")
        return text if text not in ("", "-0") else "0"
    except Exception:  # noqa: BLE001
        return str(value)


def _rate(value: Any) -> str:
    try:
        return f"{Decimal(str(value)):g}%"
    except Exception:  # noqa: BLE001
        return f"{value}%"


# ONE size for every template. Standing instruction (2026-07-18): "أي تعديل أو
# تحديث عملته على الـ QR code في النموذج الأولاني يكون متحدث على جميع النماذج" —
# النموذج الأول's QR *is* the shared QR, so there is deliberately a single constant
# and no per-template override. A scanned code is worth more than each design's own
# measured square, so the surrounding geometry gives way to it rather than the other
# way round; templates v2–v8 were re-seated around this number.
#
# النموذج الأول's QR scanned from the PDF but not once the invoice was printed on
# A4. Measured root cause is the printed MODULE PITCH, not resolution.
#
# Terminology, because conflating these two numbers caused real confusion here.
# A QR *symbol* of version V is 21 + 4*(V-1) modules on a side; the 4-module quiet
# zone is NOT part of the symbol and must never be fed into that formula. This
# <img> box draws symbol + quiet zone, so the pitch divides by the TOTAL:
#
#   payload (base64)   version   symbol modules   total across (symbol + 2*4)
#   612 chars (old)      19            93                  101
#   500 chars (now)      17            85                   93
#
# At the old 168px box, 101 across = 44.45mm / 101 = 0.440mm per module. The print
# path then shrinks it further (see _page_layout: printToPdf zeroes its margins,
# QPrinter does not, and a Letter layout on A4 paper adds fit-to-page), landing
# near 0.40mm — under what an office laser can hold once toner spread is added.
# The PDF keeps the full-resolution artwork, so on screen it still read fine;
# that is exactly the reported symptom.
#
# Things that were checked and are NOT the cause: resampling (the box_size=16
# source is an exact integer multiple of the module grid, so nearest-neighbour
# downscaling measured 0.00% module damage at 300 and 600dpi), quiet zone
# (correct at 4 modules), contrast (pure #000/#fff), and PDF image compression
# (the PNG was embedded whole, losslessly).
#
# Sizing, and why it is not 192px. 192px = 50.8mm, which over the pre-fix 101
# across was 0.503mm per module — clearing the ~0.5mm floor by 0.003mm, i.e. only
# correct if nothing downstream scales the page at all. The print path *does*
# scale: Windows print dialogs commonly default to "fit to printable area"
# (~0.95) and the artwork is Letter on A4 (~0.973), together landing around 0.92
# and putting the code back under the floor.
#
# 216px = 57.15mm. Over the current 93 across that is 0.615mm per module, holding
# 0.57mm after the 0.92 worst-case shrink.
#
# Sized deliberately for the *pre-fix* 101-across case too (0.566mm, still 0.52mm
# after shrink), so a payload that grows back toward version 19 does not silently
# fall under the floor.
#
# Raising the error-correction level would be counter-productive: EC=Q pushes the
# symbol up several versions, *shrinking* each module at the same physical size.
# EC=M is the right trade at this payload length.
QR_PRINT_PX = 216

# Kept so the intent survives a reader who only greps for the V1 name. It must
# never diverge from QR_PRINT_PX — test_invoice_qr_size.py pins that they match,
# which is what structurally enforces "template 1's QR is every template's QR".
QR_PRINT_PX_V1 = QR_PRINT_PX

# The floor a printed module must hold, after any downstream page scaling, for an
# office laser + phone camera to read it. Pinned in test_invoice_qr_vector.py.
QR_MIN_MODULE_MM = 0.50

# Legacy raster hints. No template uses these any more — every one renders the
# vector QR, and `image-rendering: pixelated` on an SVG makes Chromium rasterise it
# at layout size, which is the opposite of what is wanted. Kept only for
# qr_data_uri's raster fallback path.
QR_IMG_RENDERING = (
    "image-rendering:pixelated; image-rendering:crisp-edges; "
    "image-rendering:-webkit-optimize-contrast;"
)


def qr_data_uri(payload: str | None) -> str:
    """Render ``payload`` (the base64 TLV) as a real QR PNG ``data:`` URI.

    Falls back to the design's placeholder QR image when no payload is available
    or the ``qrcode`` dependency is missing.
    """
    if payload:
        try:
            import qrcode  # local import: optional dependency

            qr = qrcode.QRCode(
                version=None,
                # M level keeps solid print robustness while producing fewer,
                # chunkier modules than Q -> the code reads visually "bolder" at
                # the same physical size.
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                # Higher box_size => higher-resolution source PNG, so the printer
                # scales down (crisp) instead of up (blurry).
                box_size=16,
                # A 4-module quiet zone is required by the QR spec; scanners
                # frequently reject a printed code without it.
                border=4,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/png;base64,{encoded}"
        except Exception:  # noqa: BLE001 - any QR failure -> placeholder
            pass
    return _asset_data_uri("qr.png")


def _qr_svg_markup(payload: str) -> str:
    """Render ``payload`` as a *vector* QR SVG, or ``""`` if that is impossible.

    Size (``QR_PRINT_PX_V1``) is what fixes the failing print; vector is what
    keeps it fixed. There is no source resolution to lose, so nothing is
    resampled, interpolated or re-compressed on the way to the page:
    ``printToPdf`` emits the modules as PDF path operators and the printer
    rasterises them itself at its own device resolution (600–1200 dpi), with
    module edges landing on exact device pixels at *any* scale factor. That
    matters because the print path does shrink the sheet (Letter artwork on A4
    paper, plus QPrinter's non-zero driver margins) — a vector code survives that
    shrink losslessly, where a bitmap would have to be resampled to fit it.

    The encoded bytes are byte-for-byte the same as :func:`qr_data_uri` builds:
    same payload, same ``ERROR_CORRECT_M``, same 4-module border. Only the
    *carrier* changes, never the ZATCA TLV content.
    """
    try:
        import qrcode  # local import: optional dependency

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,  # irrelevant for SVG: the viewBox is in module units
            border=4,    # the spec-required quiet zone, all four sides
        )
        qr.add_data(payload)
        qr.make(fit=True)
        matrix = qr.get_matrix()  # includes the border rows/columns
    except Exception:  # noqa: BLE001 - any QR failure -> caller falls back
        return ""

    size = len(matrix)
    if not size or any(len(row) != size for row in matrix):
        return ""  # never emit a non-square QR

    # One path, with horizontal runs merged, keeps the markup small enough to sit
    # in a data: URI comfortably.
    parts: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < size:
            if not row[x]:
                x += 1
                continue
            run = 1
            while x + run < size and row[x + run]:
                run += 1
            parts.append(f"M{x} {y}h{run}v1h-{run}z")
            x += run
    if not parts:
        return ""

    # shape-rendering="crispEdges" is the vector equivalent of nearest-neighbour:
    # it turns off anti-aliasing so no module edge is ever rendered as a grey
    # half-tone. The viewBox is square and preserveAspectRatio is explicit, so the
    # code can never be stretched by the box it is placed in.
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        'preserveAspectRatio="xMidYMid meet" shape-rendering="crispEdges">'
        f'<rect x="0" y="0" width="{size}" height="{size}" fill="#ffffff"/>'
        f'<path fill="#000000" d="{"".join(parts)}"/>'
        "</svg>"
    )


def qr_vector_data_uri(payload: str | None) -> str:
    """A vector (SVG) ``data:`` URI for ``payload``; raster/placeholder if not.

    Used by النموذج الأول. The other templates keep :func:`qr_data_uri`.
    """
    if payload:
        markup = _qr_svg_markup(payload)
        if markup:
            encoded = base64.b64encode(markup.encode("utf-8")).decode("ascii")
            return f"data:image/svg+xml;base64,{encoded}"
    return qr_data_uri(payload)


# ----------------------------------------------------------------------
# Arabic amount-in-words
# ----------------------------------------------------------------------
def amount_in_words_ar(total: Any) -> str:
    """Spell a SAR amount in Arabic words, e.g. ``فقط ... ريالاً لا غير``."""
    try:
        amount = Decimal(str(total))
    except Exception:  # noqa: BLE001
        return ""
    amount = amount.quantize(Decimal("0.01"))
    riyals = int(amount)
    halalas = int((amount - riyals) * 100)
    try:
        from num2words import num2words

        riyal_words = _clean_ar_words(num2words(riyals, lang="ar"))
    except Exception:  # noqa: BLE001 - fall back to digits if lib misbehaves
        riyal_words = str(riyals)
    parts = [f"فقط {riyal_words} ريالاً"]
    if halalas:
        try:
            from num2words import num2words

            halala_words = _clean_ar_words(num2words(halalas, lang="ar"))
        except Exception:  # noqa: BLE001
            halala_words = str(halalas)
        parts.append(f"و{halala_words} هللة")
    parts.append("لا غير")
    return " ".join(parts)


def _clean_ar_words(text: str) -> str:
    """Attach the Arabic conjunction ``و`` to the following word (وأربعمائة)."""
    return " ".join(text.split()).replace(" و ", " و")


# ======================================================================
# The one signature line the vouchers and the statement print
# ======================================================================
# Every receipt-voucher layout used to end in two captioned signature blocks
# (توقيع الخزينة / توقيع الحسابات / المحاسب / أمين الصندوق / المستلم ...), and
# النموذج الرابع added a «ختم الشركة» ring beside them. All of it was dropped at
# the user's request (2026-07-17) for this single line, bottom-left. The text
# lives here, once, so seven independent layouts cannot drift apart on it.
SIGNATURE_TEXT = "التوقيع: ........."


def signature_line_html(
    *, font_size: str = "13px", color: str = "#333", extra_style: str = ""
) -> str:
    """«التوقيع: .........» alone, at the physical left of its container.

    ``text-align:left`` is deliberate: CSS's ``left`` is physical, so it stays on
    the sheet's left edge on these RTL documents (``start`` would flip it right).
    ``direction:rtl`` is equally deliberate — it keeps the whole run Arabic, so
    the label prints rightmost with the dots trailing away to its left, which is
    the order an Arabic reader expects and what the request drew. Under an LTR
    container the neutral dots would instead jump to the right of the label.

    Callers pass their own type scale and any positioning through ``extra_style``;
    only the wording is shared.
    """
    return (
        f'<div style="direction:rtl; text-align:left; font-size:{font_size}; '
        f'font-weight:700; color:{color}; white-space:nowrap; {extra_style}">'
        f"{SIGNATURE_TEXT}</div>"
    )


# ======================================================================
# HTML template
# ======================================================================
def _party_card(label_ar: str, label_en: str, party: dict[str, Any]) -> str:
    """One ``Bill From`` / ``Bill To`` card, matching the mock-up exactly."""
    name = html.escape(str(party.get("name") or ""))
    address = html.escape(str(party.get("address") or ""))
    vat = html.escape(str(party.get("vat") or ""))
    cr = html.escape(str(party.get("cr") or ""))
    return f"""
    <div style="border: 1px solid {LINE}; border-radius: 3px; overflow: hidden;">
      <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 11px; border-bottom: 1px solid {LINE};">
        <span style="color: {ACCENT}; font-weight: 800; font-size: 13px; letter-spacing: .3px; white-space: nowrap;">{label_en}</span>
        <span style="color: {ACCENT}; font-weight: 800; font-size: 13.5px; white-space: nowrap;" dir="rtl">{label_ar}</span>
      </div>
      <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 11px;">
        <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Name</span>
        <span style="font-size: 11.5px; color: #111; text-align: center; font-weight: 700;" dir="rtl">{name}</span>
        <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">الاسم</span>
      </div>
      <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 11px; border-top: 1px solid {LINE};">
        <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Adress</span>
        <span style="font-size: 12px; color: #111; text-align: center;" dir="rtl">{address}</span>
        <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">العنوان</span>
      </div>
      <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 11px; border-top: 1px solid {LINE};">
        <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Vat No</span>
        <span style="font-size: 12px; color: #111; text-align: center; letter-spacing: .5px;">{vat}</span>
        <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">الرقم الضريبي</span>
      </div>
      <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 11px; border-top: 1px solid {LINE};">
        <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Cr N</span>
        <span style="font-size: 12px; color: #111; text-align: center; letter-spacing: .5px;">{cr}</span>
        <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">السجل التجارى</span>
      </div>
    </div>"""


def _item_rows(lines: list[dict[str, Any]]) -> str:
    """The items rows. The name column wraps and grows its row (النموذج الخامس's rule).

    An item name can be one long unbroken token (no spaces to wrap at). The table
    is ``table-layout:fixed``, so such a name could not widen its column — it just
    spilled sideways out of the cell, straight across «#» and off the paper's left
    edge. ``overflow-wrap:anywhere`` lets the break happen mid-token; the row then
    grows, which the sheet's ``min-height`` (not ``height``) absorbs.
    """
    if not lines:
        return (
            '<tr><td colspan="7" style="border: 1px solid %s; text-align: center; '
            'padding: 18px 3px; color: #999; font-size: 12px;" dir="rtl">لا توجد أصناف</td></tr>'
            % LINE
        )
    rows: list[str] = []
    for index, line in enumerate(lines, start=1):
        code = html.escape(str(line.get("code") or ""))
        name = html.escape(str(line.get("name") or ""))
        rows.append(f"""
      <tr>
        <td style="border: 1px solid {LINE}; text-align: center; vertical-align: middle; padding: 9px 3px; font-size: 12px;">{index}</td>
        <td style="border: 1px solid {LINE}; padding: 8px 9px; vertical-align: middle;" dir="rtl">
          <div style="font-size: 12.5px; font-weight: 700; color: #1a1a1a; line-height: 1.3; overflow-wrap: anywhere;">{name}</div>
          <div style="font-size: 10px; color: #808080; direction: ltr; text-align: right; margin-top: 2px; letter-spacing: .3px; overflow-wrap: anywhere;">{code}</div>
        </td>
        <td style="border: 1px solid {LINE}; text-align: center; vertical-align: middle; padding: 9px 3px; font-size: 12px;">{_qty(line.get("qty"))}</td>
        <td style="border: 1px solid {LINE}; text-align: center; vertical-align: middle; padding: 9px 3px; font-size: 12px;">{_money(line.get("price"))}</td>
        <td style="border: 1px solid {LINE}; text-align: center; vertical-align: middle; padding: 9px 3px; font-size: 12px; font-weight: 500;">{_money(line.get("before"))}</td>
        <td style="border: 1px solid {LINE}; text-align: center; vertical-align: middle; padding: 9px 3px;">
          <div style="font-size: 12px; font-weight: 600;">{_money(line.get("vat_amount"))}</div>
          <div style="font-size: 9.5px; color: #777; margin-top: 1px;">{_rate(line.get("vat_rate"))}</div>
        </td>
        <td style="border: 1px solid {LINE}; text-align: center; vertical-align: middle; padding: 9px 3px; font-size: 12px; font-weight: 700;">{_money(line.get("total"))}</td>
      </tr>""")
    return "".join(rows)


def build_invoice_html(data: dict[str, Any]) -> str:
    """Build the full printable invoice HTML from a live-data ``data`` dict."""
    title_img = _asset_data_uri("tax-invoice-title.png")
    qr = qr_vector_data_uri(data.get("qr_payload"))
    logo = company_logo_data_uri(data.get("seller"))
    logo_img = (
        f'<img src="{logo}" alt="logo" style="max-height: 130px; max-width: 240px; '
        f'width: auto; height: auto; display: block; object-fit: contain;">'
        if logo
        else ""
    )

    invoice_number = html.escape(str(data.get("invoice_number") or ""))
    issued = data.get("issue_datetime")
    if isinstance(issued, datetime.datetime):
        issue_date = issued.strftime("%d-%m-%Y")
    else:
        issue_date = html.escape(str(issued or ""))
    payment = html.escape(str(data.get("payment_label") or ""))

    seller = data.get("seller") or {}
    customer = data.get("customer") or {}
    totals = data.get("totals") or {}
    lines = data.get("lines") or []

    subtotal = totals.get("subtotal", 0)
    vat_total = totals.get("vat", 0)
    grand_total = totals.get("total", 0)
    words = html.escape(amount_in_words_ar(grand_total))

    accent_soft = "rgba(96,124,97,.09)"

    # NOTE: the sheet is intentionally LTR-structured (exactly like the approved
    # mock-up): the header grid reads (empty)|title|qr and Bill From|Bill To read
    # left-to-right, and the totals panel sits flush-right. Arabic shaping/alignment
    # is handled per-cell via ``dir="rtl"`` spans — never a page-level RTL that
    # would mirror the whole layout.
    return f"""<!DOCTYPE html>
<html lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
  html, body {{ margin: 0; padding: 0; }}
  body {{ background: #e9e9ea; font-family: 'Tajawal', 'Cairo', 'Segoe UI', 'Tahoma', 'Arial', sans-serif; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .sheet {{ box-sizing: border-box; width: 816px; min-height: 1056px; margin: 24px auto; background: #ffffff; padding: 0 36px; box-shadow: 0 6px 28px rgba(0,0,0,.16); color: #1a1a1a; }}
  /* This sheet FLOWS (min-height, not height), so a long invoice carries onto a
     second printed page by itself — nothing is ever clipped here. Two things
     were wrong with how it did that (user, 2026-07-23: «ما يجيش مقصوص»):

     1. the page break could fall *through* a row or through the totals panel,
        printing half of each on two pages. `tr, .keep` below keeps them whole;
        the table's own <thead> repeats on the new page, which is Chromium's
        default for a real table header group.
     2. page 2 began at the paper's very edge — measured 0.3mm — because the
        sheet's own padding applies once, at the ends of the whole flow, not to
        each page. An office laser cannot print within ~4mm of the edge, so the
        first row on every continuation page came out shaved.

     The fix for (2) is the `page` wrapper: the sheet's top and bottom padding
     move into a <thead>/<tfoot> holding nothing but a spacer cell, and Chromium
     repeats those on every printed page — so every page gets the margin, not
     just the first and last. (A CSS `@page` margin does not work here: the
     zero-margin QPageLayout that printToPdf is given wins over it.) The spacers
     carry font-size:0 so their own line box cannot make them taller than the
     padding they replace: with it, page 1 prints ink-for-ink where it did. */
  tr, .keep {{ break-inside: avoid; page-break-inside: avoid; }}
  thead {{ display: table-header-group; }}
  .page {{ width: 100%; table-layout: fixed; border-collapse: collapse; }}
  .page > tbody > tr > td {{ padding: 0; vertical-align: top; }}
  .page > thead > tr > td, .page > tfoot > tr > td {{ padding: 0; font-size: 0; line-height: 0; }}
  .page-top {{ height: 30px; }}
  .page-bottom {{ height: 44px; }}
  @media print {{
    body {{ background: #ffffff; }}
    .sheet {{ margin: 0; box-shadow: none; width: 100%; min-height: 0; }}
  }}
</style>
</head>
<body>
  <div class="sheet">
  <table class="page">
    <thead><tr><td class="page-top"></td></tr></thead>
    <tfoot><tr><td class="page-bottom"></td></tr></tfoot>
    <tbody><tr><td>

    <!-- ===== HEADER (company logo | bilingual title | QR) ===== -->
    <div style="display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; column-gap: 16px; padding-bottom: 16px;">
      <div style="justify-self: start;">{logo_img}</div>
      <div style="justify-self: center;">
        <img src="{title_img}" alt="فاتورة ضريبية Tax Invoice" style="height: 46px; display: block;">
      </div>
      <div style="justify-self: end;">
        <!-- Vector QR: no image-rendering hint on purpose. "pixelated" would make
             Chromium rasterise the SVG at layout size and nearest-neighbour it,
             throwing away the very resolution independence we switched to SVG for.
             Crisp module edges come from shape-rendering inside the SVG instead. -->
        <img src="{qr}" alt="QR" style="width: {QR_PRINT_PX_V1}px; height: {QR_PRINT_PX_V1}px; display: block; background: #ffffff;">
      </div>
    </div>

    <div style="height: 1px; background: {LINE}; margin-bottom: 16px;"></div>

    <!-- ===== PARTIES ===== -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; column-gap: 16px;">
      {_party_card("فاتورة من", "Bill From", seller)}
      {_party_card("فاتورة إلى", "Bill To", customer)}
    </div>

    <!-- ===== INVOICE META ===== -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; border: 1px solid {LINE}; border-radius: 3px; margin-top: 14px; overflow: hidden;">
      <div style="border-right: 1px solid {LINE};">
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 8px 11px;">
          <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Invoice Number</span>
          <span style="font-size: 12.5px; color: #111; text-align: center; font-weight: 700;">{invoice_number}</span>
          <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">رقم الفاتورة</span>
        </div>
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 8px 11px; border-top: 1px solid {LINE};">
          <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Invoice Date</span>
          <span style="font-size: 12.5px; color: #111; text-align: center; font-weight: 500;">{issue_date}</span>
          <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">تاريخ الفاتورة</span>
        </div>
      </div>
      <div>
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 8px 11px;">
          <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Payment Method</span>
          <span style="font-size: 12.5px; color: #111; text-align: center; font-weight: 700;" dir="rtl">{payment}</span>
          <span style="font-size: 12px; color: #666; font-weight: 500;" dir="rtl">طريقة الدفع والسداد</span>
        </div>
        <div style="padding: 8px 11px; border-top: 1px solid {LINE}; min-height: 17px;"></div>
      </div>
    </div>

    <!-- ===== ITEMS TABLE ===== -->
    <table style="width: 100%; border-collapse: collapse; margin-top: 16px; table-layout: fixed;">
      <thead>
        <tr style="background: {ACCENT}; color: #ffffff;">
          <th style="width: 4%; padding: 6px 3px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div style="font-size: 12px; font-weight: 700;">#</div>
          </th>
          <th style="width: 29%; padding: 6px 6px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div dir="rtl" style="font-size: 11.5px; font-weight: 700;">تفاصيل السلع والخدمات</div>
            <div style="font-size: 7.5px; font-weight: 500; opacity: .92; letter-spacing: .2px;">NATURE OF GOODS OR SERVICES</div>
          </th>
          <th style="width: 9%; padding: 6px 3px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div dir="rtl" style="font-size: 11.5px; font-weight: 700;">الكميه</div>
            <div style="font-size: 7.5px; font-weight: 500; opacity: .92;">QUANTITY</div>
          </th>
          <th style="width: 11%; padding: 6px 3px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div dir="rtl" style="font-size: 11.5px; font-weight: 700;">السعر</div>
            <div style="font-size: 7.5px; font-weight: 500; opacity: .92;">UNIT PRICE</div>
          </th>
          <th style="width: 15%; padding: 6px 3px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div dir="rtl" style="font-size: 11px; font-weight: 700;">المبلغ الخاضع للضريبة</div>
            <div style="font-size: 7.5px; font-weight: 500; opacity: .92;">TAXABLE AMOUNT</div>
          </th>
          <th style="width: 15%; padding: 6px 3px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div dir="rtl" style="font-size: 11px; font-weight: 700;">الضريبة @ نسبة الضريبة</div>
            <div style="font-size: 7.5px; font-weight: 500; opacity: .92;">TAX @ TAX RATE</div>
          </th>
          <th style="width: 16%; padding: 6px 3px; border: 1px solid rgba(255,255,255,.4); text-align: center; vertical-align: middle;">
            <div dir="rtl" style="font-size: 11.5px; font-weight: 700;">المجموع</div>
            <div style="font-size: 7.5px; font-weight: 500; opacity: .92;">Total</div>
          </th>
        </tr>
      </thead>
      <tbody>{_item_rows(lines)}
      </tbody>
    </table>

    <!-- ===== TOTALS + AMOUNT IN WORDS ===== -->
    <!-- `keep`: the panel moves to the next page whole rather than being split
         across the break — see the rule in the <style> block. -->
    <div class="keep" style="display: flex; justify-content: flex-end; margin-top: 18px;">
      <div style="width: 63%; border: 1px solid {LINE}; border-radius: 3px; overflow: hidden;">
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 12px;">
          <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Subtotal Excl. VAT</span>
          <span style="font-size: 12.5px; color: #111; text-align: center; font-weight: 600;">{_money(subtotal)}</span>
          <span style="font-size: 11.5px; color: #444; font-weight: 500;" dir="rtl">إجمالي القيمة بدون الضريبة</span>
        </div>
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 12px; border-top: 1px solid {LINE};">
          <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Taxable Amount</span>
          <span style="font-size: 12.5px; color: #111; text-align: center; font-weight: 600;">{_money(subtotal)}</span>
          <span style="font-size: 11.5px; color: #444; font-weight: 500;" dir="rtl">المبلغ الخاضع للضريبة</span>
        </div>
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 7px 12px; border-top: 1px solid {LINE};">
          <span style="font-size: 10.5px; color: #666; font-weight: 500; white-space: nowrap;">Total VAT (SAR)</span>
          <span style="font-size: 12.5px; color: #111; text-align: center; font-weight: 600;">{_money(vat_total)}</span>
          <span style="font-size: 11.5px; color: #444; font-weight: 500;" dir="rtl">إجمالي الضريبة (ر.س)</span>
        </div>
        <div style="display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; padding: 9px 12px; border-top: 2px solid {ACCENT}; background: {accent_soft};">
          <span style="font-size: 10.5px; color: #333; font-weight: 700; white-space: nowrap;">Total Including VAT (SAR)</span>
          <span style="font-size: 14px; color: #111; text-align: center; font-weight: 800;">{_money(grand_total)}</span>
          <span style="font-size: 12px; color: {ACCENT}; font-weight: 800;" dir="rtl">المجموع مع الضريبة (ر.س)</span>
        </div>
        <div style="padding: 8px 12px; border-top: 1px solid {LINE};">
          <div style="font-size: 11px; color: #666; font-weight: 700; text-align: right; margin-bottom: 3px;" dir="rtl">تدفع بالكلمات</div>
          <div style="font-size: 12.5px; color: #1a1a1a; text-align: right; font-weight: 500; line-height: 1.5;" dir="rtl">{words}</div>
        </div>
      </div>
    </div>

    </td></tr></tbody>
  </table>
  </div>
</body>
</html>"""


# ======================================================================
# PDF page layout
# ======================================================================
def _page_layout() -> QPageLayout:
    """Letter portrait with zero margins — matches the 816×1056 design sheet."""
    return QPageLayout(
        QPageSize(QPageSize.Letter),
        QPageLayout.Portrait,
        QMarginsF(0, 0, 0, 0),
    )


def _default_pdf_name(data: dict[str, Any]) -> str:
    number = str(data.get("invoice_number") or "").strip() or "invoice"
    safe = "".join(ch for ch in number if ch.isalnum() or ch in ("-", "_")) or "invoice"
    return f"فاتورة_{safe}.pdf"


# ======================================================================
# Template chooser (النموذج الأول → الثامن)
# ======================================================================
_DEFAULT_TEMPLATE_OPTIONS: tuple[str, ...] = (
    "النموذج الأول",
    "النموذج الثاني",
    "النموذج الثالث",
    "النموذج الرابع",
    "النموذج الخامس",
    "النموذج السادس",
    "النموذج السابع",
    "النموذج الثامن",
)

# Button colour per option position (cycled if there are more options than kinds).
_CHOICE_BUTTON_KINDS: tuple[str, ...] = ("green", "blue", "gray")


class InvoiceTemplateChoiceDialog(QDialog):
    """A minimal dialog that lets the user pick which print layout to use.

    Works for any number of templates (2, 3, …). ``choice`` holds the selected
    template's 1-based index after :meth:`exec` returns Accepted — ``1`` for the
    first option, ``2`` for the second, and so on.
    """

    def __init__(
        self,
        action_label: str,
        parent: QWidget | None = None,
        options: "tuple[str, ...] | list[str]" = _DEFAULT_TEMPLATE_OPTIONS,
    ) -> None:
        super().__init__(parent)
        self.choice: int | None = None
        self.setWindowTitle(action_label)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        from PySide6.QtWidgets import QLabel

        heading = QLabel(f"اختر النموذج المطلوب لـ{action_label}")
        heading.setStyleSheet("font-size:14px; font-weight:700; color:#111827;")
        heading.setAlignment(Qt.AlignRight)
        root.addWidget(heading)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        for index, label in enumerate(options, start=1):
            button = QPushButton(label)
            button.setFixedHeight(44)
            button.setMinimumWidth(150)
            button.setCursor(Qt.PointingHandCursor)
            style_button(button, _CHOICE_BUTTON_KINDS[(index - 1) % len(_CHOICE_BUTTON_KINDS)])
            button.clicked.connect(lambda _checked=False, i=index: self._pick(i))
            buttons.addWidget(button)
        root.addLayout(buttons)

        cancel_button = QPushButton("إلغاء")
        cancel_button.setFixedHeight(34)
        cancel_button.setCursor(Qt.PointingHandCursor)
        style_button(cancel_button, "white")
        cancel_button.clicked.connect(self.reject)
        root.addWidget(cancel_button, alignment=Qt.AlignLeft)

    def _pick(self, choice: int) -> None:
        self.choice = choice
        self.accept()


def choose_invoice_template(
    parent: QWidget,
    action_label: str,
    options: "tuple[str, ...] | list[str]" = _DEFAULT_TEMPLATE_OPTIONS,
) -> int | None:
    """Prompt for a template; return its 1-based index or ``None`` if cancelled."""
    dialog = InvoiceTemplateChoiceDialog(action_label, parent, options)
    if dialog.exec() == QDialog.Accepted:
        return dialog.choice
    return None


# ======================================================================
# Preview dialog
# ======================================================================
class SaudiInvoicePreviewDialog(QDialog):
    """On-screen preview of the printable invoice, with export/print/close."""

    def __init__(
        self,
        data: dict[str, Any],
        parent: QWidget | None = None,
        html_builder: Callable[[dict[str, Any]], str] = build_invoice_html,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._html_builder = html_builder
        self.setWindowTitle("معاينة الفاتورة")
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
        self.view.setStyleSheet("background:#e9e9ea; border:1px solid #E5E7EB; border-radius:8px;")
        self.view.setHtml(html_builder(data), QUrl.fromLocalFile(str(_ASSETS_DIR) + "/"))
        root.addWidget(self.view, 1)

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "حفظ الفاتورة PDF", _default_pdf_name(self._data), "PDF (*.pdf)"
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
                QMessageBox.information(self, "تم", f"تم تصدير الفاتورة إلى:\n{file_path}")
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
        printer.setPageSize(QPageSize(QPageSize.Letter))
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
# Direct PDF export (toolbar button, no preview window)
# ======================================================================
def export_invoice_to_pdf(
    parent: QWidget,
    data: dict[str, Any],
    html_builder: Callable[[dict[str, Any]], str] = build_invoice_html,
) -> None:
    """Ask for a path and render the invoice straight to a PDF file.

    Renders the chosen template's markup off-screen in a ``QWebEnginePage`` and
    prints it once loading settles. The page is parented so it lives until the
    async PDF write completes.
    """
    path, _ = QFileDialog.getSaveFileName(
        parent, "حفظ الفاتورة PDF", _default_pdf_name(data), "PDF (*.pdf)"
    )
    if not path:
        return

    from PySide6.QtWebEngineCore import QWebEnginePage

    page = QWebEnginePage(parent)
    # Hold a reference on the parent so the page is not garbage-collected mid-print.
    holder = getattr(parent, "_pdf_export_pages", None)
    if holder is None:
        holder = []
        parent._pdf_export_pages = holder  # type: ignore[attr-defined]
    holder.append(page)

    def _cleanup() -> None:
        try:
            holder.remove(page)
        except ValueError:
            pass
        page.deleteLater()

    def _on_pdf(file_path: str, success: bool) -> None:
        if success:
            QMessageBox.information(parent, "تم", f"تم تصدير الفاتورة إلى:\n{file_path}")
        else:
            QMessageBox.warning(parent, "تعذّر التصدير", "حدث خطأ أثناء إنشاء ملف PDF.")
        _cleanup()

    def _on_load(ok: bool) -> None:
        if not ok:
            QMessageBox.warning(parent, "تعذّر التصدير", "تعذّر تجهيز مستند الفاتورة.")
            _cleanup()
            return
        # Give fonts/layout a beat to settle before printing.
        QTimer.singleShot(150, lambda: page.printToPdf(path, _page_layout()))

    page.pdfPrintingFinished.connect(_on_pdf)
    page.loadFinished.connect(_on_load)
    page.setHtml(html_builder(data), QUrl.fromLocalFile(str(_ASSETS_DIR) + "/"))


__all__ = [
    "build_invoice_html",
    "amount_in_words_ar",
    "qr_data_uri",
    "qr_vector_data_uri",
    "company_logo_data_uri",
    "SIGNATURE_TEXT",
    "signature_line_html",
    "SaudiInvoicePreviewDialog",
    "export_invoice_to_pdf",
    "InvoiceTemplateChoiceDialog",
    "choose_invoice_template",
]
