"""Phase-2 (ZATCA) data generation for the Saudi sales-invoice screen.

This produces the **non-cryptographic** Phase-2 artefacts that can be generated
locally and safely, with no secrets involved:

* ``uuid``                 — a random invoice UUID (v4)
* ``invoice_counter_value`` (ICV) — a monotonic per-seller counter
* ``previous_invoice_hash`` (PIH) — the chain hash of the previous invoice
* ``generated_xml``        — a UBL 2.1 invoice document
* ``invoice_hash``         — base64( SHA-256(xml) )
* ``qr_code_base64``       — a TLV QR carrying the visible invoice facts

It deliberately does **not** produce the cryptographic stamp, the digital
signature, the public key or any certificate material: those require the EGS
unit's ZATCA-issued private key (CSID), which this system neither holds nor is
permitted to handle. The corresponding columns are left empty until a real
signing/approval service (with proper EGS onboarding) fills them. Nothing here
calls a ZATCA API.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import uuid as _uuid
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

# ZATCA "genesis" PIH for the first invoice in a chain: base64 of the hex
# SHA-256 digest of the string "0" (the value mandated by the ZATCA SDK).
GENESIS_PIH = base64.b64encode(
    hashlib.sha256(b"0").hexdigest().encode("ascii")
).decode("ascii")

STATUS_GENERATED = "generated"

# UBL PaymentMeans codes (UN/ECE 4461): cash = 10, credit transfer = 30.
_PAYMENT_MEANS = {"cash": "10", "credit": "30"}


def new_uuid() -> str:
    """A fresh random invoice UUID (v4)."""
    return str(_uuid.uuid4())


def _money(value: Any) -> str:
    return f"{Decimal(str(value)):.2f}"


def _qty(value: Any) -> str:
    return f"{Decimal(str(value)):f}"


def build_invoice_xml(
    *,
    invoice_number: str,
    uuid: str,
    issue_datetime: datetime.datetime,
    icv: int,
    pih: str,
    currency: str,
    payment_type: str,
    seller: dict[str, Any],
    customer: dict[str, Any],
    lines: list[dict[str, Any]],
    totals: dict[str, Any],
) -> bytes:
    """Build a representative UBL 2.1 invoice document (unsigned).

    ``seller`` / ``customer`` use the invoice snapshots; ``lines`` are the
    computed line dicts; ``totals`` carries subtotal/vat/total Decimals.
    """
    issue_date = issue_datetime.strftime("%Y-%m-%d")
    issue_time = issue_datetime.strftime("%H:%M:%S")
    means = _PAYMENT_MEANS.get(payment_type, "10")

    line_xml_parts: list[str] = []
    for index, line in enumerate(lines, start=1):
        line_xml_parts.append(
            f"""  <cac:InvoiceLine>
    <cbc:ID>{index}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="{escape(str(line.get('unit_code') or 'PCE'))}">{_qty(line['quantity'])}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{currency}">{_money(line['line_amount_before_vat'])}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="{currency}">{_money(line['vat_amount'])}</cbc:TaxAmount>
      <cbc:RoundingAmount currencyID="{currency}">{_money(line['line_total_including_vat'])}</cbc:RoundingAmount>
    </cac:TaxTotal>
    <cac:Item>
      <cbc:Name>{escape(str(line.get('product_name_snapshot') or ''))}</cbc:Name>
      <cac:ClassifiedTaxCategory>
        <cbc:ID>{escape(str(line.get('vat_category_code') or 'S'))}</cbc:ID>
        <cbc:Percent>{_money(line['vat_rate'])}</cbc:Percent>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price>
      <cbc:PriceAmount currencyID="{currency}">{_money(line['unit_price'])}</cbc:PriceAmount>
    </cac:Price>
  </cac:InvoiceLine>"""
        )
    lines_xml = "\n".join(line_xml_parts)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ProfileID>reporting:1.0</cbc:ProfileID>
  <cbc:ID>{escape(invoice_number)}</cbc:ID>
  <cbc:UUID>{escape(str(uuid))}</cbc:UUID>
  <cbc:IssueDate>{issue_date}</cbc:IssueDate>
  <cbc:IssueTime>{issue_time}</cbc:IssueTime>
  <cbc:InvoiceTypeCode name="0100000">388</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>
  <cbc:TaxCurrencyCode>{currency}</cbc:TaxCurrencyCode>
  <cac:AdditionalDocumentReference>
    <cbc:ID>ICV</cbc:ID>
    <cbc:UUID>{icv}</cbc:UUID>
  </cac:AdditionalDocumentReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>PIH</cbc:ID>
    <cac:Attachment>
      <cbc:EmbeddedDocumentBinaryObject mimeCode="text/plain">{escape(pih)}</cbc:EmbeddedDocumentBinaryObject>
    </cac:Attachment>
  </cac:AdditionalDocumentReference>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>{escape(str(seller.get('seller_vat_number_snapshot') or ''))}</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>{escape(str(seller.get('seller_name_ar_snapshot') or ''))}</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyTaxScheme>
        <cbc:CompanyID>{escape(str(customer.get('customer_vat_number_snapshot') or ''))}</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:PartyTaxScheme>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>{escape(str(customer.get('customer_name_snapshot') or ''))}</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:PaymentMeans>
    <cbc:PaymentMeansCode>{means}</cbc:PaymentMeansCode>
  </cac:PaymentMeans>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="{currency}">{_money(totals['vat_total'])}</cbc:TaxAmount>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="{currency}">{_money(totals['subtotal_before_vat'])}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="{currency}">{_money(totals['subtotal_before_vat'])}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{currency}">{_money(totals['total_including_vat'])}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="{currency}">{_money(totals['total_including_vat'])}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
{lines_xml}
</Invoice>"""
    return xml.encode("utf-8")


def compute_invoice_hash(xml_bytes: bytes) -> str:
    """base64( SHA-256(xml) ) — the ZATCA invoice hash over the document."""
    return base64.b64encode(hashlib.sha256(xml_bytes).digest()).decode("ascii")


def _tlv(tag: int, value: str) -> bytes:
    """TLV for a *text* tag (1-6): the value is the UTF-8 text itself."""
    return _tlv_bytes(tag, value.encode("utf-8"))


def _tlv_bytes(tag: int, raw: bytes) -> bytes:
    """TLV for a *binary* tag (7-9): the value is the raw bytes.

    ZATCA's TLV length is a single byte, so a value can never exceed 255 bytes.
    """
    if len(raw) > 255:
        raise ValueError(f"QR TLV tag {tag} is {len(raw)} bytes; the max is 255")
    return bytes([tag, len(raw)]) + raw


# --- QR tag 6 encoding -------------------------------------------------------
# Confirmed against the official standard (quoted verbatim), section 4.1
# "Structure of the QR code", ZATCA Electronic Invoice Security Features
# Implementation Standards v1.2 (2023-05-19), page 25:
#
#     The TLV encoding shall be as follows:
#       Tag: the tag value as mentioned above stored in one byte
#     [for tags 1 to 5]
#       Length: the length of the byte array resulted from the UTF8 encoding of
#               the field value. The length shall be stored in one byte.
#       Value:  the byte array resulting from the UTF8 encoding of the field value.
#     [for tag 6]
#       Length: length of hash (SHA256 ) is 32 bytes
#       Value:  the byte array constituting the value of the field
#
# So tags 1-5 are UTF-8 text and tag 6 is explicitly carved out as the **raw
# 32-byte digest** — not its 44-character base64 text. Source:
# https://zatca.gov.sa/ar/E-Invoicing/SystemsDevelopers/Documents/20230519_ZATCA_Electronic_Invoice_Security_Features_Implementation_Standards_vF.pdf
#
# The same section caps the whole QR at "up to 700 characters" of Base64.
#
# ⚠️ CURRENTLY SET TO False — REVERTED ON REQUEST 2026-07-18.
#
# The standard quoted above says tag 6 is the raw 32-byte digest, and this was
# briefly set to True to match it. It was rolled back at the user's explicit
# instruction ("رجعني 2 خطوه") while the Phase-2 signing/onboarding work is
# deferred. So the current output DEVIATES from the standard on tag 6: it emits
# the 44-character base64 text instead of the 32 raw bytes.
#
# This does NOT affect whether تطبيق فاتورة accepts the invoice — that rejection
# comes from the self-signed key in tags 8/9, and was reproduced identically with
# tag 6 in both encodings. Set back to True to restore standard-compliant tag 6;
# tests/unit/test_qr_tlv_encoding.py covers both settings.
QR_TAG6_RAW_DIGEST = False

# The Base64 QR string may not exceed this (same section 4.1).
QR_MAX_BASE64_CHARS = 700

# ⚠️ KNOWN DEVIATION, REVERTED ON REQUEST 2026-07-18 — QR tag 3.
#
# ``_qr_timestamp`` below is intentionally NOT used right now. Tag 3 is emitted by
# ``dt.strftime("%Y-%m-%dT%H:%M:%SZ")``, which stamps a ``Z`` (= UTC) onto a naive
# datetime that is actually KSA local time. Every invoice therefore claims a
# timestamp 3 hours later than the real instant, and invoices issued between
# 00:00 and 03:00 KSA also carry the wrong DATE.
#
# The corrected implementation is kept here rather than deleted so re-applying it
# is a one-line change at the call site in build_qr. It was rolled back at the
# user's explicit instruction while Phase-2 work is deferred, not because it was
# wrong.
KSA_TZ = datetime.timezone(datetime.timedelta(hours=3), "AST")


def _qr_timestamp(dt: datetime.datetime) -> str:
    """ISO-8601 for QR tag 3, as a genuine UTC instant with a ``Z`` suffix.

    A **naive** datetime is what the invoice screen produces and it means KSA
    local time, so it is localised to UTC+03:00 and then converted to real UTC.
    An **aware** datetime is converted from whatever zone it carries.

    Currently unused — see the note above.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KSA_TZ)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw_from_b64(value: str) -> bytes:
    """Decode a base64 string to the raw bytes ZATCA wants inside tags 7-9.

    Tags 7 (signature), 8 (public key) and 9 (stamp) must carry the **binary**
    value — the DER bytes — not its base64 text. Putting the text in is the
    classic mistake: the outer QR still decodes, so ordinary scanners look fine,
    but ZATCA's own reader parses the TLV and rejects it. Tag 8 in particular must
    start with 0x30 (a DER SEQUENCE); base64 text starts with 'M'.

    Falls back to the UTF-8 bytes if the value is not valid base64, so a caller
    passing something unexpected degrades instead of raising.
    """
    try:
        return base64.b64decode(value, validate=True)
    except Exception:  # noqa: BLE001 - not base64; keep whatever we were given
        return value.encode("utf-8")


def build_qr(
    *,
    seller_name: str,
    vat_number: str,
    timestamp: datetime.datetime,
    total_with_vat: Any,
    vat_total: Any,
    invoice_hash: str | None = None,
    signature: str | None = None,
    public_key: str | None = None,
    stamp: str | None = None,
) -> str:
    """A base64 TLV QR carrying the invoice facts and (optionally) the stamp.

    Tags 1–5 are the seller name, VAT number, timestamp, total (incl. VAT) and
    VAT amount. Tag 6 is the invoice hash (``base64(SHA-256(xml))``). Tags 7–9
    are the ECDSA signature, the signing public key and the stamp — the
    cryptographic tags a Phase-2 QR reader looks for.

    **Encoding rule:** tags 1–5 carry *text* and tags 7–9 carry the *raw DER
    bytes*, not their base64 text. ``signature``/``public_key``/``stamp`` are all
    passed in base64-encoded (that is how the signer and the DB hold them) and are
    decoded back to bytes on the way in. Emitting the base64 text instead still
    produces a QR that ordinary scanners decode — the outer base64 is intact — but
    a reader that walks the TLV and loads tag 8 as a DER key rejects it, which is
    exactly how that bug hid.

    **Two deviations from the standard are live right now**, both reverted on
    request 2026-07-18 and both documented at their definitions above:

    * tag 6 emits base64 text, where the standard specifies the raw 32-byte
      digest (see ``QR_TAG6_RAW_DIGEST``);
    * tag 3 stamps a ``Z`` onto unconverted KSA local time (see
      ``_qr_timestamp``, currently unused).

    ⚠️ **Tags 7, 8 and 9 are NOT ZATCA-compliant in this build.** They are only
    present when a signer supplies them, and here that signer is a **local,
    test-only** one (see ``saudi_egs_test_signer``). What that means per tag:

    * tag 7 — an ECDSA signature, but made with a locally generated key rather
      than an onboarded EGS signing key;
    * tag 8 — that same local key's public half, not a key backed by a ZATCA
      certificate;
    * tag 9 — the standard defines this as "the ECDSA signature of the
      cryptographic stamp's issued by ZATCA's technical CA" (Security Features
      Implementation Standards v1.2, Table 3). Ours is a **self-signature over
      our own public key**. There is no ZATCA technical CA involved anywhere, so
      this tag is structurally present but semantically wrong.

    The result is *structurally* Phase-2 and internally self-consistent (tag 7
    verifies against tag 8 over tag 6), which is enough for a structure-checking
    reader to display it — but it is **not** a ZATCA-onboarded stamp and the
    invoice is **not** valid for ZATCA clearance or reporting. Only tags 1-6 are
    genuinely correct here.
    """
    payload = (
        _tlv(1, seller_name or "")
        + _tlv(2, vat_number or "")
        # Reverted 2026-07-18: emits a Z suffix on unconverted local time. Swap in
        # _qr_timestamp(timestamp) to restore true UTC — see the note above it.
        + _tlv(3, timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
        + _tlv(4, _money(total_with_vat))
        + _tlv(5, _money(vat_total))
    )
    # Tag 6: the raw 32-byte SHA-256 digest per the Security Features standard
    # (see QR_TAG6_RAW_DIGEST). ``invoice_hash`` arrives base64-encoded because
    # that is the form the XML DigestValue and the DB column use.
    if invoice_hash:
        if QR_TAG6_RAW_DIGEST:
            payload += _tlv_bytes(6, _raw_from_b64(invoice_hash))
        else:
            payload += _tlv(6, invoice_hash)
    # Tags 7-9 are BINARY: the raw DER bytes, not their base64 text. See
    # _raw_from_b64 — callers hand these over base64-encoded, so decode first.
    if signature:
        payload += _tlv_bytes(7, _raw_from_b64(signature))
    if public_key:
        payload += _tlv_bytes(8, _raw_from_b64(public_key))
    if stamp:
        payload += _tlv_bytes(9, _raw_from_b64(stamp))
    return base64.b64encode(payload).decode("ascii")


__all__ = [
    "GENESIS_PIH",
    "STATUS_GENERATED",
    "new_uuid",
    "build_invoice_xml",
    "compute_invoice_hash",
    "build_qr",
]
