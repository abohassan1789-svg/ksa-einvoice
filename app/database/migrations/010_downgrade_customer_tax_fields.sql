-- Migration 010 (DOWNGRADE): remove the customer tax/registration/address fields.
--
-- Drops ONLY the three columns migration 010 added (and their index). It does
-- not touch any other column or table. NOTE: dropping these columns discards any
-- values entered in them; all other customer data is preserved. Safe to run
-- repeatedly (every drop uses IF EXISTS).

BEGIN;

DROP INDEX IF EXISTS idx_customers_vat_number;

ALTER TABLE customers DROP COLUMN IF EXISTS vat_number;
ALTER TABLE customers DROP COLUMN IF EXISTS cr;
ALTER TABLE customers DROP COLUMN IF EXISTS address;

COMMIT;
