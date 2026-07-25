-- Migration 009 (DOWNGRADE): reverse 009_create_products_schema.sql.
--
-- Removes ONLY the objects that migration 009 created: the products table, its
-- indexes, the item-code sequence, and the updated_at trigger + function. It
-- does not touch any other table or data.
--
-- NOTE: dropping ``products`` discards the rows in that table (the table itself
-- is what this migration introduced). No pre-existing / unrelated data is
-- affected. Safe to run repeatedly (every drop uses IF EXISTS).

BEGIN;

DROP TRIGGER IF EXISTS trg_products_set_updated_at ON products;

-- The table owns the sequence (ALTER SEQUENCE ... OWNED BY) and holds the
-- indexes, so dropping the table also removes uq_products_item_code,
-- idx_products_item_name(_trgm) and the item-code sequence. We intentionally do
-- NOT issue a separate DROP SEQUENCE: the owned sequence is gone with the table,
-- and an unqualified drop could otherwise resolve to a same-named sequence in
-- another schema on the search_path.
DROP TABLE IF EXISTS products;

-- The trigger function is products-specific; safe to drop once the trigger is
-- gone.
DROP FUNCTION IF EXISTS products_set_updated_at();

COMMIT;
