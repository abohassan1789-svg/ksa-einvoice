"""Thermal (80 mm) Cashier receipt renderer + preview/print.

Reuses the project's existing print framework — HTML rendered in a
``QWebEngineView`` (Chromium shapes Arabic / handles RTL) and printed through
``QPrinter`` — rather than introducing a second printing stack. The **same**
HTML/renderer is used for the on-screen preview, the PDF export and the physical
print, so what you see is what prints.

Key thermal behaviours:

* 80 mm paper width (58 mm supported by passing ``paper_mm=58``);
* the receipt height grows with the number of lines — the page height is
  measured from the rendered content (``scrollHeight``) so there is no fixed
  height and no blank second page;
* the QR (when a *validated production* value exists) is rendered as a crisp
  black-on-white square with a ≥4-module quiet zone; when the Phase-2 QR is not
  available the area shows a clear notice instead of a fabricated code.

Print-count tracking is delegated to :class:`CashierPrintService` and only runs
after the print job reports success.
"""

from __future__ import annotations

import base64
import io
from html import escape
from typing import Any, Callable

from PySide6.QtCore import QMarginsF, QSizeF, Qt
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

from app.services.cashier_print_service import CashierPrintService
from app.ui.common.saudi_invoice_style import si_icon, style_button

# Thermal print reference DPI (most 80 mm printers are 203 dpi).
THERMAL_DPI = 203
QR_TARGET_MM_80 = 38
QR_TARGET_MM_58 = 32
QR_QUIET_MODULES = 4  # ZATCA/ISO minimum quiet zone


# ---------------------------------------------------------------------------
# QR image (only ever called with a validated production Base64 value)
# ---------------------------------------------------------------------------

def render_qr_data_uri(qr_base64: str, *, paper_mm: int = 80) -> str:
    """Render the QR Base64 payload to a sharp black-on-white PNG data URI.

    Integer module scaling + a 4-module quiet zone, no logo/colour/gradient/
    transparency, perfect square. Sized for a crisp thermal print at
    :data:`THERMAL_DPI`. The QR *content* is the exact validated TLV Base64.
    """
    import qrcode  # optional dependency (present in this project)

    target_mm = QR_TARGET_MM_80 if paper_mm >= 80 else QR_TARGET_MM_58
    target_px = target_mm / 25.4 * THERMAL_DPI

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=QR_QUIET_MODULES,
    )
    qr.add_data(qr_base64)
    qr.make(fit=True)
    modules = qr.modules_count + 2 * QR_QUIET_MODULES
    # Whole printer pixels per module (never smooth-scale a small bitmap up).
    box = max(1, round(target_px / modules))
    qr.box_size = box
    img = qr.make_image(fill_color="black", back_color="white").convert("1")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# HTML builder (pure — testable without Qt)
# ---------------------------------------------------------------------------

def build_cashier_receipt_html(model: dict[str, Any], qr_data_uri: str | None = None) -> str:
    """Build the thermal receipt HTML for ``model`` (from the print service)."""
    paper_mm = int(model.get("paper_mm", 80))
    seller = model["seller"]
    inv = model["invoice"]
    buyer = model["buyer"]

    def row(label_ar: str, value: str) -> str:
        if not value:
            return ""
        return (
            f'<div class="kv"><span class="k">{escape(label_ar)}</span>'
            f'<span class="v">{escape(value)}</span></div>'
        )

    header_bits = []
    if model.get("logo_data_uri"):
        header_bits.append(f'<img class="logo" src="{model["logo_data_uri"]}" alt="logo">')
    if seller["name_ar"]:
        header_bits.append(f'<div class="seller-name">{escape(seller["name_ar"])}</div>')
    if seller["address"]:
        header_bits.append(f'<div class="seller-line">{escape(seller["address"])}</div>')
    if seller["cr"]:
        header_bits.append(f'<div class="seller-line">س.ت: {escape(seller["cr"])}</div>')
    if seller["vat"]:
        header_bits.append(f'<div class="seller-line">الرقم الضريبي: {escape(seller["vat"])}</div>')

    reprint_tag = (
        '<div class="reprint">نسخة معاد طباعتها / REPRINT</div>'
        if model.get("is_reprint") else ""
    )
    watermark = (
        f'<div class="watermark">{escape(model["watermark"]).replace(chr(10), "<br>")}</div>'
        if model.get("watermark") else ""
    )

    lines_html = []
    for line in model["lines"]:
        lines_html.append(
            f'<div class="item">'
            f'<div class="item-name">{escape(line["name"])}</div>'
            f'<div class="item-calc"><span>{escape(line["qty"])} × {escape(line["unit_price"])}</span>'
            f'<span class="item-total">{escape(line["line_total"])}</span></div>'
            f'<div class="item-vat"><span>ضريبة {escape(line["vat_rate"])}%</span>'
            f'<span>{escape(line["vat_amount"])}</span></div>'
            f'</div>'
        )

    cur = escape(inv["currency"] or "SAR")
    totals = model["totals"]
    totals_html = (
        f'<div class="trow"><span>الإجمالي قبل الضريبة / Subtotal</span>'
        f'<span>{escape(totals["subtotal"])} {cur}</span></div>'
        f'<div class="trow"><span>ضريبة القيمة المضافة / VAT</span>'
        f'<span>{escape(totals["vat"])} {cur}</span></div>'
        f'<div class="trow grand"><span>الإجمالي شامل الضريبة / Total</span>'
        f'<span>{escape(totals["grand"])} {cur}</span></div>'
    )

    if model["qr"]["available"] and qr_data_uri:
        qr = model["qr"]
        # NOTE: the QR is signed by a local test key, not a ZATCA-onboarded CSID
        # (see app.services.cashier_zatca_signing). The receipt used to print that
        # fact here; it was removed at the user's explicit request, so nothing on
        # the printed page distinguishes this from an approved stamp. The model
        # still carries `qr["test_stamp"]` if it ever needs surfacing again.
        defaults_html = (
            f'<div class="qr-notice">{escape(qr["defaults_notice_ar"])}</div>'
            if qr.get("used_defaults") else ""
        )
        # A draft's QR is rebuilt at issue, so never let it read as the final one.
        notice_html = (
            f'<div class="qr-notice">{escape(qr["preview_notice_ar"])}</div>'
            if qr.get("is_preview") else ""
        )
        qr_html = (
            '<div class="qr-wrap">'
            f'<img class="qr" src="{qr_data_uri}" alt="ZATCA QR">'
            '<div class="qr-label">امسح الرمز للتحقق من بيانات الفاتورة<br>Scan to verify invoice data</div>'
            f'{notice_html}{defaults_html}'
            '</div>'
        )
    else:
        missing = "، ".join(model["qr"].get("missing", []))
        qr_html = (
            '<div class="qr-missing">رمز QR للمرحلة الثانية غير متاح<br>'
            'بيانات التوقيع/البصمة/الشهادة غير مكتملة'
            + (f'<div class="qr-missing-detail">الناقص: {escape(missing)}</div>' if missing else "")
            + '</div>'
        )

    buyer_html = row("العميل / Customer", buyer["name"]) + row("الرقم الضريبي للعميل", buyer["vat"])
    uuid_html = (
        f'<div class="uuid">UUID: {escape(inv["uuid"])}</div>' if inv["uuid"] else ""
    )

    # Printable width: paper minus a small safe padding on each side.
    body_mm = paper_mm - 6

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<style>
  @page {{ size: {paper_mm}mm auto; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: #FFFFFF; }}
  body {{
    width: {body_mm}mm; margin: 0 auto; padding: 3mm 0 4mm;
    font-family: 'Cairo', 'Tahoma', 'Arial', sans-serif; color: #000000;
    font-size: 11px; line-height: 1.45; text-align: center; direction: rtl;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  .logo {{ max-width: 26mm; max-height: 16mm; margin: 0 auto 2mm; display: block; }}
  .seller-name {{ font-size: 15px; font-weight: 800; margin-bottom: 1mm; }}
  .seller-line {{ font-size: 10px; }}
  .title-ar {{ font-size: 16px; font-weight: 800; margin: 2mm 0 0; }}
  .title-en {{ font-size: 10px; letter-spacing: .5px; margin-bottom: 1mm; }}
  .reprint {{ font-size: 11px; font-weight: 800; border: 1px solid #000; display: inline-block;
             padding: 1px 8px; margin: 1mm auto; border-radius: 3px; }}
  .watermark {{ font-size: 12px; font-weight: 800; color: #000; border: 2px dashed #000;
               padding: 2mm; margin: 2mm 0; }}
  .sep {{ border-top: 1px dashed #000; margin: 2mm 0; }}
  .kv {{ display: flex; justify-content: space-between; font-size: 10.5px; }}
  .kv .k {{ color: #000; }} .kv .v {{ font-weight: 700; }}
  .uuid {{ font-size: 8.5px; word-break: break-all; margin-top: 1mm; }}
  .items {{ text-align: right; }}
  .item {{ padding: 1mm 0; border-bottom: 1px dotted #999; }}
  .item-name {{ font-weight: 700; font-size: 11px; }}
  .item-calc, .item-vat {{ display: flex; justify-content: space-between; font-size: 10px; }}
  .item-total {{ font-weight: 800; }}
  .totals {{ margin-top: 1mm; }}
  .trow {{ display: flex; justify-content: space-between; font-size: 11px; padding: .5mm 0; }}
  .trow.grand {{ font-size: 15px; font-weight: 800; border-top: 1px solid #000; margin-top: 1mm; padding-top: 1mm; }}
  .qr-wrap {{ margin: 3mm 0 1mm; }}
  .qr {{ width: {QR_TARGET_MM_80 if paper_mm >= 80 else QR_TARGET_MM_58}mm;
         height: {QR_TARGET_MM_80 if paper_mm >= 80 else QR_TARGET_MM_58}mm;
         image-rendering: pixelated; image-rendering: crisp-edges; display: block; margin: 0 auto; }}
  .qr-label {{ font-size: 9px; margin-top: 1mm; }}
  .qr-notice {{ font-size: 8px; margin-top: 1mm; }}
  .qr-missing {{ border: 1px solid #000; padding: 2mm; margin: 3mm 0; font-size: 10px; font-weight: 700; }}
  .qr-missing-detail {{ font-size: 8.5px; font-weight: 400; margin-top: 1mm; }}
  .footer {{ margin-top: 2mm; font-size: 11px; font-weight: 700; }}
  .footer-en {{ font-size: 9px; font-weight: 400; }}
</style></head>
<body>
  {"".join(header_bits)}
  <div class="title-ar">{escape(model["title_ar"])}</div>
  <div class="title-en">{escape(model["title_en"])}</div>
  {reprint_tag}
  {watermark}
  <div class="sep"></div>
  {row("رقم الفاتورة / Invoice No.", inv["number"])}
  {row("التاريخ / Date", inv["date"])}
  {row("الوقت / Time", inv["time"])}
  {row("العملة / Currency", inv["currency"])}
  {row("الكاشير / Cashier", inv["cashier"])}
  {uuid_html}
  {('<div class="sep"></div>' + buyer_html) if (buyer["name"] or buyer["vat"]) else ""}
  <div class="sep"></div>
  <div class="items">{"".join(lines_html)}</div>
  <div class="sep"></div>
  <div class="totals">{totals_html}</div>
  {qr_html}
  <div class="sep"></div>
  <div class="footer">{escape(model["footer_ar"])}</div>
  <div class="footer-en">{escape(model["footer_en"])}</div>
</body></html>"""


def _px_to_mm(px: float) -> float:
    return px / 96.0 * 25.4  # QWebEngine lays out at 96 CSS dpi


def _thermal_layout(paper_mm: int, height_mm: float) -> QPageLayout:
    height_mm = max(40.0, height_mm)
    return QPageLayout(
        QPageSize(QSizeF(float(paper_mm), height_mm), QPageSize.Millimeter),
        QPageLayout.Portrait,
        QMarginsF(0, 0, 0, 0),
    )


# ---------------------------------------------------------------------------
# Preview dialog (same renderer used for preview / PDF / print)
# ---------------------------------------------------------------------------

class CashierReceiptPreviewDialog(QDialog):
    """Preview a Cashier receipt with export-PDF, print and close.

    The طباعة button is enabled only when the invoice is ISSUED and carries a
    validated production QR; otherwise only a clearly-watermarked test preview /
    PDF is available (never an official print with a missing/fake QR).
    """

    def __init__(
        self,
        service: CashierPrintService,
        invoice_id: int,
        model: dict[str, Any],
        qr_data_uri: str | None,
        parent: QWidget | None = None,
        on_printed: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.invoice_id = invoice_id
        self.model = model
        self.qr_data_uri = qr_data_uri
        self._on_printed = on_printed
        self._printer = None

        self.setWindowTitle("معاينة طباعة إيصال الكاشير")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(460, 900)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.print_button = QPushButton("طباعة إيصال الكاشير")
        self.print_button.setFixedHeight(36)
        self.print_button.setIcon(si_icon("fa5s.print", color="#FFFFFF"))
        style_button(self.print_button, "green")
        self.print_button.clicked.connect(self._on_print)
        can_print = bool(model["qr"]["available"]) and not model.get("is_draft")
        self.print_button.setEnabled(can_print)
        if not can_print:
            self.print_button.setToolTip(
                "الطباعة الرسمية متاحة فقط لفاتورة مُصدرة تحتوي رمز QR متوافق مع المرحلة الثانية."
            )

        self.pdf_button = QPushButton("تصدير PDF")
        self.pdf_button.setFixedHeight(36)
        self.pdf_button.setIcon(si_icon("fa5s.file-pdf", color="#FFFFFF"))
        style_button(self.pdf_button, "red")
        self.pdf_button.clicked.connect(self._on_export_pdf)

        close_button = QPushButton("إغلاق")
        close_button.setFixedHeight(36)
        close_button.setIcon(si_icon("fa5s.times", color="#374151"))
        style_button(close_button, "white")
        close_button.clicked.connect(self.reject)

        bar.addWidget(self.print_button)
        bar.addWidget(self.pdf_button)
        bar.addStretch(1)
        bar.addWidget(close_button)
        root.addLayout(bar)

        self.view = QWebEngineView(self)
        self.view.setStyleSheet("background:#e9eaec; border:1px solid #E5E7EB; border-radius:8px;")
        self.view.setHtml(build_cashier_receipt_html(model, qr_data_uri))
        root.addWidget(self.view, 1)

    # -- height measurement (dynamic receipt height, no blank page) ----------
    def _measure_then(self, after: Callable[[float], None]) -> None:
        self.view.page().runJavaScript(
            "Math.ceil(document.body.getBoundingClientRect().height)",
            0,
            lambda px: after(_px_to_mm(float(px or 400))),
        )

    def _on_export_pdf(self) -> None:
        number = "".join(c for c in self.model["invoice"]["number"] if c.isalnum() or c in "-_") or "cashier"
        path, _ = QFileDialog.getSaveFileName(self, "حفظ الإيصال PDF", f"cashier_{number}.pdf", "PDF (*.pdf)")
        if not path:
            return
        paper_mm = int(self.model.get("paper_mm", 80))

        def _do(height_mm: float) -> None:
            def _finished(file_path: str, success: bool) -> None:  # noqa: ARG001
                try:
                    self.view.page().pdfPrintingFinished.disconnect(_finished)
                except (RuntimeError, TypeError):
                    pass
                if success:
                    QMessageBox.information(self, "تم", f"تم تصدير الإيصال إلى:\n{file_path}")
                else:
                    QMessageBox.warning(self, "تعذّر التصدير", "حدث خطأ أثناء إنشاء ملف PDF.")

            self.view.page().pdfPrintingFinished.connect(_finished)
            self.view.page().printToPdf(path, _thermal_layout(paper_mm, height_mm))

        self._measure_then(_do)

    def _on_print(self) -> None:
        try:
            from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        except Exception:  # noqa: BLE001
            QMessageBox.warning(self, "الطباعة", "خدمة الطباعة غير متاحة.")
            return
        paper_mm = int(self.model.get("paper_mm", 80))

        def _do(height_mm: float) -> None:
            printer = QPrinter(QPrinter.HighResolution)
            printer.setPageLayout(_thermal_layout(paper_mm, height_mm))
            dialog = QPrintDialog(printer, self)
            if dialog.exec() != QPrintDialog.Accepted:
                return  # cancelled -> no print, no count bump
            self._printer = printer

            def _done(success: bool) -> None:
                try:
                    self.view.printFinished.disconnect(_done)
                except (RuntimeError, TypeError):
                    pass
                if not success:
                    QMessageBox.warning(self, "تعذّر الطباعة", "لم تكتمل مهمة الطباعة.")
                    return
                # Record the successful print (and persist the validated QR).
                try:
                    updated = self.service.record_successful_print(
                        self.invoice_id, self.model["qr"]["base64"]
                    )
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(
                        self, "تنبيه",
                        "تمت الطباعة لكن تعذّر تحديث عدّاد الطباعة.\n" + str(exc),
                    )
                    return
                if self._on_printed is not None:
                    self._on_printed(updated)
                QMessageBox.information(self, "تم", "تمت طباعة الإيصال بنجاح.")

            self.view.printFinished.connect(_done)
            self.view.print(printer)

        self._measure_then(_do)


__all__ = [
    "CashierReceiptPreviewDialog",
    "build_cashier_receipt_html",
    "render_qr_data_uri",
    "THERMAL_DPI",
]
