"""Schema definition and domain constants for the Companies module (الشركات).

This module holds the *structure* (DDL) and reusable domain constants only — no
SQL execution and no business logic. It mirrors the established ``products``
module (see ``app/models/product.py``): the raw ``COMPANIES_SCHEMA_SQL`` below is
the single source of truth and is copied verbatim into
``app/database/migrations/011_create_companies_schema.sql`` (upgrade) and reversed
in ``app/database/migrations/011_downgrade_companies_schema.sql`` (downgrade).

Every statement in ``COMPANIES_SCHEMA_SQL`` uses ``CREATE ... IF NOT EXISTS`` (or
is otherwise idempotent) and nothing is dropped, reset, or deleted, so it is safe
to run repeatedly and additively against a database that may already hold a
``companies`` table.

Design notes
------------
* ``id`` is a PostgreSQL IDENTITY column (sequence-backed). Ids are never computed
  with ``MAX(id) + 1`` — the same convention the rest of the project follows.
* ``commercial_registration`` and ``vat_number`` are stored as ``varchar`` (never
  integer/numeric) so leading zeros are preserved. Each carries a UNIQUE index.
* Required text fields (``name_ar``, ``commercial_registration``, ``vat_number``)
  cannot be blank: a BEFORE trigger trims surrounding whitespace and CHECK
  constraints then reject empty strings.
* Optional text fields (``name_en``, ``address_ar``, ``address_en``) are trimmed
  and normalised to ``NULL`` when blank by the same trigger.
* ``created_at`` / ``updated_at`` are timezone-aware (``timestamptz``) with
  database-side defaults; ``updated_at`` is kept accurate by a BEFORE UPDATE
  trigger regardless of the caller.

This module also exposes a lightweight SQLAlchemy ORM model (``Company``) mapped
to the same table for callers that prefer ORM access. As with ``Product``, the
live application talks to PostgreSQL through raw psycopg, so the ORM model is
optional and self-contained.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Identity,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

# --- domain constants -------------------------------------------------------

TABLE_NAME = "companies"

# Reasonable maximum lengths. Names are display strings; the registration/VAT
# numbers are short identifier strings (kept generous to accommodate any
# jurisdiction's format while never being numeric).
NAME_MAX_LENGTH = 255
REGISTRATION_MAX_LENGTH = 50

# Columns loaded for list/detail views (in display order).
LIST_COLUMNS: tuple[str, ...] = (
    "id",
    "name_ar",
    "name_en",
    "commercial_registration",
    "vat_number",
    "address_ar",
    "address_en",
    "created_at",
    "updated_at",
)

# Columns the application is allowed to write on insert/update. Note the absence
# of ``id`` (IDENTITY) and the audit columns (database-managed).
WRITABLE_COLUMNS: tuple[str, ...] = (
    "name_ar",
    "name_en",
    "commercial_registration",
    "vat_number",
    "address_ar",
    "address_en",
)


# --- raw DDL (source of truth, mirrored by migration 011) -------------------

COMPANIES_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id                       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name_ar                  varchar({NAME_MAX_LENGTH}) NOT NULL,
    name_en                  varchar({NAME_MAX_LENGTH}),
    commercial_registration  varchar({REGISTRATION_MAX_LENGTH}) NOT NULL,
    vat_number               varchar({REGISTRATION_MAX_LENGTH}) NOT NULL,
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
    ON {TABLE_NAME} (commercial_registration);
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_vat_number
    ON {TABLE_NAME} (vat_number);

-- Search-by-name index on the primary (Arabic) name. A trigram index makes the
-- app's "contains" search (ILIKE '%kw%') fast; if pg_trgm cannot be installed
-- the plain btree below is still created and used for equality / prefix / ORDER
-- BY. This mirrors the products module's approach.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS idx_companies_name_ar_trgm
        ON {TABLE_NAME} USING gin (name_ar gin_trgm_ops);
EXCEPTION WHEN insufficient_privilege OR feature_not_supported THEN
    RAISE NOTICE 'pg_trgm unavailable; skipping trigram index on companies (btree only).';
END $$;

CREATE INDEX IF NOT EXISTS idx_companies_name_ar ON {TABLE_NAME} (name_ar);

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

DROP TRIGGER IF EXISTS trg_companies_normalize ON {TABLE_NAME};
CREATE TRIGGER trg_companies_normalize
    BEFORE INSERT OR UPDATE ON {TABLE_NAME}
    FOR EACH ROW EXECUTE FUNCTION companies_normalize();
"""


# --- optional SQLAlchemy ORM model (same table) -----------------------------

Base = declarative_base()


class Company(Base):
    """SQLAlchemy ORM mapping for the ``companies`` table.

    Optional convenience for ORM callers; the running app uses raw psycopg like
    the other modules. ``id`` is database-generated (IDENTITY) and the audit
    columns are database-managed.
    """

    __tablename__ = TABLE_NAME

    id = Column(Integer, Identity(always=True), primary_key=True)
    name_ar = Column(String(NAME_MAX_LENGTH), nullable=False)
    name_en = Column(String(NAME_MAX_LENGTH), nullable=True)
    commercial_registration = Column(String(REGISTRATION_MAX_LENGTH), nullable=False)
    vat_number = Column(String(REGISTRATION_MAX_LENGTH), nullable=False)
    address_ar = Column(Text, nullable=True)
    address_en = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name_ar)) > 0", name="ck_companies_name_ar_not_blank"
        ),
        CheckConstraint(
            "char_length(btrim(commercial_registration)) > 0",
            name="ck_companies_commercial_registration_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(vat_number)) > 0", name="ck_companies_vat_number_not_blank"
        ),
        Index("uq_companies_commercial_registration", "commercial_registration", unique=True),
        Index("uq_companies_vat_number", "vat_number", unique=True),
        Index("idx_companies_name_ar", "name_ar"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Company id={self.id} name_ar={self.name_ar!r} cr={self.commercial_registration!r}>"
