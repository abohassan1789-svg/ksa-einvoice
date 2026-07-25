"""Schema definition and domain constants for the Products / Items module (المنتجات / الأصناف).

This module holds the *structure* (DDL) and reusable domain constants only — no
SQL execution and no business logic. The repository layer runs
``PRODUCTS_SCHEMA_SQL`` (every statement uses ``CREATE ... IF NOT EXISTS`` / is
otherwise idempotent, so it is safe to run repeatedly and never drops, resets, or
deletes anything). The same SQL is mirrored in
``app/database/migrations/009_create_products_schema.sql`` (upgrade) and reversed
in ``app/database/migrations/009_downgrade_products_schema.sql`` (downgrade).

Design notes
------------
* ``id`` is a PostgreSQL IDENTITY column (sequence-backed). Ids are never
  computed with ``MAX(id) + 1``.
* ``item_code`` is a user-facing code that starts at ``1001``. Automatic codes
  come from the dedicated sequence ``products_item_code_seq`` (seeded so the
  first ``nextval`` returns ``1001``). ``nextval`` is atomic, so concurrent
  creators never receive the same code. A UNIQUE index is the final guard, and
  the service re-seeds the sequence past any manually-entered code so a high
  manual code can never collide with a future automatic one.
* ``total`` is a STORED generated column (``quantity * price``); it can never be
  set to a manually-entered value.
* Money uses ``numeric`` (never floating point). ``updated_at`` is maintained by
  a BEFORE UPDATE trigger so it is always accurate regardless of the caller.

This module also exposes a lightweight SQLAlchemy ORM model (``Product``) mapped
to the same table, for callers that prefer ORM access. The live application talks
to PostgreSQL through the raw-psycopg ``ProductsRepository`` (mirroring the other
modules), so the ORM model is optional and self-contained.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

# --- domain constants -------------------------------------------------------

TABLE_NAME = "products"

# Automatic item codes start here: the sequence is seeded so the first
# ``nextval`` returns FIRST_ITEM_CODE.
FIRST_ITEM_CODE = 1001
ITEM_CODE_SEQUENCE = "products_item_code_seq"

# Numeric precision/scale.
QUANTITY_PRECISION, QUANTITY_SCALE = 18, 3   # supports decimal quantities
MONEY_PRECISION, MONEY_SCALE = 18, 2         # financial amounts (NUMERIC(18,2))

# Columns loaded for list/detail views (in display order). ``total`` is read
# from the generated column; it is never written by the application.
LIST_COLUMNS: tuple[str, ...] = (
    "id",
    "item_code",
    "item_name",
    "quantity",
    "price",
    "total",
    "created_at",
    "updated_at",
)

# Columns the application is allowed to write on insert/update. Note the absence
# of ``id`` (IDENTITY) and ``total`` (generated) — both are database-managed.
WRITABLE_COLUMNS: tuple[str, ...] = ("item_code", "item_name", "quantity", "price")


# --- raw DDL (source of truth, mirrored by migration 009) -------------------

PRODUCTS_SCHEMA_SQL = f"""
-- Sequence that feeds automatic item codes. Seeded to {FIRST_ITEM_CODE - 1} with
-- is_called = true so the FIRST nextval() returns {FIRST_ITEM_CODE}. Re-seeding
-- on a re-run never regresses below what it (or an existing row) already used.
CREATE SEQUENCE IF NOT EXISTS {ITEM_CODE_SEQUENCE} AS bigint MINVALUE 1;

CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_code   integer NOT NULL DEFAULT nextval('{ITEM_CODE_SEQUENCE}'),
    item_name   varchar(200) NOT NULL,
    quantity    numeric({QUANTITY_PRECISION}, {QUANTITY_SCALE}) NOT NULL DEFAULT 0,
    price       numeric({MONEY_PRECISION}, {MONEY_SCALE}) NOT NULL DEFAULT 0,
    total       numeric({MONEY_PRECISION + QUANTITY_SCALE + 2}, {MONEY_SCALE + QUANTITY_SCALE})
                    GENERATED ALWAYS AS (quantity * price) STORED,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_products_quantity_non_negative CHECK (quantity >= 0),
    CONSTRAINT ck_products_price_non_negative    CHECK (price >= 0),
    CONSTRAINT ck_products_item_name_not_blank   CHECK (char_length(btrim(item_name)) > 0)
);

-- Bind the sequence to the column so it is owned/dropped together and reported
-- by pg_get_serial_sequence. (No-op if already owned.)
ALTER SEQUENCE {ITEM_CODE_SEQUENCE} OWNED BY {TABLE_NAME}.item_code;

-- Seed the sequence so the FIRST automatic code is {FIRST_ITEM_CODE}, and on any
-- re-run never regress below the greatest existing code or what it already
-- handed out. Floor {FIRST_ITEM_CODE - 1} dominates the fresh-table case
-- (max = 0), so setval(..., true) makes the next nextval return {FIRST_ITEM_CODE}.
SELECT setval(
    '{ITEM_CODE_SEQUENCE}',
    GREATEST(
        (SELECT COALESCE(MAX(item_code), 0) FROM {TABLE_NAME}),
        {FIRST_ITEM_CODE - 1},
        (SELECT COALESCE(last_value, 1) FROM {ITEM_CODE_SEQUENCE})
    ),
    true
);

-- Unique code (final guard for both automatic and manually-entered codes).
CREATE UNIQUE INDEX IF NOT EXISTS uq_products_item_code ON {TABLE_NAME} (item_code);

-- Search-by-name index. A trigram index makes the app's "contains" search
-- (ILIKE '%kw%') fast; if pg_trgm cannot be installed the plain btree below is
-- still created and used for equality / prefix / ORDER BY.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE INDEX IF NOT EXISTS idx_products_item_name_trgm
        ON {TABLE_NAME} USING gin (item_name gin_trgm_ops);
EXCEPTION WHEN insufficient_privilege OR feature_not_supported THEN
    RAISE NOTICE 'pg_trgm unavailable; skipping trigram index on products (btree only).';
END $$;

CREATE INDEX IF NOT EXISTS idx_products_item_name ON {TABLE_NAME} (item_name);

-- Keep updated_at accurate on every UPDATE, whoever performs it.
CREATE OR REPLACE FUNCTION products_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_products_set_updated_at ON {TABLE_NAME};
CREATE TRIGGER trg_products_set_updated_at
    BEFORE UPDATE ON {TABLE_NAME}
    FOR EACH ROW EXECUTE FUNCTION products_set_updated_at();
"""


# --- optional SQLAlchemy ORM model (same table) -----------------------------

Base = declarative_base()


class Product(Base):
    """SQLAlchemy ORM mapping for the ``products`` table.

    Optional convenience for ORM callers; the running app uses
    ``ProductsRepository`` (raw psycopg). ``total`` is a read-only generated
    column and ``id`` is database-generated.
    """

    __tablename__ = TABLE_NAME

    id = Column(Integer, Identity(always=True), primary_key=True)
    item_code = Column(
        Integer,
        nullable=False,
        server_default=text(f"nextval('{ITEM_CODE_SEQUENCE}')"),
    )
    item_name = Column(String(200), nullable=False)
    quantity = Column(
        Numeric(QUANTITY_PRECISION, QUANTITY_SCALE),
        nullable=False,
        server_default=text("0"),
    )
    price = Column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        server_default=text("0"),
    )
    total = Column(
        Numeric(MONEY_PRECISION + QUANTITY_SCALE + 2, MONEY_SCALE + QUANTITY_SCALE),
        Computed("quantity * price", persisted=True),
    )
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_products_quantity_non_negative"),
        CheckConstraint("price >= 0", name="ck_products_price_non_negative"),
        CheckConstraint(
            "char_length(btrim(item_name)) > 0", name="ck_products_item_name_not_blank"
        ),
        Index("uq_products_item_code", "item_code", unique=True),
        Index("idx_products_item_name", "item_name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Product id={self.id} item_code={self.item_code} name={self.item_name!r}>"
