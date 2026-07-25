-- 016_allow_unregistered_invoice_lines.sql
-- Lets a sales-invoice line describe an item that is not in the products master.
--
-- Until now ``sales_invoice_lines.product_id`` was NOT NULL, so every line had to
-- point at a registered product. An invoice sometimes has to bill a one-off item
-- that nobody wants to add to the master data, so ``product_id`` becomes nullable:
-- such a line stores only what the user typed, in the columns that already carry
-- the line's own copy of the item — ``product_code_snapshot`` and
-- ``product_name_snapshot``, both of which stay NOT NULL.
--
-- What does NOT change:
--   * The FK to products keeps its ON UPDATE CASCADE / ON DELETE RESTRICT rules;
--     a NULL simply does not participate in it, so registered lines stay linked
--     and a product that is used by an invoice still cannot be deleted.
--   * Every amount/quantity CHECK constraint stays exactly as it is.
--   * Existing rows are untouched: dropping NOT NULL never rewrites data, and a
--     line that has a product keeps it.

ALTER TABLE sales_invoice_lines
    ALTER COLUMN product_id DROP NOT NULL;
