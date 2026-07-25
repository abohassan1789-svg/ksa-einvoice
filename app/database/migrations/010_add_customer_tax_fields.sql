-- Migration 010 (UPGRADE): add tax/registration/address fields to customers.
--
-- Adds three OPTIONAL columns to the existing ``customers`` table, using the
-- project's lowercase snake_case naming convention (same as customer_name,
-- phone_number, ...):
--   vat_number varchar(50) — الرقم الضريبي (VAT number)
--   cr         varchar(50) — السجل التجاري (Commercial Registration)
--   address    text        — العنوان (Address)
--
-- Non-destructive: every column uses ADD COLUMN IF NOT EXISTS and is nullable
-- with no default, so existing rows are untouched (the new fields are simply
-- NULL until edited). Safe to run repeatedly.
--
-- A btree index on vat_number supports exact/prefix lookups; the screen's
-- "contains" search still works without it at the current data scale.
-- Reverse with 010_downgrade_customer_tax_fields.sql.

BEGIN;

ALTER TABLE customers ADD COLUMN IF NOT EXISTS vat_number varchar(50);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS cr         varchar(50);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS address    text;

CREATE INDEX IF NOT EXISTS idx_customers_vat_number ON customers (vat_number);

COMMIT;
