-- Migration 015 (UPGRADE): Cashier / POS invoices schema (فواتير الكاشير / نقطة البيع).
--
-- EXPERIMENTAL module. Creates ONLY two new standalone tables and their
-- supporting objects:
--   * cashier_invoices       — Cashier/POS invoice header (one row per invoice)
--   * cashier_invoice_lines  — Cashier/POS invoice detail lines
--
-- It does NOT touch, drop, or alter any existing table, column, constraint,
-- index, sequence, trigger, migration, or data. Every existing table
-- (products, customers, companies, app_users, sales_invoices,
-- sales_invoice_lines, receipt_vouchers, ...) and all of its rows are left
-- exactly as they are. The Cashier module REUSES the existing products,
-- customers, companies and app_users tables via foreign keys — it creates no
-- products / customers / companies table of its own.
--
-- SCOPE (this phase = database structure only): no UI, no printing, no QR-code,
-- no ZATCA XML/signing/reporting, no accounting journal entries, no receipt
-- vouchers, no customer-balance updates, no inventory updates, and nothing is
-- posted to the existing sales-invoice tables. The ZATCA / print columns are
-- created now but stay nullable/defaulted so later phases can populate them.
--
-- CONVENTIONS (mirror migrations 009 / 011 / 012)
--   id             : PostgreSQL IDENTITY (sequence-backed) primary key. Never MAX+1.
--   invoice_number : user-facing document number. Automatic values come from the
--                    concurrency-safe sequence ``cashier_invoice_number_seq``
--                    (first nextval = 1, rendered as CINV-000001, CINV-000002,
--                    ...). nextval is atomic so many cashier stations issuing at
--                    once never collide; the UNIQUE index is the final guard.
--                    Never MAX(invoice_number)+1. Stays editable — the app's
--                    numbering service may store a manual number and re-seed the
--                    sequence past any manual CINV-<n> (forward-only re-seed below).
--   invoice_uuid   : native uuid, UNIQUE when present, NULL allowed during DRAFT
--                    (PostgreSQL treats NULLs as distinct, so many drafts may hold
--                    NULL while the UNIQUE index still forbids duplicate values).
--   money          : numeric(18,2) — fixed decimal, never FLOAT/REAL.
--   quantity /
--   unit_price     : numeric(18,6) — matches sales_invoice_lines precision.
--   vat_rate       : numeric(5,2), constrained to 0..100.
--   created_at     : row creation timestamp (timestamptz DEFAULT now()).
--   updated_at     : maintained by a BEFORE UPDATE trigger (always accurate).
--
-- Reverse with 015_downgrade_cashier_invoices_schema.sql (drops the detail table
-- before the header table). Safe to run repeatedly: every statement uses
-- CREATE ... IF NOT EXISTS / CREATE OR REPLACE / a forward-only re-seed, so no
-- existing data is deleted, reset, or modified.

BEGIN;

-- Concurrency-safe feed for automatic cashier invoice numbers. Seeded so the
-- first nextval() returns 1 (CINV-000001).
CREATE SEQUENCE IF NOT EXISTS cashier_invoice_number_seq AS bigint MINVALUE 1;

-- ---------------------------------------------------------------------------
-- Table 1: cashier_invoices (header)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cashier_invoices (
    id                                       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    invoice_number                           varchar(100) NOT NULL
                                               DEFAULT ('CINV-' ||
                                                        to_char(nextval('cashier_invoice_number_seq'), 'FM000000')),
    invoice_uuid                             uuid,

    invoice_date                             date        NOT NULL DEFAULT CURRENT_DATE,
    invoice_time                             time        NOT NULL DEFAULT localtime,
    invoice_datetime                         timestamptz NOT NULL DEFAULT now(),

    -- FK columns. company_id required; the rest nullable per spec. No FK ever
    -- cascade-deletes a cashier invoice when a master row is removed.
    customer_id                              integer,
    company_id                               integer NOT NULL,
    branch_id                                integer,          -- no branches table in this DB: plain nullable integer, no FK
    created_by_user_id                       integer,

    invoice_status                           varchar(20) NOT NULL DEFAULT 'DRAFT',
    invoice_type                             varchar(20) NOT NULL DEFAULT 'SIMPLIFIED',
    currency_code                            char(3)     NOT NULL DEFAULT 'SAR',

    subtotal                                 numeric(18, 2) NOT NULL DEFAULT 0,
    vat_amount                               numeric(18, 2) NOT NULL DEFAULT 0,
    grand_total                              numeric(18, 2) NOT NULL DEFAULT 0,

    notes                                    text,

    -- Seller / buyer snapshots. Nullable during DRAFT; populated at ISSUE time
    -- from the selected company / customer so an issued invoice keeps its
    -- original data even if the master record changes later.
    seller_name_snapshot                     varchar(255),
    seller_vat_number_snapshot               varchar(15),
    seller_commercial_registration_snapshot  varchar(50),
    seller_address_snapshot                  text,

    buyer_name_snapshot                      varchar(255),
    buyer_vat_number_snapshot                varchar(15),

    -- ZATCA columns (all nullable now; generation/signing/reporting come later).
    zatca_icv                                bigint,           -- invoice counter value
    zatca_previous_invoice_hash              text,
    zatca_invoice_hash                       text,
    zatca_qr_base64                          text,
    zatca_xml                                text,
    zatca_signed_xml                         text,
    zatca_cryptographic_stamp                text,
    zatca_public_key                         text,
    zatca_signature                          text,
    zatca_status                             varchar(30) NOT NULL DEFAULT 'NOT_GENERATED',
    zatca_response                           text,
    zatca_reported_at                        timestamptz,

    -- Printing bookkeeping (printing itself is not implemented in this phase).
    print_count                              integer NOT NULL DEFAULT 0,
    last_printed_at                          timestamptz,

    created_at                               timestamptz NOT NULL DEFAULT now(),
    updated_at                               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_cashier_invoices_invoice_number_not_blank
        CHECK (char_length(btrim(invoice_number)) > 0),
    CONSTRAINT ck_cashier_invoices_invoice_status
        CHECK (invoice_status IN ('DRAFT', 'ISSUED', 'CANCELLED')),
    CONSTRAINT ck_cashier_invoices_invoice_type
        CHECK (invoice_type IN ('SIMPLIFIED')),
    CONSTRAINT ck_cashier_invoices_zatca_status
        CHECK (zatca_status IN ('NOT_GENERATED', 'READY', 'PENDING', 'REPORTED',
                                'ACCEPTED', 'ACCEPTED_WITH_WARNINGS', 'REJECTED', 'FAILED')),
    CONSTRAINT ck_cashier_invoices_subtotal_non_negative      CHECK (subtotal    >= 0),
    CONSTRAINT ck_cashier_invoices_vat_amount_non_negative    CHECK (vat_amount  >= 0),
    CONSTRAINT ck_cashier_invoices_grand_total_non_negative   CHECK (grand_total >= 0),
    CONSTRAINT ck_cashier_invoices_print_count_non_negative   CHECK (print_count >= 0),

    -- References to EXISTING master tables. RESTRICT / SET NULL only — never
    -- ON DELETE CASCADE (a master-row delete must not remove a cashier invoice).
    CONSTRAINT fk_cashier_invoices_customer
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_cashier_invoices_company
        FOREIGN KEY (company_id) REFERENCES companies (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_cashier_invoices_created_by_user
        FOREIGN KEY (created_by_user_id) REFERENCES app_users (id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

ALTER SEQUENCE cashier_invoice_number_seq OWNED BY cashier_invoices.invoice_number;

-- Forward-only re-seed (idempotent). No-op on a fresh table (first number stays
-- CINV-000001); on a re-run over existing CINV-<n> data it advances the sequence
-- past the greatest existing <n>. Never regresses.
DO $$
DECLARE
    max_existing bigint;
    seq_last     bigint;
BEGIN
    SELECT COALESCE(
             MAX(CAST(substring(invoice_number FROM '^CINV-([0-9]+)$') AS bigint)),
             0)
      INTO max_existing
      FROM cashier_invoices
     WHERE invoice_number ~ '^CINV-[0-9]+$';

    SELECT last_value INTO seq_last FROM cashier_invoice_number_seq;

    IF max_existing > seq_last THEN
        PERFORM setval('cashier_invoice_number_seq', max_existing, true);
    END IF;
END $$;

-- Unique guards.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cashier_invoices_invoice_number
    ON cashier_invoices (invoice_number);
-- UNIQUE on a nullable column: PostgreSQL allows many NULLs, so this enforces
-- "unique when present" while leaving DRAFT rows free to hold NULL.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cashier_invoices_invoice_uuid
    ON cashier_invoices (invoice_uuid);

-- Report / lookup indexes.
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_invoice_date
    ON cashier_invoices (invoice_date);
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_customer_id
    ON cashier_invoices (customer_id);
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_company_id
    ON cashier_invoices (company_id);
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_branch_id
    ON cashier_invoices (branch_id);
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_created_by_user_id
    ON cashier_invoices (created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_invoice_status
    ON cashier_invoices (invoice_status);
CREATE INDEX IF NOT EXISTS idx_cashier_invoices_zatca_status
    ON cashier_invoices (zatca_status);

-- Keep updated_at accurate on every UPDATE (dedicated function name avoids
-- clashing with the other modules' trigger functions).
CREATE OR REPLACE FUNCTION cashier_invoices_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cashier_invoices_set_updated_at ON cashier_invoices;
CREATE TRIGGER trg_cashier_invoices_set_updated_at
    BEFORE UPDATE ON cashier_invoices
    FOR EACH ROW EXECUTE FUNCTION cashier_invoices_set_updated_at();

-- ---------------------------------------------------------------------------
-- Table 2: cashier_invoice_lines (detail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cashier_invoice_lines (
    id                     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    cashier_invoice_id     integer NOT NULL,
    line_number            integer NOT NULL,

    product_id             integer NOT NULL,
    product_code_snapshot  varchar(100),
    product_name_snapshot  varchar(255),

    quantity               numeric(18, 6) NOT NULL DEFAULT 1,
    unit_price             numeric(18, 6) NOT NULL DEFAULT 0,

    line_subtotal          numeric(18, 2) NOT NULL DEFAULT 0,
    vat_rate               numeric(5, 2)  NOT NULL DEFAULT 15.00,
    vat_amount             numeric(18, 2) NOT NULL DEFAULT 0,
    line_total             numeric(18, 2) NOT NULL DEFAULT 0,

    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_cashier_invoice_lines_line_number_positive   CHECK (line_number > 0),
    CONSTRAINT ck_cashier_invoice_lines_quantity_positive      CHECK (quantity > 0),
    CONSTRAINT ck_cashier_invoice_lines_unit_price_non_negative CHECK (unit_price >= 0),
    CONSTRAINT ck_cashier_invoice_lines_line_subtotal_non_negative CHECK (line_subtotal >= 0),
    CONSTRAINT ck_cashier_invoice_lines_vat_amount_non_negative CHECK (vat_amount >= 0),
    CONSTRAINT ck_cashier_invoice_lines_line_total_non_negative CHECK (line_total >= 0),
    CONSTRAINT ck_cashier_invoice_lines_vat_rate_range         CHECK (vat_rate >= 0 AND vat_rate <= 100),

    -- Deleting a (draft) cashier invoice removes its detail lines. Application
    -- logic will later block deletion of ISSUED invoices.
    CONSTRAINT fk_cashier_invoice_lines_invoice
        FOREIGN KEY (cashier_invoice_id) REFERENCES cashier_invoices (id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    -- Reuse the EXISTING products table; never cascade-delete a line when a
    -- product is removed.
    CONSTRAINT fk_cashier_invoice_lines_product
        FOREIGN KEY (product_id) REFERENCES products (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    -- One line_number per invoice.
    CONSTRAINT uq_cashier_invoice_lines_invoice_line
        UNIQUE (cashier_invoice_id, line_number)
);

CREATE INDEX IF NOT EXISTS idx_cashier_invoice_lines_cashier_invoice_id
    ON cashier_invoice_lines (cashier_invoice_id);
CREATE INDEX IF NOT EXISTS idx_cashier_invoice_lines_product_id
    ON cashier_invoice_lines (product_id);

CREATE OR REPLACE FUNCTION cashier_invoice_lines_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cashier_invoice_lines_set_updated_at ON cashier_invoice_lines;
CREATE TRIGGER trg_cashier_invoice_lines_set_updated_at
    BEFORE UPDATE ON cashier_invoice_lines
    FOR EACH ROW EXECUTE FUNCTION cashier_invoice_lines_set_updated_at();

COMMIT;

-- Post-conditions (informational): both tables exist + current row counts.
SELECT 'cashier_invoices'      AS table_created, (SELECT COUNT(*) FROM cashier_invoices)      AS row_count
UNION ALL
SELECT 'cashier_invoice_lines' AS table_created, (SELECT COUNT(*) FROM cashier_invoice_lines) AS row_count;
