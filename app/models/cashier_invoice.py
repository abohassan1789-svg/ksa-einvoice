"""Domain constants and column maps for the experimental Cashier/POS tables.

Scope note
----------
Like :mod:`app.models.sales_invoice` (and unlike :mod:`app.models.product`),
this module contains **no DDL and never runs any schema statement**. The two
tables it describes already exist in the ``InvPhase2`` database (created by
migration ``015_create_cashier_invoices_schema.sql``) and must be used as-is:

* ``cashier_invoices``       — Cashier/POS invoice header (one row per invoice)
* ``cashier_invoice_lines``  — Cashier/POS invoice detail lines

This layer therefore holds only *structure metadata* (table + column names) and
*reusable domain constants* (statuses, defaults, precisions). No
``CREATE``/``ALTER``/``create_all`` is performed anywhere for these tables.

The Cashier module deliberately reuses the existing master data (``products``,
``customers``, ``companies``, ``app_users``) and never posts anything to the
``sales_invoice*`` tables, accounting, inventory or receipt vouchers.

All monetary and quantity values are handled with :class:`decimal.Decimal`;
Python ``float`` is never used for invoice figures.
"""

from __future__ import annotations

from decimal import Decimal

# --- table names ------------------------------------------------------------

TBL_CASHIER_INVOICES = "cashier_invoices"
TBL_CASHIER_LINES = "cashier_invoice_lines"

# --- invoice status (mirrors cashier_invoices.invoice_status CHECK) ----------

STATUS_DRAFT = "DRAFT"
STATUS_ISSUED = "ISSUED"
STATUS_CANCELLED = "CANCELLED"

STATUS_LABELS_AR = {
    STATUS_DRAFT: "مسودة",
    STATUS_ISSUED: "مُصدرة",
    STATUS_CANCELLED: "ملغاة",
}

# --- invoice type (simplified POS invoice only in this phase) -----------------

INVOICE_TYPE_SIMPLIFIED = "SIMPLIFIED"

# --- SAR-only currency (the Cashier never exposes currency controls) ---------

CURRENCY_CODE = "SAR"

# --- ZATCA status (mirrors cashier_invoices.zatca_status CHECK) --------------
# Only NOT_GENERATED is used in this phase; the rest exist for later phases.

ZATCA_STATUS_NOT_GENERATED = "NOT_GENERATED"

# --- line defaults -----------------------------------------------------------

DEFAULT_VAT_RATE = Decimal("15.00")   # standard KSA VAT rate, %
DEFAULT_QUANTITY = Decimal("1")

# --- numeric precision (must not exceed the physical column definitions) -----
# cashier_invoices: subtotal / vat_amount / grand_total = numeric(18,2)
# cashier_invoice_lines: quantity / unit_price = numeric(18,6);
#                        line_subtotal / vat_amount / line_total = numeric(18,2);
#                        vat_rate = numeric(5,2)

MONEY_QUANT = Decimal("0.01")           # numeric(18,2) amounts round to 2 dp
QTY_PRICE_QUANT = Decimal("0.000001")   # numeric(18,6) quantity / unit_price
VAT_RATE_QUANT = Decimal("0.01")        # numeric(5,2) vat rate

VAT_NUMBER_MAX_LEN = 15  # matches the *_vat_number_snapshot column width

# --- header columns the application writes on DRAFT insert -------------------
# ``id`` (IDENTITY), ``invoice_date`` / ``invoice_time`` / ``invoice_datetime``
# (DB defaults on insert), ``print_count`` (default 0), ``created_at`` /
# ``updated_at`` (DB defaults) and every cryptographic ZATCA column are
# intentionally excluded. Seller/buyer snapshots stay NULL for a draft and are
# populated at ISSUE time (see ``ISSUE_HEADER_COLUMNS``).
INVOICE_INSERT_COLUMNS: tuple[str, ...] = (
    "invoice_number",
    "customer_id",
    "company_id",
    "branch_id",
    "created_by_user_id",
    "invoice_status",
    "invoice_type",
    "currency_code",
    "subtotal",
    "vat_amount",
    "grand_total",
    "notes",
    "zatca_status",
)

# Header columns that may legitimately change on a draft update.
INVOICE_UPDATE_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "company_id",
    "branch_id",
    "subtotal",
    "vat_amount",
    "grand_total",
    "notes",
)

# Header columns written when locally issuing (in addition to the status flip,
# invoice_date/time/datetime handled explicitly in the repository).
ISSUE_HEADER_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "company_id",
    "subtotal",
    "vat_amount",
    "grand_total",
    "notes",
    "seller_name_snapshot",
    "seller_vat_number_snapshot",
    "seller_commercial_registration_snapshot",
    "seller_address_snapshot",
    "buyer_name_snapshot",
    "buyer_vat_number_snapshot",
)

# Header columns read back / shown on the screen.
INVOICE_SELECT_COLUMNS: tuple[str, ...] = (
    "id",
    "invoice_number",
    "invoice_uuid",
    "invoice_date",
    "invoice_time",
    "invoice_datetime",
    "customer_id",
    "company_id",
    "branch_id",
    "created_by_user_id",
    "invoice_status",
    "invoice_type",
    "currency_code",
    "subtotal",
    "vat_amount",
    "grand_total",
    "notes",
    "seller_name_snapshot",
    "seller_vat_number_snapshot",
    "seller_commercial_registration_snapshot",
    "seller_address_snapshot",
    "buyer_name_snapshot",
    "buyer_vat_number_snapshot",
    # ZATCA sources — needed by the print service to check Phase-2 prerequisites
    # and build/validate the receipt QR. Written once at issue time by
    # ``app.services.cashier_zatca_signing`` (via the repository's single guarded
    # signing statement) and never modified afterwards, so a reprint reproduces
    # the original QR. The signature is a local test stamp, not a ZATCA CSID.
    "zatca_icv",
    "zatca_previous_invoice_hash",
    "zatca_invoice_hash",
    "zatca_qr_base64",
    "zatca_cryptographic_stamp",
    "zatca_public_key",
    "zatca_signature",
    "zatca_status",
    "print_count",
    "last_printed_at",
    "created_at",
    "updated_at",
)

# Detail-line columns the application writes (``id`` is IDENTITY; timestamps
# default in the database).
LINE_INSERT_COLUMNS: tuple[str, ...] = (
    "cashier_invoice_id",
    "line_number",
    "product_id",
    "product_code_snapshot",
    "product_name_snapshot",
    "quantity",
    "unit_price",
    "line_subtotal",
    "vat_rate",
    "vat_amount",
    "line_total",
)

LINE_SELECT_COLUMNS: tuple[str, ...] = ("id",) + LINE_INSERT_COLUMNS


__all__ = [name for name in dir() if not name.startswith("_")]
