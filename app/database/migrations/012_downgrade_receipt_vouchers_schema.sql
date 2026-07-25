-- Migration 012 (DOWNGRADE): reverse 012_create_receipt_vouchers_schema.sql.
--
-- Removes ONLY the objects that migration 012 created: the receipt_vouchers
-- table, its indexes, its numbering sequence, and the updated_at trigger +
-- function. It does not touch any other table or data (customers, companies,
-- app_users, products, sales_invoices, accounting, ... are all left intact).
--
-- NOTE: dropping ``receipt_vouchers`` discards the rows in that table (the table
-- itself is what this migration introduced). No pre-existing / unrelated data is
-- affected. Safe to run repeatedly (every drop uses IF EXISTS).

BEGIN;

DROP TRIGGER IF EXISTS trg_receipt_vouchers_set_updated_at ON receipt_vouchers;

-- The table holds its indexes (uq_receipt_vouchers_voucher_number,
-- idx_receipt_vouchers_*), so dropping the table also removes them. The
-- sequence is OWNED BY receipt_vouchers.voucher_number, so it is dropped with
-- the table; the explicit DROP SEQUENCE below is a belt-and-braces no-op.
DROP TABLE IF EXISTS receipt_vouchers;

DROP SEQUENCE IF EXISTS receipt_voucher_number_seq;

-- The trigger function is receipt-voucher-specific; safe to drop once the
-- trigger is gone.
DROP FUNCTION IF EXISTS receipt_vouchers_set_updated_at();

COMMIT;
