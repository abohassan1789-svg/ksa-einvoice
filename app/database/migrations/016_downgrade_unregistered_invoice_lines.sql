-- 016_downgrade_unregistered_invoice_lines.sql
-- Reverses 016_allow_unregistered_invoice_lines.sql.
--
-- WARNING: restoring NOT NULL fails while any line without a product exists —
-- those are exactly the unregistered-item lines the forward migration allows. If
-- this ALTER errors, decide deliberately what to do with them (delete them, or
-- attach each to a real product); this script will not throw invoice data away
-- on its own.

ALTER TABLE sales_invoice_lines
    ALTER COLUMN product_id SET NOT NULL;
