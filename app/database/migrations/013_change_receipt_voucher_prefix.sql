-- Migration 013 (UPGRADE): change the automatic Receipt Voucher number prefix
-- from ``PAID-`` to ``PA-`` (سندات القبض).
--
-- Per user request (2026-07-13) automatic voucher numbers are now
-- PA-001, PA-002, PA-003, ... — still generated atomically by the PostgreSQL
-- sequence ``receipt_voucher_number_seq`` (never MAX+1) and still editable by
-- an authorised user (the UNIQUE index remains the final duplicate guard).
--
-- This migration ONLY changes the column DEFAULT expression (and re-anchors the
-- sequence past any stored PA-<n> numbers, forward-only). It does not touch any
-- row, any other column, or any other table. Migration 012 is left as history.
--
-- Mirrors app/models/receipt_voucher.py (RECEIPT_VOUCHERS_SCHEMA_SQL). Reverse
-- with 013_downgrade_receipt_voucher_prefix.sql. Safe to run repeatedly.

BEGIN;

ALTER TABLE receipt_vouchers
    ALTER COLUMN voucher_number SET DEFAULT
        ('PA-' || to_char(nextval('receipt_voucher_number_seq'), 'FM000'));

-- Forward-only re-seed against existing PA-<n> numbers (idempotent; a fresh /
-- empty table is untouched, so the first automatic number stays PA-001).
DO $$
DECLARE
    max_existing bigint;
    seq_last     bigint;
BEGIN
    SELECT COALESCE(
             MAX(CAST(substring(voucher_number FROM '^PA-([0-9]+)$') AS bigint)),
             0)
      INTO max_existing
      FROM receipt_vouchers
     WHERE voucher_number ~ '^PA-[0-9]+$';

    SELECT last_value INTO seq_last FROM receipt_voucher_number_seq;

    IF max_existing > seq_last THEN
        PERFORM setval('receipt_voucher_number_seq', max_existing, true);
    END IF;
END $$;

COMMIT;

-- Post-condition (informational): show the new DEFAULT expression.
SELECT column_default
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name = 'receipt_vouchers'
   AND column_name = 'voucher_number';
