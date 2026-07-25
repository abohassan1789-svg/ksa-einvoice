-- Migration 015 (DOWNGRADE): reverse 015_create_cashier_invoices_schema.sql.
--
-- Removes ONLY the objects that migration 015 created: the two Cashier/POS
-- tables (cashier_invoice_lines, cashier_invoices), their indexes, the invoice-
-- number sequence, and the updated_at triggers + functions. It does not touch
-- any other table or data — every existing table (products, customers,
-- companies, app_users, sales_invoices, sales_invoice_lines, receipt_vouchers,
-- ...) and all of its rows are left intact.
--
-- NOTE: dropping the two cashier tables discards only the rows in those tables
-- (the tables themselves are what this migration introduced). No pre-existing /
-- unrelated data is affected. Safe to run repeatedly (every drop uses IF EXISTS).
--
-- ORDER: the detail table (cashier_invoice_lines) is dropped BEFORE the header
-- table (cashier_invoices) so the child FK is gone before its parent.

BEGIN;

-- 1) Detail table first (child).
DROP TRIGGER IF EXISTS trg_cashier_invoice_lines_set_updated_at ON cashier_invoice_lines;
DROP TABLE IF EXISTS cashier_invoice_lines;
DROP FUNCTION IF EXISTS cashier_invoice_lines_set_updated_at();

-- 2) Header table second (parent). Its indexes live on the table and go with it.
DROP TRIGGER IF EXISTS trg_cashier_invoices_set_updated_at ON cashier_invoices;
DROP TABLE IF EXISTS cashier_invoices;
DROP FUNCTION IF EXISTS cashier_invoices_set_updated_at();

-- The sequence is OWNED BY cashier_invoices.invoice_number, so it is dropped
-- with the header table above; this explicit DROP is a belt-and-braces no-op.
DROP SEQUENCE IF EXISTS cashier_invoice_number_seq;

COMMIT;
