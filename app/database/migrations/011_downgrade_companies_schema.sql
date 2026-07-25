-- Migration 011 (DOWNGRADE): reverse 011_create_companies_schema.sql.
--
-- Removes ONLY the objects that migration 011 created: the companies table, its
-- indexes, and the normalise/updated_at trigger + function. It does not touch any
-- other table or data.
--
-- NOTE: dropping ``companies`` discards the rows in that table (the table itself
-- is what this migration introduced). No pre-existing / unrelated data is
-- affected. Safe to run repeatedly (every drop uses IF EXISTS).

BEGIN;

DROP TRIGGER IF EXISTS trg_companies_normalize ON companies;

-- The table holds the indexes (uq_companies_commercial_registration,
-- uq_companies_vat_number, idx_companies_name_ar[_trgm]), so dropping the table
-- also removes them.
DROP TABLE IF EXISTS companies;

-- The trigger function is companies-specific; safe to drop once the trigger is
-- gone.
DROP FUNCTION IF EXISTS companies_normalize();

COMMIT;
