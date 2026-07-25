-- 014_downgrade_company_logo.sql
-- Reverses 014_add_company_logo.sql by dropping the embedded logo columns.

ALTER TABLE companies
    DROP COLUMN IF EXISTS logo,
    DROP COLUMN IF EXISTS logo_mime;
