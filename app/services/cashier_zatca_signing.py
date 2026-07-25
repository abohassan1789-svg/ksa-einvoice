"""Phase-2 (ZATCA) data generation for the Cashier/POS receipt QR.

Fills the ``cashier_invoices.zatca_*`` columns at ISSUE time so the thermal
receipt can carry a full tags 1–9 QR that a Phase-2 reader parses as compatible:

* ``invoice_uuid``                — random UUID v4 (when not already set)
* ``zatca_icv``                   — monotonic per-company invoice counter
* ``zatca_previous_invoice_hash`` — the PIH chain value (genesis for the first)
* ``zatca_xml``                   — a UBL 2.1 invoice document
* ``zatca_invoice_hash``          — base64(SHA-256(xml))            → QR tag 6
* ``zatca_signature``             — ECDSA signature of that hash    → QR tag 7
* ``zatca_public_key``            — the signing public key (DER)    → QR tag 8
* ``zatca_cryptographic_stamp``   — signature over the public key   → QR tag 9

This reuses the *same* generator + signer the Saudi sales-invoice screen already
uses (:mod:`app.services.saudi_zatca_generator`,
:mod:`app.services.saudi_egs_test_signer`) rather than inventing a second
pipeline.

⚠️  HONESTY — what this is and is NOT
------------------------------------
Tags 6–9 are produced by a **local, self-signed, test-only** secp256k1 key (see
``saudi_egs_test_signer``), *not* by a ZATCA-onboarded EGS credential (CSID).
Nothing here is fabricated or hard-coded: the hash is a real SHA-256 of the real
invoice XML, and tag 7 genuinely verifies against tag 8 over tag 6 — so the QR is
**structurally Phase-2 and self-consistent**, and an offline reader (including
this project's own :func:`decode_and_validate_zatca_qr`) reads it as compatible.

But because the public key is not backed by a ZATCA certificate:

* the invoice is **NOT** valid for ZATCA clearance / reporting, and
* the ZATCA Fatoora portal / official validator will **reject** the stamp.

``zatca_status`` is therefore set to ``READY`` (locally generated), never to
``REPORTED``/``ACCEPTED``. Replacing this with a real onboarded EGS signing flow
means changing only :func:`build_cashier_zatca_fields` — the encoder, renderer,
print path and tracking stay as they are.

⚠️  The receipt used to print :data:`TEST_STAMP_NOTICE_AR` under the QR so the
printed page said this out loud. That line was **removed from the receipt at the
user's explicit request** (2026-07-15), so nothing on paper now distinguishes
this from a ZATCA-approved stamp — the caveat survives only here, in
``zatca_status``, and in ``qr["test_stamp"]``. The constants are kept for
whoever puts it back.

Placeholder seller data
-----------------------
QR tags 1 and 2 (seller name / VAT number) must be present and non-empty. When a
company record has no Arabic name or no VAT number, this module substitutes the
documented placeholders below rather than leaving the QR unbuildable, and reports
``used_defaults`` so the receipt can say so out loud. Placeholders are only ever
used where the source value is genuinely absent.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from app.models.cashier_invoice import (
    CURRENCY_CODE,
    STATUS_ISSUED,
)
from app.repositories.cashier_repository import CashierRepository
from app.services import saudi_egs_test_signer
from app.services import saudi_zatca_generator as zatca_gen

# zatca_status value for "locally generated + signed, not reported to ZATCA".
# Allowed by the ck_cashier_invoices_zatca_status CHECK in migration 015.
ZATCA_STATUS_READY = "READY"

# Placeholders used ONLY when the company record has no such value at all.
# 300000000000003 is ZATCA's own documentation/sandbox sample VAT number — it is
# not a real taxpayer, which is exactly why it is safe as a visible placeholder.
DEFAULT_SELLER_NAME_AR = "منشأة غير محددة"
DEFAULT_SELLER_VAT = "300000000000003"

TEST_STAMP_NOTICE_AR = (
    "ختم توقيع محلي للاختبار — غير معتمد من هيئة الزكاة والضريبة والجمارك"
)
TEST_STAMP_NOTICE_EN = "Local test stamp — not a ZATCA-approved cryptographic stamp"

DEFAULTS_NOTICE_AR = "بعض بيانات البائع غير مسجلة وتم استخدام قيم افتراضية"

# A draft's QR is regenerated at issue (the number, timestamp, ICV and PIH are
# only fixed then), so it must never be mistaken for the final code.
PREVIEW_QR_NOTICE_AR = "رمز QR للمعاينة فقط — يُعاد توليده عند إصدار الفاتورة"

# UBL defaults for a simplified POS line (single standard-rate VAT category).
_UNIT_CODE = "PCE"
_VAT_CATEGORY = "S"


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def resolve_seller_for_qr(header: dict[str, Any]) -> dict[str, Any]:
    """Seller name / VAT for QR tags 1–2, falling back to documented defaults.

    Returns ``{"name", "vat", "used_defaults"}``. ``used_defaults`` is True when
    either value had to be substituted, so the caller can print the honest
    "placeholder data" notice on the receipt.
    """
    name = (header.get("seller_name_snapshot") or "").strip()
    vat = (header.get("seller_vat_number_snapshot") or "").strip()
    used_defaults = False
    if not name:
        name = DEFAULT_SELLER_NAME_AR
        used_defaults = True
    if not vat:
        vat = DEFAULT_SELLER_VAT
        used_defaults = True
    return {"name": name, "vat": vat, "used_defaults": used_defaults}


def _xml_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map stored cashier line columns onto the UBL builder's expected keys."""
    return [
        {
            "quantity": _dec(line["quantity"]),
            "unit_price": _dec(line["unit_price"]),
            "vat_rate": _dec(line["vat_rate"]),
            "unit_code": _UNIT_CODE,
            "vat_category_code": _VAT_CATEGORY,
            "product_name_snapshot": line.get("product_name_snapshot") or "",
            "line_amount_before_vat": _dec(line["line_subtotal"]),
            "vat_amount": _dec(line["vat_amount"]),
            "line_total_including_vat": _dec(line["line_total"]),
        }
        for line in lines
    ]


def build_cashier_zatca_fields(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    icv: int,
    pih: str,
) -> dict[str, Any]:
    """Build every ``zatca_*`` column value for one issued cashier invoice.

    Pure: no database access, no clock reads beyond the invoice's own datetime.
    ``icv`` / ``pih`` come from the caller (the repository reads them inside the
    signing transaction so the per-company chain stays consistent).

    Raises :class:`CashierSigningError` if the local signer is unavailable — the
    caller then leaves the invoice unsigned rather than storing a partial QR.
    """
    seller = resolve_seller_for_qr(header)

    issue_dt = header.get("invoice_datetime")
    if not isinstance(issue_dt, datetime.datetime):
        issue_dt = datetime.datetime.now().astimezone()

    uuid = str(header["invoice_uuid"]) if header.get("invoice_uuid") else zatca_gen.new_uuid()

    totals = {
        "subtotal_before_vat": _dec(header["subtotal"]),
        "vat_total": _dec(header["vat_amount"]),
        "total_including_vat": _dec(header["grand_total"]),
    }
    party = {
        "seller_name_ar_snapshot": seller["name"],
        "seller_vat_number_snapshot": seller["vat"],
        "customer_name_snapshot": header.get("buyer_name_snapshot") or "",
        "customer_vat_number_snapshot": header.get("buyer_vat_number_snapshot") or "",
    }

    xml = zatca_gen.build_invoice_xml(
        invoice_number=str(header["invoice_number"]),
        uuid=uuid,
        issue_datetime=issue_dt,
        icv=int(icv),
        pih=pih,
        currency=header.get("currency_code") or CURRENCY_CODE,
        payment_type="cash",  # the Cashier is a cash POS
        seller=party,
        customer=party,
        lines=_xml_lines(lines),
        totals=totals,
    )
    invoice_hash = zatca_gen.compute_invoice_hash(xml)

    signature = saudi_egs_test_signer.sign_invoice_hash(invoice_hash)
    if not signature:
        raise CashierSigningError(
            "تعذّر توليد التوقيع محليًا: مكتبة التشفير غير متاحة. لم يتم تعديل الفاتورة."
        )

    fields: dict[str, Any] = {
        "invoice_uuid": uuid,
        "zatca_icv": int(icv),
        "zatca_previous_invoice_hash": pih,
        "zatca_xml": xml.decode("utf-8"),
        "zatca_invoice_hash": invoice_hash,          # tag 6
        "zatca_signature": signature["signature"],   # tag 7
        "zatca_public_key": signature["public_key"], # tag 8
        "zatca_cryptographic_stamp": signature["stamp"],  # tag 9
        "zatca_status": ZATCA_STATUS_READY,
    }
    # Persist the placeholders that went into the QR so the stored invoice, the
    # printed receipt and the QR payload all agree (QR validation compares tags
    # against these columns).
    if not (header.get("seller_name_snapshot") or "").strip():
        fields["seller_name_snapshot"] = seller["name"]
    if not (header.get("seller_vat_number_snapshot") or "").strip():
        fields["seller_vat_number_snapshot"] = seller["vat"]
    return fields


def seller_from_company(company: dict[str, Any] | None) -> dict[str, Any]:
    """Seller name / VAT read straight from a ``companies`` row.

    Used for a DRAFT, whose ``seller_*_snapshot`` columns are still NULL because
    they are only written at issue time. The VAT number always travels with the
    company record — it is never typed on the Cashier screen — so it is looked up
    here rather than required as a visible field.
    """
    if not company:
        return {"seller_name_snapshot": None, "seller_vat_number_snapshot": None}
    name = (company.get("name_ar") or company.get("name_en") or "").strip()
    return {
        "seller_name_snapshot": name or None,
        "seller_vat_number_snapshot": (company.get("vat_number") or "").strip() or None,
    }


def build_preview_header(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    company: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """A throw-away header completed enough to render a DRAFT preview QR.

    Mirrors ``saudi_sales_invoice_service.build_preview_qr``: seller data comes
    from the currently selected company (the draft has no snapshot yet), and the
    Phase-2 fields are generated with a placeholder ICV/PIH since the draft has
    no place in the invoice chain until it is issued.

    **Never persisted.** The returned hash/signature are for on-screen preview
    only and are deliberately *not* the values the invoice will carry once
    issued (its number, timestamp, ICV and PIH are only fixed at that moment) —
    the receipt labels such a QR as a preview so it is never mistaken for the
    final one. Returns ``None`` on any problem; the caller then shows the honest
    "QR unavailable" notice.
    """
    try:
        if not lines:
            return None
        preview = dict(header)
        for key, value in seller_from_company(company).items():
            if not (preview.get(key) or "").strip() and value:
                preview[key] = value
        seller = resolve_seller_for_qr(preview)
        preview["seller_name_snapshot"] = seller["name"]
        preview["seller_vat_number_snapshot"] = seller["vat"]
        preview.update(
            build_cashier_zatca_fields(preview, lines, icv=0, pih=zatca_gen.GENESIS_PIH)
        )
        return preview
    except Exception:  # noqa: BLE001 - a preview QR is best-effort, never fatal
        return None


class CashierSigningError(Exception):
    """Local Phase-2 data could not be generated; the invoice is left untouched."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CashierZatcaSigner:
    """Generates + persists the local Phase-2 data for an issued cashier invoice."""

    def __init__(self, repository: CashierRepository | None = None) -> None:
        self.repository = repository or CashierRepository()

    def ensure_signed(self, invoice_id: int) -> dict[str, Any] | None:
        """Sign the invoice if it is ISSUED and not signed yet; else leave it be.

        Idempotent by design: the repository's guarded write only matches a row
        whose ``zatca_invoice_hash`` is still NULL, so a reprint never re-signs
        and the QR of an issued invoice never changes. Returns the (re)loaded
        invoice, or ``None`` if it does not exist.
        """
        invoice = self.repository.load_invoice(int(invoice_id))
        if invoice is None:
            return None
        header = invoice["header"]
        if header["invoice_status"] != STATUS_ISSUED:
            return invoice
        if (header.get("zatca_invoice_hash") or "").strip():
            return invoice  # already signed — keep the original QR
        if not invoice["lines"]:
            return invoice

        signed = self.repository.sign_zatca_atomically(
            int(invoice_id), build_cashier_zatca_fields
        )
        return signed or invoice


__all__ = [
    "CashierZatcaSigner",
    "CashierSigningError",
    "build_cashier_zatca_fields",
    "build_preview_header",
    "seller_from_company",
    "resolve_seller_for_qr",
    "PREVIEW_QR_NOTICE_AR",
    "ZATCA_STATUS_READY",
    "DEFAULT_SELLER_NAME_AR",
    "DEFAULT_SELLER_VAT",
    "TEST_STAMP_NOTICE_AR",
    "TEST_STAMP_NOTICE_EN",
    "DEFAULTS_NOTICE_AR",
]
