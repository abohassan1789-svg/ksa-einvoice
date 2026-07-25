-- 006: Copy Area Number into Area Code for existing customers.
--
-- Business fields (Customers screen labels):
--   * Area Number = customers.area_number         ("رقم المنطقة", varchar)
--   * Area Code   = customers.place_area_feddan    ("كود المنطقة", integer)
--
-- Goal: every existing customer that has an Area Number should also carry the
-- same value in Area Code, without touching any other data.
--
-- Rules enforced below:
--   * Only rows where Area Number holds a value (non-null, non-blank).
--   * Only rows where Area Code is currently empty (NULL). An Area Code that is
--     already filled is never overwritten.
--   * Area Code is integer while Area Number is varchar, so only purely numeric
--     Area Number values are copied (cast to integer). Any non-numeric value is
--     skipped, so the migration can never fail on a bad cast.
--   * Area Number itself is left unchanged.
--
-- The whole script runs in one transaction with built-in verification: if any
-- invariant is violated it RAISEs and the transaction rolls back, leaving the
-- data untouched. Re-running is safe (idempotent): rows copied on a previous run
-- already have a non-null Area Code and are skipped.

BEGIN;

-- Persistent backup of the Area Code column before the change, so the update can
-- be reverted if ever needed (see 006_rollback note at the end of this file).
DROP TABLE IF EXISTS backup_006_customers_area_code;
CREATE TABLE backup_006_customers_area_code AS
SELECT customer_id, place_area_feddan AS pre_area_code, area_number
FROM customers;

-- Copy Area Number -> Area Code where Area Code is empty and Area Number is a
-- numeric value. Area Number is left as-is.
UPDATE customers AS c
SET place_area_feddan = c.area_number::integer
WHERE c.place_area_feddan IS NULL
  AND c.area_number IS NOT NULL
  AND btrim(c.area_number) <> ''
  AND c.area_number ~ '^[0-9]+$';

-- Verification: fail loudly (and roll back) if any rule was broken.
DO $$
DECLARE
    not_populated          integer;
    overwritten            integer;
    area_number_changed    integer;
    empty_row_touched      integer;
BEGIN
    -- 1. Rows with a numeric Area Number and previously empty Area Code must now
    --    have Area Code equal to Area Number.
    SELECT COUNT(*) INTO not_populated
    FROM backup_006_customers_area_code b
    JOIN customers c ON c.customer_id = b.customer_id
    WHERE b.pre_area_code IS NULL
      AND b.area_number ~ '^[0-9]+$'
      AND c.place_area_feddan IS DISTINCT FROM b.area_number::integer;

    -- 2. Rows that already had an Area Code must keep exactly that value.
    SELECT COUNT(*) INTO overwritten
    FROM backup_006_customers_area_code b
    JOIN customers c ON c.customer_id = b.customer_id
    WHERE b.pre_area_code IS NOT NULL
      AND c.place_area_feddan IS DISTINCT FROM b.pre_area_code;

    -- 3. Area Number must never change.
    SELECT COUNT(*) INTO area_number_changed
    FROM backup_006_customers_area_code b
    JOIN customers c ON c.customer_id = b.customer_id
    WHERE c.area_number IS DISTINCT FROM b.area_number;

    -- 4. Rows with empty / non-numeric Area Number and empty Area Code stay empty.
    SELECT COUNT(*) INTO empty_row_touched
    FROM backup_006_customers_area_code b
    JOIN customers c ON c.customer_id = b.customer_id
    WHERE b.pre_area_code IS NULL
      AND (b.area_number IS NULL OR btrim(b.area_number) = '' OR b.area_number !~ '^[0-9]+$')
      AND c.place_area_feddan IS NOT NULL;

    IF not_populated > 0 THEN
        RAISE EXCEPTION 'Area Code migration: % rows with Area Number were not populated', not_populated;
    END IF;
    IF overwritten > 0 THEN
        RAISE EXCEPTION 'Area Code migration: % rows had an existing Area Code overwritten', overwritten;
    END IF;
    IF area_number_changed > 0 THEN
        RAISE EXCEPTION 'Area Code migration: % rows had Area Number modified', area_number_changed;
    END IF;
    IF empty_row_touched > 0 THEN
        RAISE EXCEPTION 'Area Code migration: % rows with no Area Number were wrongly touched', empty_row_touched;
    END IF;
END $$;

COMMIT;

-- Summary of the result.
SELECT
    COUNT(*)                                                              AS total_customers,
    COUNT(place_area_feddan)                                             AS area_code_filled,
    COUNT(*) FILTER (WHERE place_area_feddan IS NULL)                    AS area_code_empty,
    COUNT(*) FILTER (WHERE area_number IS NOT NULL AND btrim(area_number) <> '') AS area_number_has_value
FROM customers;

-- Rollback (manual, if ever required):
--   UPDATE customers AS c
--   SET place_area_feddan = b.pre_area_code
--   FROM backup_006_customers_area_code b
--   WHERE c.customer_id = b.customer_id;
-- Then optionally: DROP TABLE backup_006_customers_area_code;
