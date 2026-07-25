"""Domain constants and column maps for the Saudi Phase-2 sales-invoice tables.

Scope note
----------
Unlike :mod:`app.models.product`, this module contains **no DDL and never runs
any schema statement**. The five tables it describes already exist in the
``InvPhase2`` database and are owned/managed outside this application:

* ``sales_invoices``                 — invoice header (one row per invoice)
* ``sales_invoice_lines``            — invoice detail lines
* ``sales_invoice_zatca_data``       — Phase-2 technical/cryptographic data (1:1)
* ``sales_invoice_zatca_submissions``— ZATCA submission attempts (history)
* ``sales_invoice_audit_logs``       — append-only audit trail

This layer therefore holds only *structure metadata* (table + column names) and
*reusable domain constants* (statuses, payment types, defaults, precisions). No
``CREATE``/``ALTER``/``create_all`` is performed anywhere for these tables.

All monetary and quantity values are handled with :class:`decimal.Decimal`;
Python ``float`` is never used for invoice figures.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

# --- table names ------------------------------------------------------------

TBL_INVOICES = "sales_invoices"
TBL_LINES = "sales_invoice_lines"
TBL_ZATCA_DATA = "sales_invoice_zatca_data"
TBL_ZATCA_SUBMISSIONS = "sales_invoice_zatca_submissions"
TBL_AUDIT_LOGS = "sales_invoice_audit_logs"

# --- document status (mirrors sales_invoices.document_status) ----------------

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"

STATUS_LABELS_AR = {
    STATUS_DRAFT: "مسودة",
    STATUS_APPROVED: "معتمدة",
}

# --- payment type (stored codes; UI shows the Arabic labels) -----------------

PAYMENT_CASH = "cash"
PAYMENT_CREDIT = "credit"

PAYMENT_LABELS_AR = {
    PAYMENT_CASH: "نقدي",
    PAYMENT_CREDIT: "آجل",
}

# --- receipt-voucher numbering (سند القبض) ----------------------------------
# The cash-receipt voucher a cash invoice produces — both the one PRINTED from
# the sales-invoice screen and the settlement movement the customer statement
# derives for a cash invoice — carries its OWN serial, offset from the invoice
# number so the two documents never share a number. At the user's request the
# offset is 123 (invoice 123 → voucher 246, 5 → 128). Defined here, in the
# domain layer, so the print screen and the statement report apply the exact
# same rule and can never drift.
VOUCHER_NUMBER_OFFSET = 123


def voucher_serial_from_invoice_number(
    invoice_number: Any, *, offset: int = VOUCHER_NUMBER_OFFSET
) -> str:
    """Derive the cash-receipt voucher serial from an invoice number by adding ``offset``.

    The invoice number is free text, so only its trailing run of digits is treated
    as the sequence and incremented; any prefix and zero-padding width are kept:
    ``"123" → "246"``, ``"5" → "128"``, ``"A-77" → "A-200"``, ``"005" → "128"``.
    A value with no digits at all is returned unchanged — there is nothing to add
    to. This is derivation only; it never touches the stored invoice number.
    """
    text = str(invoice_number or "").strip()
    match = re.search(r"\d+(?=\D*$)", text)  # the last run of digits in the string
    if match is None:
        return text
    digits = match.group()
    bumped = str(int(digits) + offset).zfill(len(digits))
    return text[: match.start()] + bumped + text[match.end():]

# --- SAR-only currency (this screen never exposes currency controls) ---------

CURRENCY_CODE = "SAR"

# --- line defaults -----------------------------------------------------------

DEFAULT_VAT_RATE = Decimal("15.00")   # standard KSA VAT rate, %
DEFAULT_VAT_CATEGORY = "S"            # ZATCA "Standard rate" tax category
DEFAULT_UNIT_CODE = "PCE"            # UN/ECE Rec 20 code for "piece"

# --- numeric precision (must not exceed the physical column definitions) -----

MONEY_QUANT = Decimal("0.01")        # numeric(18,2) amounts round to 2 dp
QTY_PRICE_QUANT = Decimal("0.000001")  # numeric(18,6) quantity / unit_price

# --- line item identity limits (mirror the physical varchar lengths) ---------
# A line for an unregistered item stores the typed code/name in these columns,
# so the entry layer must respect their physical size.
MAX_PRODUCT_CODE_LEN = 100           # product_code_snapshot varchar(100)
MAX_PRODUCT_NAME_LEN = 255           # product_name_snapshot varchar(255)

# --- header columns the application is allowed to write ----------------------
# ``id`` (IDENTITY), ``created_at``/``updated_at`` (DB defaults), ``row_version``
# (DB-managed) and the approval columns are intentionally excluded here.
INVOICE_INSERT_COLUMNS: tuple[str, ...] = (
    "invoice_number",
    "issue_datetime",
    "seller_company_id",
    "seller_name_ar_snapshot",
    "seller_name_en_snapshot",
    "seller_vat_number_snapshot",
    "customer_id",
    "customer_name_snapshot",
    "customer_vat_number_snapshot",
    "customer_address_snapshot",
    "payment_type",
    "notes",
    "invoice_currency_code",
    "tax_currency_code",
    "subtotal_before_vat",
    "vat_total",
    "total_including_vat",
    "document_status",
    "created_by",
    "updated_by",
)

# Header columns read back / shown on the screen.
INVOICE_SELECT_COLUMNS: tuple[str, ...] = (
    "id",
    "invoice_number",
    "issue_datetime",
    "seller_company_id",
    "seller_name_ar_snapshot",
    "seller_name_en_snapshot",
    "seller_vat_number_snapshot",
    "customer_id",
    "customer_name_snapshot",
    "customer_vat_number_snapshot",
    "customer_address_snapshot",
    "payment_type",
    "notes",
    "invoice_currency_code",
    "tax_currency_code",
    "subtotal_before_vat",
    "vat_total",
    "total_including_vat",
    "document_status",
    "approved_at",
    "approved_by",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
    "row_version",
)

# Detail-line columns the application writes (``id`` is IDENTITY; timestamps
# default in the database).
LINE_INSERT_COLUMNS: tuple[str, ...] = (
    "invoice_id",
    "line_number",
    "product_id",
    "product_code_snapshot",
    "product_name_snapshot",
    "unit_code",
    "quantity",
    "unit_price",
    "line_amount_before_vat",
    "vat_category_code",
    "vat_rate",
    "vat_amount",
    "line_total_including_vat",
)

LINE_SELECT_COLUMNS: tuple[str, ...] = ("id",) + LINE_INSERT_COLUMNS

# Integration-status values used by this screen.
ZATCA_STATUS_NOT_GENERATED = "not_generated"
ZATCA_STATUS_GENERATED = "generated"
# Statuses that mean the invoice has left the device toward ZATCA — once in any
# of these (or with a submission row) a draft is protected from deletion.
ZATCA_SUBMITTED_STATUSES: tuple[str, ...] = ("submitted", "cleared", "reported", "accepted")

# Non-cryptographic Phase-2 columns the app writes when it generates data on
# save. The cryptographic_stamp / digital_signature / public_key_snapshot /
# certificate columns are deliberately absent — they need the EGS private key,
# which this app never holds or handles.
ZATCA_GENERATE_COLUMNS: tuple[str, ...] = (
    "invoice_id",
    "uuid",
    "invoice_counter_value",
    "previous_invoice_hash",
    "invoice_hash",
    "generated_xml",
    "qr_code_base64",
    "xml_file_name",
    "integration_status",
    "xml_generated_at",
)

# Phase-2 technical columns surfaced (read-only) on the screen. Private keys,
# OTP and any other secret material are deliberately NOT listed here.
ZATCA_DISPLAY_COLUMNS: tuple[str, ...] = (
    "integration_status",
    "uuid",
    "invoice_counter_value",
    "previous_invoice_hash",
    "invoice_hash",
    "cryptographic_stamp",
    "public_key_snapshot",
    "digital_signature",
    "xml_file_name",
    "xml_file_path",
    "zatca_request_id",
    "xml_generated_at",
    "submitted_at",
    "accepted_at",
)


__all__ = [name for name in dir() if not name.startswith("_")]
