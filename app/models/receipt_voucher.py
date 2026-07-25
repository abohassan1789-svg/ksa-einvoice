"""Schema definition and domain constants for the Receipt Vouchers module
(سندات القبض).

This module holds the *structure* (DDL) and reusable domain constants only — no
SQL execution and no business logic. The repository layer runs
``RECEIPT_VOUCHERS_SCHEMA_SQL`` (every statement uses ``CREATE ... IF NOT EXISTS``
/ is otherwise idempotent, so it is safe to run repeatedly and never drops,
resets, or deletes anything). The same SQL is mirrored in
``app/database/migrations/012_create_receipt_vouchers_schema.sql`` (create) plus
``013_change_receipt_voucher_prefix.sql`` (number prefix PAID- -> PA-), each with
its own downgrade file.

Design notes
------------
* ``id`` is a PostgreSQL IDENTITY column (sequence-backed). Ids are never
  computed with ``MAX(id) + 1``.
* ``voucher_number`` is a user-facing document number that starts at ``PA-001``.
  Automatic numbers come from the dedicated sequence
  ``receipt_voucher_number_seq`` (the first ``nextval`` returns ``1``, formatted
  as ``PA-001``). ``nextval`` is atomic, so concurrent creators never receive
  the same number. A UNIQUE index is the final guard. The number stays editable:
  an authorised user may supply their own value, and the numbering service
  re-seeds the sequence past any manually-entered ``PA-<n>`` so a high manual
  number can never collide with a future automatic one. The next number is never
  produced with ``MAX(voucher_number) + 1``.
* ``voucher_date`` defaults to ``CURRENT_DATE`` when a new voucher is created.
* ``customer_id`` / ``company_id`` are RESTRICT foreign keys to the existing
  ``customers`` (PK ``customer_id``) and ``companies`` (PK ``id``) tables — a
  customer or company that has receipt vouchers cannot be deleted. No duplicate
  master tables are created.
* ``payment_type`` is guarded by a CHECK constraint (``cash`` / ``bank_transfer``)
  — the project's existing convention for enumerations (see
  ``companies`` / ``products``); no native ``ENUM`` type is introduced.
* ``amount`` uses ``numeric(18, 2)`` (never floating point) and a CHECK enforces
  ``amount > 0``.
* ``description`` is optional free text (Unicode; Arabic and English).
* Audit fields mirror the project's existing convention (see
  ``sales_invoices``): ``created_at`` / ``updated_at`` are ``timestamptz`` and
  ``updated_at`` is maintained by a BEFORE UPDATE trigger; ``created_by`` /
  ``updated_by`` are nullable FKs to ``app_users(id)`` with ``ON DELETE SET
  NULL`` (deleting a user must not block, and must not delete, its vouchers).

This module also exposes a lightweight SQLAlchemy ORM model
(``ReceiptVoucher``) mapped to the same table, for callers that prefer ORM
access. The live application talks to PostgreSQL through the raw-psycopg
``ReceiptVoucherRepository`` (mirroring the other modules), so the ORM model is
optional and self-contained.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

# --- domain constants -------------------------------------------------------

TABLE_NAME = "receipt_vouchers"

# Automatic voucher numbers: the sequence yields 1, 2, 3, ... and the number is
# rendered as ``PA-<zero-padded-to-3>`` -> PA-001, PA-002, ...
VOUCHER_NUMBER_SEQUENCE = "receipt_voucher_number_seq"
VOUCHER_NUMBER_PREFIX = "PA-"
VOUCHER_NUMBER_PAD = 3           # minimum digits (grows past 999 -> PA-1000)
FIRST_VOUCHER_SEQ = 1            # first sequence value -> PA-001

# Supported payment types (stored codes; the UI will show the Arabic labels).
PAYMENT_CASH = "cash"
PAYMENT_BANK_TRANSFER = "bank_transfer"
PAYMENT_TYPES: tuple[str, ...] = (PAYMENT_CASH, PAYMENT_BANK_TRANSFER)

PAYMENT_LABELS_AR = {
    PAYMENT_CASH: "كاش",
    PAYMENT_BANK_TRANSFER: "تحويل",
}

# Numeric precision/scale for money (NUMERIC(18,2)).
MONEY_PRECISION, MONEY_SCALE = 18, 2

# Columns loaded for list/detail views (in logical order).
LIST_COLUMNS: tuple[str, ...] = (
    "id",
    "voucher_number",
    "voucher_date",
    "customer_id",
    "company_id",
    "payment_type",
    "amount",
    "description",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
)

# Columns the application is allowed to write on insert. ``id`` (IDENTITY),
# ``voucher_number`` (DB DEFAULT unless explicitly supplied), and the timestamps
# (DB defaults / trigger) are intentionally excluded from the *required* set;
# ``voucher_number`` may still be passed explicitly to store a manual number.
WRITABLE_COLUMNS: tuple[str, ...] = (
    "voucher_number",
    "voucher_date",
    "customer_id",
    "company_id",
    "payment_type",
    "amount",
    "description",
    "created_by",
    "updated_by",
)


# --- raw DDL (source of truth, mirrored by migration 012) -------------------
#
# The voucher_number DEFAULT formats the sequence value the same way the Python
# numbering service does: ``to_char(n, 'FM000')`` yields a minimum of 3 digits
# with no leading spaces (FM = fill mode), matching ``f"{n:03d}"``.

RECEIPT_VOUCHERS_SCHEMA_SQL = f"""
-- Sequence that feeds automatic voucher numbers. A freshly-created sequence
-- returns {FIRST_VOUCHER_SEQ} on the FIRST nextval() (so the first document is
-- PA-001). nextval is atomic, so concurrent creators never share a number.
CREATE SEQUENCE IF NOT EXISTS {VOUCHER_NUMBER_SEQUENCE} AS bigint MINVALUE 1;

CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    voucher_number varchar(30) NOT NULL
                     DEFAULT ('{VOUCHER_NUMBER_PREFIX}' ||
                              to_char(nextval('{VOUCHER_NUMBER_SEQUENCE}'), 'FM000')),
    voucher_date   date NOT NULL DEFAULT CURRENT_DATE,
    customer_id    integer NOT NULL,
    company_id     integer NOT NULL,
    payment_type   varchar(20) NOT NULL,
    amount         numeric({MONEY_PRECISION}, {MONEY_SCALE}) NOT NULL,
    description    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    created_by     integer,
    updated_by     integer,
    CONSTRAINT ck_receipt_vouchers_amount_positive
        CHECK (amount > 0),
    CONSTRAINT ck_receipt_vouchers_payment_type
        CHECK (payment_type IN ('{PAYMENT_CASH}', '{PAYMENT_BANK_TRANSFER}')),
    CONSTRAINT ck_receipt_vouchers_voucher_number_not_blank
        CHECK (char_length(btrim(voucher_number)) > 0),
    -- RESTRICT: a customer/company linked to receipt vouchers cannot be deleted.
    CONSTRAINT fk_receipt_vouchers_customer
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_receipt_vouchers_company
        FOREIGN KEY (company_id) REFERENCES companies (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    -- Audit user refs: nullable; deleting a user nulls the reference (never
    -- blocks and never deletes the voucher).
    CONSTRAINT fk_receipt_vouchers_created_by
        FOREIGN KEY (created_by) REFERENCES app_users (id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_receipt_vouchers_updated_by
        FOREIGN KEY (updated_by) REFERENCES app_users (id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

-- Bind the sequence to the column so it is owned/dropped together and reported
-- by pg_get_serial_sequence. (No-op if already owned.)
ALTER SEQUENCE {VOUCHER_NUMBER_SEQUENCE} OWNED BY {TABLE_NAME}.voucher_number;

-- Align the number DEFAULT on re-runs: CREATE TABLE IF NOT EXISTS never touches
-- an existing table, so a database created before the prefix changed (PAID- ->
-- PA-, migration 013) still gets the current prefix. Idempotent.
ALTER TABLE {TABLE_NAME}
    ALTER COLUMN voucher_number SET DEFAULT
        ('{VOUCHER_NUMBER_PREFIX}' || to_char(nextval('{VOUCHER_NUMBER_SEQUENCE}'), 'FM000'));

-- Idempotent, forward-only re-seed: on a re-run over a table that already holds
-- PA-<n> numbers, advance the sequence past the greatest existing <n> so the
-- next automatic number cannot collide with stored ones. Never regresses (only
-- fires when the greatest existing number is ahead of the sequence), so a fresh
-- table keeps its first nextval() = {FIRST_VOUCHER_SEQ} (PA-001).
DO $$
DECLARE
    max_existing bigint;
    seq_last     bigint;
BEGIN
    SELECT COALESCE(
             MAX(CAST(substring(voucher_number FROM '^{VOUCHER_NUMBER_PREFIX}([0-9]+)$') AS bigint)),
             0)
      INTO max_existing
      FROM {TABLE_NAME}
     WHERE voucher_number ~ '^{VOUCHER_NUMBER_PREFIX}[0-9]+$';

    SELECT last_value INTO seq_last FROM {VOUCHER_NUMBER_SEQUENCE};

    IF max_existing > seq_last THEN
        PERFORM setval('{VOUCHER_NUMBER_SEQUENCE}', max_existing, true);
    END IF;
END $$;

-- Unique voucher number (final guard for both automatic and manual numbers).
-- This index also serves exact/prefix lookups, so no separate btree is added.
CREATE UNIQUE INDEX IF NOT EXISTS uq_receipt_vouchers_voucher_number
    ON {TABLE_NAME} (voucher_number);

-- Report / search indexes (voucher_number is already covered by the UNIQUE
-- index above, so it is deliberately not indexed again here).
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_voucher_date
    ON {TABLE_NAME} (voucher_date);
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_customer_id
    ON {TABLE_NAME} (customer_id);
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_company_id
    ON {TABLE_NAME} (company_id);
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_payment_type
    ON {TABLE_NAME} (payment_type);

-- Keep updated_at accurate on every UPDATE, whoever performs it. A dedicated
-- function name avoids clashing with the other modules' trigger functions.
CREATE OR REPLACE FUNCTION receipt_vouchers_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_receipt_vouchers_set_updated_at ON {TABLE_NAME};
CREATE TRIGGER trg_receipt_vouchers_set_updated_at
    BEFORE UPDATE ON {TABLE_NAME}
    FOR EACH ROW EXECUTE FUNCTION receipt_vouchers_set_updated_at();
"""


# --- optional SQLAlchemy ORM model (same table) -----------------------------

Base = declarative_base()


class ReceiptVoucher(Base):
    """SQLAlchemy ORM mapping for the ``receipt_vouchers`` table.

    Optional convenience for ORM callers; the running app uses
    ``ReceiptVoucherRepository`` (raw psycopg). ``id`` is database-generated and
    ``voucher_number`` has a database DEFAULT (``PA-<seq>``); ``updated_at`` is
    maintained by a BEFORE UPDATE trigger.
    """

    __tablename__ = TABLE_NAME

    id = Column(Integer, Identity(always=True), primary_key=True)
    voucher_number = Column(
        String(30),
        nullable=False,
        server_default=text(
            f"('{VOUCHER_NUMBER_PREFIX}' || "
            f"to_char(nextval('{VOUCHER_NUMBER_SEQUENCE}'), 'FM000'))"
        ),
    )
    voucher_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    customer_id = Column(
        Integer,
        ForeignKey("customers.customer_id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    company_id = Column(
        Integer,
        ForeignKey("companies.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    payment_type = Column(String(20), nullable=False)
    amount = Column(Numeric(MONEY_PRECISION, MONEY_SCALE), nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(
        Integer, ForeignKey("app_users.id", onupdate="CASCADE", ondelete="SET NULL")
    )
    updated_by = Column(
        Integer, ForeignKey("app_users.id", onupdate="CASCADE", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_receipt_vouchers_amount_positive"),
        CheckConstraint(
            f"payment_type IN ('{PAYMENT_CASH}', '{PAYMENT_BANK_TRANSFER}')",
            name="ck_receipt_vouchers_payment_type",
        ),
        CheckConstraint(
            "char_length(btrim(voucher_number)) > 0",
            name="ck_receipt_vouchers_voucher_number_not_blank",
        ),
        Index("uq_receipt_vouchers_voucher_number", "voucher_number", unique=True),
        Index("idx_receipt_vouchers_voucher_date", "voucher_date"),
        Index("idx_receipt_vouchers_customer_id", "customer_id"),
        Index("idx_receipt_vouchers_company_id", "company_id"),
        Index("idx_receipt_vouchers_payment_type", "payment_type"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<ReceiptVoucher id={self.id} number={self.voucher_number!r} "
            f"amount={self.amount}>"
        )
