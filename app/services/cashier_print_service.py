"""Print logic for the Cashier/POS thermal receipt (no Qt here — fully testable).

Responsibilities (kept out of the screen and the renderer):

* load a printable invoice and validate it (status, immutable totals, seller);
* build a display model sourced **only** from the invoice snapshots and line
  snapshots (never from the current products / company legal text), so a reprint
  reproduces the original document exactly;
* encode / decode / validate the ZATCA Phase-2 QR as **binary TLV → Base64**
  (tags 1–9, UTF-8 byte lengths, no separators, deterministic);
* gate the production QR behind a strict prerequisite check — and when the
  cryptographic inputs are missing, refuse to fabricate one;
* record a successful print (updates only ``print_count`` / ``last_printed_at``
  / ``zatca_qr_base64``).

IMPORTANT — ZATCA honesty
-------------------------
A Phase-2 simplified-invoice QR needs tags 6–9 (invoice hash, ECDSA signature,
public key, and the CA signature of the stamp). Those values are produced at
issue time by :mod:`app.services.cashier_zatca_signing` from a **local test-only
key**, not from a ZATCA-onboarded EGS credential (CSID) — the invoice therefore
scans as structurally Phase-2 and self-consistent but is **not** valid for ZATCA
clearance/reporting, and every receipt carrying such a QR prints that fact.

This module still never invents tag values: it reads them from the invoice's
stored columns. :func:`check_zatca_prerequisites` reports exactly what is missing
and :func:`build_zatca_tlv_bytes` refuses to run until every input is present, so
an unsigned invoice gets an honest "QR unavailable" notice rather than a
fabricated or truncated five-field code labelled Phase-2.
"""

from __future__ import annotations

import base64
import datetime
from decimal import Decimal
from typing import Any

from app.models.cashier_invoice import (
    CURRENCY_CODE,
    MONEY_QUANT,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_ISSUED,
)
from app.repositories.cashier_repository import CashierRepository
from app.services.cashier_zatca_signing import (
    DEFAULT_SELLER_NAME_AR,
    DEFAULT_SELLER_VAT,
    DEFAULTS_NOTICE_AR,
    PREVIEW_QR_NOTICE_AR,
    TEST_STAMP_NOTICE_AR,
    TEST_STAMP_NOTICE_EN,
    CashierZatcaSigner,
    build_preview_header,
    seller_from_company,
)

DRAFT_WATERMARK = "مسودة - غير صالحة كفاتورة ضريبية\nDRAFT - NOT A VALID TAX INVOICE"
NO_QR_WATERMARK = "نسخة اختبار - QR المرحلة الثانية غير متاح"

# ZATCA Phase-2 QR tags in their mandated order.
TAG_SELLER_NAME = 1
TAG_SELLER_VAT = 2
TAG_TIMESTAMP = 3
TAG_TOTAL_WITH_VAT = 4
TAG_VAT_TOTAL = 5
TAG_INVOICE_HASH = 6
TAG_SIGNATURE = 7
TAG_PUBLIC_KEY = 8
TAG_CA_SIGNATURE = 9
QR_TAG_ORDER = (1, 2, 3, 4, 5, 6, 7, 8, 9)


class CashierPrintError(Exception):
    """Base for print errors; ``message`` is Arabic and safe to show as-is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CashierPrintValidationError(CashierPrintError):
    """Invoice is not printable (wrong status, empty, totals mismatch, ...)."""


class CashierQrError(CashierPrintError):
    """The QR TLV is invalid or fails validation."""


class ZatcaPrerequisitesMissing(CashierPrintError):
    """Required Phase-2 cryptographic inputs are absent — no production QR."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        detail = "، ".join(missing)
        super().__init__(
            "لا يمكن طباعة الفاتورة كفاتورة متوافقة مع المرحلة الثانية لأن بيانات "
            "التوقيع أو البصمة أو شهادة جهاز الفوترة غير مكتملة.\n"
            f"العناصر الناقصة: {detail}"
        )


# ---------------------------------------------------------------------------
# Formatting helpers (Decimal only; dot decimal separator; exact values)
# ---------------------------------------------------------------------------

def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money_str(value: Any) -> str:
    """Two-decimal money string with a dot separator (e.g. ``115.00``)."""
    return f"{_dec(value).quantize(MONEY_QUANT)}"


def qty_str(value: Any) -> str:
    """Quantity without unnecessary trailing zeros (``1``, ``2``, ``1.5``, ``0.250``)."""
    dec = _dec(value)
    normalized = dec.normalize()
    text = format(normalized, "f")
    # Decimal.normalize() can yield exponential form for integers (e.g. 2 -> 2E+0).
    if "E" in text or "e" in text:
        text = format(dec, "f").rstrip("0").rstrip(".")
    return text or "0"


def iso_timestamp(value: Any) -> str:
    """ISO 8601 timestamp for QR tag 3, taken from the invoice datetime."""
    if isinstance(value, datetime.datetime):
        return value.replace(microsecond=0).isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Binary TLV (ZATCA): tag (1 byte) + length (1 byte, UTF-8 byte count) + value
# ---------------------------------------------------------------------------

def _tlv_record(tag: int, value: bytes) -> bytes:
    if not 1 <= tag <= 255:
        raise CashierQrError(f"وسم TLV غير صالح: {tag}")
    length = len(value)
    if length > 255:
        # Standard ZATCA tags use a single-byte length; a longer value signals a
        # wrong (non-binary) encoding upstream.
        raise CashierQrError(f"طول قيمة الوسم {tag} يتجاوز 255 بايت.")
    return bytes([tag, length]) + value


def encode_tlv(tag_values: list[tuple[int, bytes]]) -> bytes:
    """Concatenate TLV records with NO separators/padding between them."""
    return b"".join(_tlv_record(tag, value) for tag, value in tag_values)


def decode_tlv(raw: bytes) -> list[tuple[int, bytes]]:
    """Parse sequential TLV records; raise on any truncation/overrun."""
    out: list[tuple[int, bytes]] = []
    i, n = 0, len(raw)
    while i < n:
        if i + 2 > n:
            raise CashierQrError("بنية TLV مقطوعة: ترويسة غير مكتملة.")
        tag = raw[i]
        length = raw[i + 1]
        i += 2
        if i + length > n:
            raise CashierQrError("طول TLV يتجاوز البيانات الفعلية.")
        out.append((tag, raw[i:i + length]))
        i += length
    return out


def build_zatca_tlv_bytes(header: dict[str, Any]) -> bytes:
    """Build the full tags 1–9 TLV byte string from immutable invoice data.

    Assumes prerequisites are already validated (call
    :func:`check_zatca_prerequisites` first). Values are UTF-8 encoded so the
    length is the true byte count, not the Python character count.
    """
    tag_values = [
        (TAG_SELLER_NAME, str(header["seller_name_snapshot"]).encode("utf-8")),
        (TAG_SELLER_VAT, str(header["seller_vat_number_snapshot"]).encode("utf-8")),
        (TAG_TIMESTAMP, iso_timestamp(header["invoice_datetime"]).encode("utf-8")),
        (TAG_TOTAL_WITH_VAT, money_str(header["grand_total"]).encode("utf-8")),
        (TAG_VAT_TOTAL, money_str(header["vat_amount"]).encode("utf-8")),
        (TAG_INVOICE_HASH, str(header["zatca_invoice_hash"]).encode("utf-8")),
        (TAG_SIGNATURE, str(header["zatca_signature"]).encode("utf-8")),
        (TAG_PUBLIC_KEY, str(header["zatca_public_key"]).encode("utf-8")),
        (TAG_CA_SIGNATURE, str(header["zatca_cryptographic_stamp"]).encode("utf-8")),
    ]
    return encode_tlv(tag_values)


def build_zatca_qr_base64(header: dict[str, Any]) -> str:
    """Deterministic Base64(TLV) production QR value for an eligible invoice."""
    return base64.b64encode(build_zatca_tlv_bytes(header)).decode("ascii")


def decode_and_validate_zatca_qr(qr_base64: str, header: dict[str, Any]) -> list[tuple[int, bytes]]:
    """Decode the Base64 QR and validate it against the immutable invoice.

    Checks (raise :class:`CashierQrError` on any failure): valid Base64; valid
    sequential TLV; tags 1–9 present exactly once and in order; each length
    equals the value byte length; Arabic seller name round-trips through UTF-8;
    tags 2–9 match their invoice sources; and re-encoding the same invoice yields
    the identical Base64 (determinism / no stray bytes).
    """
    try:
        raw = base64.b64decode(qr_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise CashierQrError("قيمة QR ليست Base64 صالحة.") from exc

    records = decode_tlv(raw)
    tags = [t for t, _v in records]
    if tags != list(QR_TAG_ORDER):
        raise CashierQrError(
            "وسوم QR يجب أن تكون من 1 إلى 9 مرة واحدة وبالترتيب الصحيح."
        )
    values = {t: v for t, v in records}

    # Length integrity is guaranteed by decode_tlv; verify semantic matches.
    def _u(tag: int) -> str:
        try:
            return values[tag].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CashierQrError(f"قيمة الوسم {tag} ليست UTF-8 صالحة.") from exc

    if _u(TAG_SELLER_VAT) != str(header["seller_vat_number_snapshot"]):
        raise CashierQrError("الرقم الضريبي في QR لا يطابق الفاتورة.")
    if _u(TAG_TIMESTAMP) != iso_timestamp(header["invoice_datetime"]):
        raise CashierQrError("توقيت QR لا يطابق توقيت إصدار الفاتورة.")
    if _u(TAG_TOTAL_WITH_VAT) != money_str(header["grand_total"]):
        raise CashierQrError("إجمالي QR لا يطابق الإجمالي شامل الضريبة.")
    if _u(TAG_VAT_TOTAL) != money_str(header["vat_amount"]):
        raise CashierQrError("ضريبة QR لا تطابق قيمة الضريبة.")
    if _u(TAG_INVOICE_HASH) != str(header["zatca_invoice_hash"]):
        raise CashierQrError("بصمة الفاتورة في QR لا تطابق المصدر.")
    if _u(TAG_SIGNATURE) != str(header["zatca_signature"]):
        raise CashierQrError("التوقيع في QR لا يطابق المصدر.")
    if _u(TAG_PUBLIC_KEY) != str(header["zatca_public_key"]):
        raise CashierQrError("المفتاح العام في QR لا يطابق المصدر.")
    if _u(TAG_CA_SIGNATURE) != str(header["zatca_cryptographic_stamp"]):
        raise CashierQrError("توقيع الجهة المصدقة في QR لا يطابق المصدر.")
    # Arabic seller name must survive the round-trip.
    _u(TAG_SELLER_NAME)

    # Determinism: regenerating from the same immutable data gives the same value.
    if build_zatca_qr_base64(header) != qr_base64:
        raise CashierQrError("إعادة توليد QR من نفس الفاتورة أعطت قيمة مختلفة.")
    return records


# ---------------------------------------------------------------------------
# Prerequisite gating
# ---------------------------------------------------------------------------

def check_zatca_prerequisites(header: dict[str, Any]) -> list[str]:
    """Return the Arabic names of missing Phase-2 QR inputs (empty = all present)."""
    checks: list[tuple[Any, str]] = [
        (header.get("seller_name_snapshot"), "اسم البائع"),
        (header.get("seller_vat_number_snapshot"), "الرقم الضريبي للبائع"),
        (header.get("invoice_datetime"), "توقيت إصدار الفاتورة"),
        (header.get("grand_total"), "الإجمالي شامل الضريبة"),
        (header.get("vat_amount"), "قيمة الضريبة"),
        (header.get("zatca_invoice_hash"), "بصمة الفاتورة (Tag 6)"),
        (header.get("zatca_signature"), "التوقيع الرقمي (Tag 7)"),
        (header.get("zatca_public_key"), "المفتاح العام (Tag 8)"),
        (header.get("zatca_cryptographic_stamp"), "توقيع الجهة المصدقة (Tag 9)"),
    ]
    missing: list[str] = []
    for value, label in checks:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(label)
    return missing


class CashierPrintService:
    def __init__(self, repository: CashierRepository | None = None) -> None:
        self.repository = repository or CashierRepository()
        self._signer = CashierZatcaSigner(self.repository)

    # -- load ---------------------------------------------------------------
    def load_printable_cashier_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Load the invoice, generating its Phase-2 QR data first if it lacks it.

        The signing call is idempotent (see
        :meth:`CashierZatcaSigner.ensure_signed`), so an invoice issued before
        this feature existed gets signed on its first print while an already
        signed one keeps its original QR on every reprint. A signing failure is
        never fatal — the receipt then reports the QR as unavailable.
        """
        try:
            invoice = self._signer.ensure_signed(int(invoice_id))
        except Exception:  # noqa: BLE001 - fall back to the unsigned invoice
            invoice = self.repository.load_invoice(int(invoice_id))
        if invoice is None:
            raise CashierPrintValidationError("الفاتورة غير موجودة.")
        return invoice

    # -- validation ---------------------------------------------------------
    def validate_invoice_for_print(
        self, invoice: dict[str, Any], *, allow_draft_preview: bool = False
    ) -> None:
        header = invoice["header"]
        status = header["invoice_status"]
        if status == STATUS_CANCELLED:
            raise CashierPrintValidationError("لا يمكن طباعة فاتورة ملغاة.")
        if status == STATUS_DRAFT and not allow_draft_preview:
            raise CashierPrintValidationError(
                "لا يمكن طباعة مسودة كفاتورة ضريبية رسمية. أصدر الفاتورة أولًا."
            )
        if status not in (STATUS_ISSUED, STATUS_DRAFT):
            raise CashierPrintValidationError("حالة الفاتورة لا تسمح بالطباعة.")
        if not invoice["lines"]:
            raise CashierPrintValidationError("لا يمكن طباعة فاتورة بدون أصناف.")
        if not (header.get("seller_name_snapshot") or "").strip() and status == STATUS_ISSUED:
            raise CashierPrintValidationError("بيانات البائع (اللقطة) غير متوفرة على الفاتورة.")

    def validate_invoice_totals(self, invoice: dict[str, Any]) -> None:
        """Header totals must equal the sum of the immutable lines (never mutate)."""
        header = invoice["header"]
        lines = invoice["lines"]
        sub = sum((_dec(l["line_subtotal"]) for l in lines), start=Decimal("0"))
        vat = sum((_dec(l["vat_amount"]) for l in lines), start=Decimal("0"))
        total = sum((_dec(l["line_total"]) for l in lines), start=Decimal("0"))
        q = MONEY_QUANT
        if (
            sub.quantize(q) != _dec(header["subtotal"]).quantize(q)
            or vat.quantize(q) != _dec(header["vat_amount"]).quantize(q)
            or total.quantize(q) != _dec(header["grand_total"]).quantize(q)
        ):
            raise CashierPrintValidationError(
                "تعارض في الإجماليات: مجموع السطور لا يساوي إجمالي الفاتورة. تم إيقاف الطباعة."
            )

    # -- QR -----------------------------------------------------------------
    def build_or_load_zatca_qr(self, header: dict[str, Any]) -> str:
        """Return a validated production QR Base64, or raise with what's missing.

        Never fabricates: if any Phase-2 input is absent it raises
        :class:`ZatcaPrerequisitesMissing` listing them.
        """
        missing = check_zatca_prerequisites(header)
        if missing:
            raise ZatcaPrerequisitesMissing(missing)
        qr = build_zatca_qr_base64(header)
        decode_and_validate_zatca_qr(qr, header)  # self-check before use
        return qr

    # -- receipt model ------------------------------------------------------
    def build_cashier_receipt_model(
        self,
        invoice: dict[str, Any],
        *,
        paper_mm: int = 80,
        is_preview: bool = False,
    ) -> dict[str, Any]:
        """Assemble every display value for the receipt.

        For an ISSUED invoice every value comes from the immutable snapshots (and
        never from the current product/company legal text), so a reprint is
        identical. A DRAFT has no snapshots yet — they are only written at issue —
        so its *preview* falls back to the currently selected company, which is
        exactly the data the invoice will carry once issued. The company logo is
        presentational and always comes from the current record.
        """
        header = invoice["header"]
        status = header["invoice_status"]
        is_draft = status == STATUS_DRAFT
        is_reprint = int(header.get("print_count") or 0) > 0

        company = None
        if header.get("company_id") is not None:
            company = self.repository.get_company(int(header["company_id"]))

        # QR. An ISSUED invoice uses its own stored, signed values. A DRAFT gets a
        # clearly-labelled preview QR built from the selected company + a
        # placeholder chain position — never stored, and regenerated at issue.
        missing = check_zatca_prerequisites(header)
        is_preview_qr = False
        qr_source = header
        qr_available = status == STATUS_ISSUED and not missing
        if not qr_available and is_draft:
            preview = build_preview_header(header, invoice["lines"], company)
            if preview is not None and not check_zatca_prerequisites(preview):
                qr_source, qr_available, is_preview_qr = preview, True, True
        qr_base64 = build_zatca_qr_base64(qr_source) if qr_available else None
        if qr_available:
            missing = []

        watermark = None
        if is_draft:
            watermark = DRAFT_WATERMARK
        elif not qr_available:
            watermark = NO_QR_WATERMARK

        # The stamp behind tags 7–9 is a local test key, never a ZATCA CSID, and
        # the seller values may be documented placeholders. Both facts are
        # surfaced on the receipt rather than left for the reader to discover.
        used_defaults = (
            (qr_source.get("seller_name_snapshot") or "") == DEFAULT_SELLER_NAME_AR
            or (qr_source.get("seller_vat_number_snapshot") or "") == DEFAULT_SELLER_VAT
        )

        cashier_name = self.repository.get_user_display_name(header.get("created_by_user_id"))

        logo_data_uri = None
        if company is not None:
            logo_data_uri = _logo_data_uri(company.get("logo"), company.get("logo_mime"))

        # Seller block: snapshots for an issued invoice; the live company for a
        # draft preview (its snapshots are still NULL).
        seller_source = header
        if is_draft and company is not None:
            seller_source = {**header, **_draft_seller(header, company)}

        lines = []
        for line in invoice["lines"]:
            lines.append({
                "name": line["product_name_snapshot"] or "",
                "qty": qty_str(line["quantity"]),
                "unit_price": money_str(line["unit_price"]),
                "line_subtotal": money_str(line["line_subtotal"]),
                "vat_rate": f"{_dec(line['vat_rate']).normalize():f}",
                "vat_amount": money_str(line["vat_amount"]),
                "line_total": money_str(line["line_total"]),
            })

        return {
            "paper_mm": paper_mm,
            "is_preview": is_preview,
            "is_draft": is_draft,
            "is_reprint": is_reprint,
            "watermark": watermark,
            "title_ar": "فاتورة ضريبية مبسطة",
            "title_en": "Simplified Tax Invoice",
            "logo_data_uri": logo_data_uri,
            "seller": {
                "name_ar": seller_source.get("seller_name_snapshot") or "",
                "address": seller_source.get("seller_address_snapshot") or "",
                "cr": seller_source.get("seller_commercial_registration_snapshot") or "",
                "vat": seller_source.get("seller_vat_number_snapshot") or "",
            },
            "invoice": {
                "number": header.get("invoice_number") or "",
                "date": _date_str(header.get("invoice_date")),
                "time": _time_str(header.get("invoice_time")),
                "currency": header.get("currency_code") or CURRENCY_CODE,
                "cashier": cashier_name or "",
                "uuid": str(header["invoice_uuid"]) if header.get("invoice_uuid") else "",
            },
            "buyer": {
                "name": header.get("buyer_name_snapshot") or "",
                "vat": header.get("buyer_vat_number_snapshot") or "",
            },
            "lines": lines,
            "totals": {
                "subtotal": money_str(header["subtotal"]),
                "vat": money_str(header["vat_amount"]),
                "grand": money_str(header["grand_total"]),
            },
            "qr": {
                "available": qr_available,
                "base64": qr_base64,
                "missing": missing,
                "test_stamp": qr_available,
                "test_stamp_notice_ar": TEST_STAMP_NOTICE_AR,
                "test_stamp_notice_en": TEST_STAMP_NOTICE_EN,
                "used_defaults": used_defaults,
                "defaults_notice_ar": DEFAULTS_NOTICE_AR,
                "is_preview": is_preview_qr,
                "preview_notice_ar": PREVIEW_QR_NOTICE_AR,
            },
            "footer_ar": "شكرًا لزيارتكم",
            "footer_en": "Thank you",
        }

    # -- print tracking -----------------------------------------------------
    def record_successful_print(
        self, invoice_id: int, qr_base64: str | None = None
    ) -> dict[str, Any]:
        """Bump print_count / last_printed_at (and persist a validated QR).

        Call ONLY after the print job was actually accepted. When ``qr_base64`` is
        supplied it must already be validated; it is stored in ``zatca_qr_base64``.
        """
        return self.repository.record_print(int(invoice_id), qr_base64)


# ---------------------------------------------------------------------------
# small presentational helpers
# ---------------------------------------------------------------------------

def _date_str(value: Any) -> str:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value or "")


def _time_str(value: Any) -> str:
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    if isinstance(value, datetime.datetime):
        return value.strftime("%H:%M")
    return str(value or "")


def _draft_seller(header: dict[str, Any], company: dict[str, Any]) -> dict[str, Any]:
    """Fill a draft's empty seller fields from the selected company record.

    Only ever fills what is genuinely blank, so a value already on the invoice
    always wins. The address / CR are presentational; the name and VAT number
    also feed the preview QR's tags 1–2.
    """
    filled = dict(seller_from_company(company))
    filled["seller_commercial_registration_snapshot"] = (
        company.get("commercial_registration") or ""
    ).strip() or None
    filled["seller_address_snapshot"] = company.get("address_ar") or None
    return {
        key: value
        for key, value in filled.items()
        if value and not (header.get(key) or "").strip()
    }


def _logo_data_uri(logo: Any, mime: Any) -> str | None:
    if not logo:
        return None
    try:
        raw = bytes(logo)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    mime_str = (mime or "image/png") if isinstance(mime, str) else "image/png"
    return f"data:{mime_str};base64,{base64.b64encode(raw).decode('ascii')}"


__all__ = [
    "CashierPrintService",
    "CashierPrintError",
    "CashierPrintValidationError",
    "CashierQrError",
    "ZatcaPrerequisitesMissing",
    "check_zatca_prerequisites",
    "build_zatca_qr_base64",
    "build_zatca_tlv_bytes",
    "encode_tlv",
    "decode_tlv",
    "decode_and_validate_zatca_qr",
    "money_str",
    "qty_str",
    "iso_timestamp",
]
