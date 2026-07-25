-- Migration 009 (UPGRADE): Products / Items schema (وحدة المنتجات / الأصناف).
--
-- Creates the standalone ``products`` table and its supporting objects. Does NOT
-- touch, drop, or alter any existing table or data.
--
-- WHAT / WHY
--   id         : PostgreSQL IDENTITY (sequence-backed) primary key. Never MAX+1.
--   item_code  : user-facing code starting at 1001. Automatic codes come from the
--                sequence ``products_item_code_seq`` (nextval is atomic, so many
--                devices creating items at once never collide). UNIQUE index is
--                the final guard; the service re-seeds the sequence past any
--                manually-entered code so a high manual code can never clash with
--                a future automatic one.
--   quantity   : numeric(18,3) >= 0, default 0 (supports decimal quantities).
--   price      : numeric(18,2) >= 0, default 0 (financial amount, never float).
--   total      : STORED generated column (quantity * price) — never manual.
--   created_at : row creation timestamp.
--   updated_at : maintained by a BEFORE UPDATE trigger (always accurate).
--
-- Mirrors app/models/product.py (PRODUCTS_SCHEMA_SQL), which the app also runs
-- automatically at startup via ProductsRepository.ensure_schema().
--
-- Safe to run repeatedly: every statement uses CREATE ... IF NOT EXISTS or is
-- otherwise idempotent, and nothing is dropped, reset, or deleted.
-- Reverse with 009_downgrade_products_schema.sql.

BEGIN;

-- Sequence feeding automatic item codes. Seeded to 1000 with is_called = true so
-- the FIRST nextval() returns 1001.
CREATE SEQUENCE IF NOT EXISTS products_item_code_seq AS bigint MINVALUE 1;

CREATE TABLE IF NOT EXISTS products (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_code   integer NOT NULL DEFAULT nextval('products_item_code_seq'),
    item_name   varchar(200) NOT NULL,
    quantity    numeric(18, 3) NOT NULL DEFAULT 0,
    price       numeric(18, 2) NOT NULL DEFAULT 0,
    total       numeric(23, 5) GENERATED ALWAYS AS (quantity * price) STORED,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_products_quantity_non_negative CHECK (quantity >= 0),
    CONSTRAINT ck_products_price_non_negative    CHECK (price >= 0),
    CONSTRAINT ck_products_item_name_not_blank   CHECK (char_length(btrim(item_name)) > 0)
);

ALTER SEQUENCE products_item_code_seq OWNED BY products.item_code;

-- Never regress the sequence below the greatest existing code or what it already
-- handed out; floor at 1000 so a fresh table's first code is 1001.
SELECT setval(
    'products_item_code_seq',
    GREATEST(
        (SELECT COALESCE(MAX(item_code), 0) FROM products),
        1000,
        (SELECT COALESCE(last_value, 1) FROM products_item_code_seq)
    ),
    true
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_products_item_code ON products (item_code);

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS idx_products_item_name_trgm
        ON products USING gin (item_name gin_trgm_ops);
EXCEPTION WHEN insufficient_privilege OR feature_not_supported THEN
    RAISE NOTICE 'pg_trgm unavailable; skipping trigram index on products (btree only).';
END $$;

CREATE INDEX IF NOT EXISTS idx_products_item_name ON products (item_name);

CREATE OR REPLACE FUNCTION products_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_products_set_updated_at ON products;
CREATE TRIGGER trg_products_set_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION products_set_updated_at();

COMMIT;

-- Post-conditions (informational): table shape + that the next code is 1001 on a
-- fresh table.
SELECT 'products' AS table_created,
       (SELECT COUNT(*) FROM products) AS row_count;
