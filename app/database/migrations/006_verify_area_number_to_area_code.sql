-- 006 verification (read-only): confirm Area Number was copied into Area Code
-- correctly. Run any time after 006_copy_area_number_to_area_code.sql.
--
--   Area Number = customers.area_number       ("رقم المنطقة")
--   Area Code   = customers.place_area_feddan  ("كود المنطقة")

-- Scenario 1: customers with Area Number and (originally) empty Area Code should
-- now have Area Code = Area Number. Expect mismatches = 0.
SELECT 'populated_correctly' AS check_name,
       COUNT(*) AS rows,
       COUNT(*) FILTER (
           WHERE place_area_feddan IS NULL
              OR place_area_feddan::text <> btrim(area_number)
       ) AS mismatches
FROM customers
WHERE area_number ~ '^[0-9]+$';

-- Scenario 2: customers with empty Area Number are left untouched.
-- (No Area Number to copy; nothing to assert beyond the row count.)
SELECT 'empty_area_number' AS check_name,
       COUNT(*) AS rows
FROM customers
WHERE area_number IS NULL OR btrim(area_number) = '';

-- Scenario 3 (only meaningful if a pre-migration backup exists): Area Codes that
-- were already filled before the migration must be unchanged. Expect changed = 0.
-- Skips cleanly if the backup table is absent.
DO $$
DECLARE
    changed integer;
BEGIN
    IF to_regclass('public.backup_006_customers_area_code') IS NULL THEN
        RAISE NOTICE 'no backup table present - skipping "not overwritten" check';
        RETURN;
    END IF;

    SELECT COUNT(*) INTO changed
    FROM backup_006_customers_area_code b
    JOIN customers c ON c.customer_id = b.customer_id
    WHERE b.pre_area_code IS NOT NULL
      AND c.place_area_feddan IS DISTINCT FROM b.pre_area_code;

    RAISE NOTICE 'existing Area Codes changed by migration: %', changed;
    IF changed > 0 THEN
        RAISE EXCEPTION 'Verification failed: % existing Area Codes were overwritten', changed;
    END IF;
END $$;
