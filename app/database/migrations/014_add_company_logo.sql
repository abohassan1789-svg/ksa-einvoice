-- 014_add_company_logo.sql
-- Adds an embedded company logo to the companies master.
--
-- The logo is stored *inside the database* as raw image bytes (BYTEA) — never a
-- filesystem path — so a company always carries its own logo, and every printed
-- document (Saudi tax invoice, receipt voucher, …) can render the logo of the
-- exact company it is printed for. ``logo_mime`` records the image type
-- (e.g. ``image/png``) so the print layer can build a correct ``data:`` URI.
--
-- Both columns are nullable: a company without a logo simply prints without one
-- (the templates fall back to their placeholder / bundled asset).

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS logo      bytea,
    ADD COLUMN IF NOT EXISTS logo_mime varchar(64);
