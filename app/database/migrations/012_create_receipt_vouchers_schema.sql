-- Migration 012 (UPGRADE): Receipt Vouchers schema (سندات القبض).
--
-- Creates the standalone ``receipt_vouchers`` table and its supporting objects
-- (numbering sequence, unique/report indexes, updated_at trigger). Does NOT
-- touch, drop, or alter any existing table or data. All existing tables
-- (customers, companies, products, sales_invoices, accounting, ...) and their
-- rows are preserved. If a ``receipt_vouchers`` table already exists this
-- migration is additive and idempotent — every statement uses
-- CREATE ... IF NOT EXISTS / CREATE OR REPLACE / a forward-only re-seed, so no
-- existing voucher data is deleted or modified.
--
-- WHAT / WHY
--   id             : PostgreSQL IDENTITY (sequence-backed) primary key. Generated
--                    by PostgreSQL; never MAX(id)+1.
--   voucher_number : user-facing document number. Automatic values come from the
--                    sequence ``receipt_voucher_number_seq`` (first nextval = 1,
--                    rendered as PAID-001, then PAID-002, PAID-003, ...). Atomic
--                    and concurrency-safe; the UNIQUE index is the final guard.
--                    Stays editable — a user may store a manual number and the
--                    application's numbering service re-seeds the sequence past
--                    any manual PAID-<n>. Never MAX(voucher_number)+1.
--   voucher_date   : date, defaults to CURRENT_DATE for a new voucher.
--   customer_id    : NOT NULL, RESTRICT FK -> customers(customer_id).
--   company_id     : NOT NULL, RESTRICT FK -> companies(id).
--   payment_type   : NOT NULL, CHECK IN ('cash','bank_transfer').
--   amount         : numeric(18,2), CHECK amount > 0 (never FLOAT/REAL).
--   description    : optional Unicode text (Arabic / English).
--   created_at     : row creation timestamp (timestamptz, DEFAULT now()).
--   updated_at     : maintained by the BEFORE UPDATE trigger (always accurate).
--   created_by /
--   updated_by     : nullable FKs -> app_users(id), ON DELETE SET NULL.
--
-- Mirrors app/models/receipt_voucher.py (RECEIPT_VOUCHERS_SCHEMA_SQL). Reverse
-- with 012_downgrade_receipt_vouchers_schema.sql.
--
-- Safe to run repeatedly: every statement is idempotent and nothing is dropped,
-- reset, or deleted.

BEGIN;

CREATE SEQUENCE IF NOT EXISTS receipt_voucher_number_seq AS bigint MINVALUE 1;

CREATE TABLE IF NOT EXISTS receipt_vouchers (
    id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    voucher_number varchar(30) NOT NULL
                     DEFAULT ('PAID-' ||
                              to_char(nextval('receipt_voucher_number_seq'), 'FM000')),
    voucher_date   date NOT NULL DEFAULT CURRENT_DATE,
    customer_id    integer NOT NULL,
    company_id     integer NOT NULL,
    payment_type   varchar(20) NOT NULL,
    amount         numeric(18, 2) NOT NULL,
    description    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    created_by     integer,
    updated_by     integer,
    CONSTRAINT ck_receipt_vouchers_amount_positive
        CHECK (amount > 0),
    CONSTRAINT ck_receipt_vouchers_payment_type
        CHECK (payment_type IN ('cash', 'bank_transfer')),
    CONSTRAINT ck_receipt_vouchers_voucher_number_not_blank
        CHECK (char_length(btrim(voucher_number)) > 0),
    CONSTRAINT fk_receipt_vouchers_customer
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_receipt_vouchers_company
        FOREIGN KEY (company_id) REFERENCES companies (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_receipt_vouchers_created_by
        FOREIGN KEY (created_by) REFERENCES app_users (id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_receipt_vouchers_updated_by
        FOREIGN KEY (updated_by) REFERENCES app_users (id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

ALTER SEQUENCE receipt_voucher_number_seq OWNED BY receipt_vouchers.voucher_number;

-- Forward-only re-seed (idempotent). On a fresh table this is a no-op, so the
-- first nextval() stays 1 (PAID-001). On a re-run over existing PAID-<n> data it
-- advances the sequence past the greatest existing <n>; it never regresses.
DO $$
DECLARE
    max_existing bigint;
    seq_last     bigint;
BEGIN
    SELECT COALESCE(
             MAX(CAST(substring(voucher_number FROM '^PAID-([0-9]+)$') AS bigint)),
             0)
      INTO max_existing
      FROM receipt_vouchers
     WHERE voucher_number ~ '^PAID-[0-9]+$';

    SELECT last_value INTO seq_last FROM receipt_voucher_number_seq;

    IF max_existing > seq_last THEN
        PERFORM setval('receipt_voucher_number_seq', max_existing, true);
    END IF;
END $$;

-- Unique voucher number (final guard + exact/prefix lookup path).
CREATE UNIQUE INDEX IF NOT EXISTS uq_receipt_vouchers_voucher_number
    ON receipt_vouchers (voucher_number);

-- Report / search indexes (voucher_number already covered by the UNIQUE index).
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_voucher_date
    ON receipt_vouchers (voucher_date);
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_customer_id
    ON receipt_vouchers (customer_id);
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_company_id
    ON receipt_vouchers (company_id);
CREATE INDEX IF NOT EXISTS idx_receipt_vouchers_payment_type
    ON receipt_vouchers (payment_type);

-- Keep updated_at accurate on every UPDATE (dedicated function name avoids
-- clashing with the other modules' trigger functions).
CREATE OR REPLACE FUNCTION receipt_vouchers_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_receipt_vouchers_set_updated_at ON receipt_vouchers;
CREATE TRIGGER trg_receipt_vouchers_set_updated_at
    BEFORE UPDATE ON receipt_vouchers
    FOR EACH ROW EXECUTE FUNCTION receipt_vouchers_set_updated_at();

COMMIT;

-- Post-conditions (informational): table shape + current row count.
SELECT 'receipt_vouchers' AS table_created,
       (SELECT COUNT(*) FROM receipt_vouchers) AS row_count;
