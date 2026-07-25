-- Renumber existing customers so customer_id starts at 1001.
-- Run with a database owner/superuser account because FK triggers are disabled
-- during the transaction while both sides of the relationship are rewritten.

BEGIN;

CREATE TEMP TABLE customer_id_map AS
SELECT
    customer_id AS old_customer_id,
    (ROW_NUMBER() OVER (ORDER BY customer_id) + 1000)::integer AS new_customer_id
FROM customers
ORDER BY customer_id;

ALTER TABLE daily_followups DISABLE TRIGGER ALL;

UPDATE customers AS c
SET customer_id = -m.new_customer_id
FROM customer_id_map AS m
WHERE c.customer_id = m.old_customer_id;

UPDATE customers AS c
SET customer_id = m.new_customer_id
FROM customer_id_map AS m
WHERE c.customer_id = -m.new_customer_id;

UPDATE daily_followups AS d
SET customer_id = m.new_customer_id
FROM customer_id_map AS m
WHERE d.customer_id = m.old_customer_id;

ALTER TABLE daily_followups ENABLE TRIGGER ALL;

DO $$
DECLARE
    orphan_count integer;
BEGIN
    SELECT COUNT(*)
    INTO orphan_count
    FROM daily_followups AS d
    LEFT JOIN customers AS c ON c.customer_id = d.customer_id
    WHERE d.customer_id IS NOT NULL
      AND c.customer_id IS NULL;

    IF orphan_count > 0 THEN
        RAISE EXCEPTION 'Customer renumber migration left % orphan daily followups', orphan_count;
    END IF;
END $$;

COMMIT;

SELECT MIN(customer_id) AS min_customer_id, MAX(customer_id) AS max_customer_id, COUNT(*) AS customer_count
FROM customers;

