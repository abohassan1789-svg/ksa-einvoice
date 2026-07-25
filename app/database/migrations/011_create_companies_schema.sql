-- Migration 011 (UPGRADE): Companies schema (وحدة الشركات).
--
-- Creates the standalone ``companies`` table and its supporting objects. Does NOT
-- touch, drop, or alter any existing table or data. If a ``companies`` table
-- already exists this migration is additive and idempotent — every statement
-- uses CREATE ... IF NOT EXISTS / CREATE OR REPLACE, so no existing company data
-- is deleted or modified.
--
-- WHAT / WHY
--   id                      : PostgreSQL IDENTITY (sequence-backed) primary key.
--                             Never MAX+1.
--   name_ar                 : Arabic company name. Required, varchar(255), and
--                             may not be blank (trimmed by trigger, guarded by
--                             CHECK).
--   name_en                 : English company name. Optional, varchar(255).
--   commercial_registration : Commercial Registration number stored as a STRING
--                             (never integer) to preserve leading zeros. Required
--                             and UNIQUE.
--   vat_number              : Tax / VAT number stored as a STRING (never integer).
--                             Required and UNIQUE.
--   address_ar / address_en : Optional Arabic / English addresses (text).
--   created_at              : row creation timestamp (timestamptz).
--   updated_at              : maintained by the BEFORE UPDATE trigger (always
--                             accurate).
--
-- Mirrors app/models/company.py (COMPANIES_SCHEMA_SQL). Reverse with
-- 011_downgrade_companies_schema.sql.
--
-- Safe to run repeatedly: every statement uses CREATE ... IF NOT EXISTS or is
-- otherwise idempotent, and nothing is dropped, reset, or deleted.

BEGIN;

CREATE TABLE IF NOT EXISTS companies (
    id                       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name_ar                  varchar(255) NOT NULL,
    name_en                  varchar(255),
    commercial_registration  varchar(50) NOT NULL,
    vat_number               varchar(50) NOT NULL,
    address_ar               text,
    address_en               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_companies_name_ar_not_blank
        CHECK (char_length(btrim(name_ar)) > 0),
    CONSTRAINT ck_companies_commercial_registration_not_blank
        CHECK (char_length(btrim(commercial_registration)) > 0),
    CONSTRAINT ck_companies_vat_number_not_blank
        CHECK (char_length(btrim(vat_number)) > 0)
);

-- Unique identifiers (each index is both the uniqueness guard and the lookup
-- path for exact-match searches by registration / VAT number).
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_commercial_registration
    ON companies (commercial_registration);
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_vat_number
    ON companies (vat_number);

-- Search-by-name index on the primary (Arabic) name. A trigram index makes the
-- app's "contains" search (ILIKE '%kw%') fast; if pg_trgm cannot be installed the
-- plain btree below is still created and used for equality / prefix / ORDER BY.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS idx_companies_name_ar_trgm
        ON companies USING gin (name_ar gin_trgm_ops);
EXCEPTION WHEN insufficient_privilege OR feature_not_supported THEN
    RAISE NOTICE 'pg_trgm unavailable; skipping trigram index on companies (btree only).';
END $$;

CREATE INDEX IF NOT EXISTS idx_companies_name_ar ON companies (name_ar);

-- Normalise on write: trim required text (CHECK constraints then reject blanks),
-- trim optional text and collapse blank -> NULL, and keep updated_at accurate on
-- every UPDATE whoever performs it.
CREATE OR REPLACE FUNCTION companies_normalize() RETURNS trigger AS $$
BEGIN
    NEW.name_ar := btrim(NEW.name_ar);
    NEW.commercial_registration := btrim(NEW.commercial_registration);
    NEW.vat_number := btrim(NEW.vat_number);
    NEW.name_en := NULLIF(btrim(NEW.name_en), '');
    NEW.address_ar := NULLIF(btrim(NEW.address_ar), '');
    NEW.address_en := NULLIF(btrim(NEW.address_en), '');
    IF TG_OP = 'UPDATE' THEN
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_companies_normalize ON companies;
CREATE TRIGGER trg_companies_normalize
    BEFORE INSERT OR UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION companies_normalize();

COMMIT;

-- Post-conditions (informational): table shape + current row count.
SELECT 'companies' AS table_created,
       (SELECT COUNT(*) FROM companies) AS row_count;
