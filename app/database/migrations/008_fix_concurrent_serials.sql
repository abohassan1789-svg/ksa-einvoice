-- Migration 008: concurrency-safe serial generation + supporting indexes.
--
-- WHY: the five core tables (places, customers, employees, case_statuses,
-- daily_followups) used a plain ``integer PRIMARY KEY`` whose next value was
-- computed in the application with ``SELECT COALESCE(MAX(pk),0)+1`` on an
-- autocommit connection. With many devices saving at once, two clients read the
-- same MAX and try to INSERT the same id: one wins, the rest fail with a
-- duplicate-key error and LOSE the user's save. A 60-client simulation lost 52
-- of 60 saves this way.
--
-- FIX: give each table a PostgreSQL SEQUENCE, seeded past the current MAX, wired
-- as the column DEFAULT and OWNED BY the column. ``nextval`` is atomic, so every
-- concurrent INSERT gets its own id with no locking and no collisions. The
-- application now inserts WITHOUT the id and reads it back with RETURNING.
--
-- Also seeds a sequence for the ATT-<n> attachment codes (same race, guarded
-- today only by a UNIQUE index that turns the collision into an error).
--
-- Safe to run repeatedly: sequences use IF NOT EXISTS and are re-seeded with
-- GREATEST(existing, current-max) so a re-run never hands back a used number.

BEGIN;

-- --- core-table identity sequences ----------------------------------------
DO $$
DECLARE
    r record;
    seq_name text;
    cur_max bigint;
    seed bigint;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('places',          'place_id',          0),
            ('customers',       'customer_id',    1000),   -- customers start at 1001
            ('employees',       'employee_id',       0),
            ('case_statuses',   'case_status_id',    0),
            ('daily_followups', 'daily_followup_id', 0)
        ) AS t(tbl, pk, floor)
    LOOP
        seq_name := r.tbl || '_' || r.pk || '_seq';

        EXECUTE format('CREATE SEQUENCE IF NOT EXISTS %I', seq_name);

        EXECUTE format('SELECT COALESCE(MAX(%I), 0) FROM %I', r.pk, r.tbl)
            INTO cur_max;

        -- Never regress below what the sequence may have already handed out.
        seed := GREATEST(cur_max, r.floor,
                         (SELECT last_value FROM pg_sequences
                          WHERE schemaname = 'public' AND sequencename = seq_name));

        -- Seed the sequence so the next id is correct even on an EMPTY table:
        --   seed >= 1 -> setval(seed, true)  => next nextval() returns seed + 1
        --   seed  < 1 -> setval(1, false)    => next nextval() returns 1
        -- (setval requires a value >= the sequence minimum of 1, so a plain
        --  setval(seq, 0, true) would error on a fresh, dataless database.)
        IF seed < 1 THEN
            PERFORM setval(seq_name, 1, false);
        ELSE
            PERFORM setval(seq_name, seed, true);
        END IF;

        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I SET DEFAULT nextval(%L)',
                       r.tbl, r.pk, seq_name);
        EXECUTE format('ALTER SEQUENCE %I OWNED BY %I.%I',
                       seq_name, r.tbl, r.pk);
    END LOOP;
END $$;

-- --- attachment code sequence (ATT-<n>) -----------------------------------
CREATE SEQUENCE IF NOT EXISTS crm_attachment_code_seq;
DO $$
DECLARE
    computed bigint;
BEGIN
    SELECT GREATEST(
        (SELECT COALESCE(MAX(CAST(substring(attachment_code from '^ATT-([0-9]+)$') AS integer)), 0)
         FROM crm_attachments
         WHERE attachment_code ~ '^ATT-[0-9]+$'),
        (SELECT COALESCE(last_value, 0) FROM pg_sequences
         WHERE schemaname = 'public' AND sequencename = 'crm_attachment_code_seq')
    ) INTO computed;

    -- Same empty-safe rule as the core-table sequences above.
    IF computed < 1 THEN
        PERFORM setval('crm_attachment_code_seq', 1, false);
    ELSE
        PERFORM setval('crm_attachment_code_seq', computed, true);
    END IF;
END $$;

-- --- supporting indexes ----------------------------------------------------
-- FK columns on daily_followups are already indexed (migration 001). These add
-- coverage for the columns the CRM actually searches / orders / reports on.

-- Trigram indexes make the app's primary "contains" search (ILIKE '%kw%') fast
-- across tens of thousands of rows. Guarded: if pg_trgm cannot be installed
-- (insufficient privilege), the migration still succeeds and the plain btree
-- indexes below are used for equality / prefix / ORDER BY.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS idx_customers_name_trgm
        ON customers USING gin (customer_name gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS idx_customers_phone_trgm
        ON customers USING gin (phone_number gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS idx_employees_name_trgm
        ON employees USING gin (employee_name gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS idx_daily_followups_notes_trgm
        ON daily_followups USING gin (notes gin_trgm_ops);
EXCEPTION WHEN insufficient_privilege OR feature_not_supported THEN
    RAISE NOTICE 'pg_trgm unavailable; skipping trigram indexes (btree only).';
END $$;

CREATE INDEX IF NOT EXISTS idx_customers_name        ON customers (customer_name);
CREATE INDEX IF NOT EXISTS idx_customers_area_number ON customers (area_number);
CREATE INDEX IF NOT EXISTS idx_employees_name        ON employees (employee_name);
CREATE INDEX IF NOT EXISTS idx_employees_phone       ON employees (phone_number);
CREATE INDEX IF NOT EXISTS idx_case_statuses_name    ON case_statuses (case_status_name);
CREATE INDEX IF NOT EXISTS idx_places_number         ON places (place_number);
CREATE INDEX IF NOT EXISTS idx_daily_followups_customer_name ON daily_followups (buyer_name);
CREATE INDEX IF NOT EXISTS idx_daily_followups_year_month
    ON daily_followups (follow_up_year, follow_up_month);

COMMIT;
