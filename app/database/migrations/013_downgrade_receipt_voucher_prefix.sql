-- Migration 013 (DOWNGRADE): reverse 013_change_receipt_voucher_prefix.sql —
-- restore the original ``PAID-`` prefix on automatic Receipt Voucher numbers.
--
-- Only the column DEFAULT expression is restored. No rows, columns, or other
-- tables are touched. Safe to run repeatedly.

BEGIN;

ALTER TABLE receipt_vouchers
    ALTER COLUMN voucher_number SET DEFAULT
        ('PAID-' || to_char(nextval('receipt_voucher_number_seq'), 'FM000'));

COMMIT;
