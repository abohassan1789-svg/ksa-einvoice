"""Database access for the Products / Items module (المنتجات / الأصناف).

All SQL for ``products`` lives here; the service and UI never touch the database
directly. Built on the shared psycopg ``Database`` helper, mirroring
``BackupRepository`` / ``AttachmentsRepository`` / ``SecurityRepository``.

Concurrency contract: automatic item codes come from the PostgreSQL sequence
``products_item_code_seq`` via ``nextval`` (atomic — two devices creating an item
at the same instant get different codes). The UNIQUE index ``uq_products_item_code``
is the final guard. When a caller saves a *manually* entered code, the sequence
is bumped past it (``reserve_item_code_ceiling``) so a high manual code can never
collide with a future automatic one.

``total`` is a generated column and is never written here; ``id`` is IDENTITY and
is likewise database-generated. Both are read back with ``RETURNING``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.database.db import Database
from app.models.product import (
    FIRST_ITEM_CODE,
    ITEM_CODE_SEQUENCE,
    LIST_COLUMNS,
    PRODUCTS_SCHEMA_SQL,
)

_LIST_COLUMNS_SQL = ", ".join(LIST_COLUMNS)


class ProductsRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the products table + sequence + indexes if missing (safe to repeat)."""
        self.db.execute_script(PRODUCTS_SCHEMA_SQL)

    # --- item-code generation ----------------------------------------------
    def next_item_code(self) -> int:
        """Reserve and return the next automatic item code atomically.

        Uses ``nextval('products_item_code_seq')`` (the first value is
        ``FIRST_ITEM_CODE`` = 1001). The value is consumed, so concurrent callers
        never receive the same code. Because a manually-saved code re-seeds the
        sequence past itself, the returned code is free of previously-saved manual
        codes too; the caller still treats the UNIQUE index as the final guard.
        """
        row = self.db.fetch_one(f"SELECT nextval('{ITEM_CODE_SEQUENCE}') AS n")
        return int(row["n"]) if row else FIRST_ITEM_CODE

    def peek_next_item_code(self) -> int:
        """Return the code the next automatic insert *would* use, without consuming it.

        Intended for pre-filling the code field in a (future) UI before the user
        edits/saves. This is advisory only: it is not a reservation, so the final
        code is always validated for uniqueness at save time.
        """
        row = self.db.fetch_one(
            f"SELECT last_value, is_called FROM {ITEM_CODE_SEQUENCE}"
        )
        if not row or row.get("last_value") is None:
            return FIRST_ITEM_CODE
        last_value = int(row["last_value"])
        candidate = last_value + 1 if row.get("is_called") else last_value
        return max(candidate, FIRST_ITEM_CODE)

    def reserve_item_code_ceiling(self, code: int) -> None:
        """Ensure future automatic codes stay strictly above ``code``.

        Called after saving a manually-entered code so an automatic code can
        never duplicate it. ``setval(seq, GREATEST(last_value, code), true)``
        never regresses the sequence.
        """
        self.db.execute(
            f"SELECT setval('{ITEM_CODE_SEQUENCE}', "
            f"GREATEST((SELECT last_value FROM {ITEM_CODE_SEQUENCE}), %s), true)",
            [int(code)],
        )

    # --- existence checks ---------------------------------------------------
    def item_code_exists(self, item_code: int, exclude_id: int | None = None) -> bool:
        """True if ``item_code`` is already used (optionally ignoring one row id)."""
        if exclude_id is None:
            row = self.db.fetch_one(
                "SELECT 1 FROM products WHERE item_code = %s LIMIT 1", [int(item_code)]
            )
        else:
            row = self.db.fetch_one(
                "SELECT 1 FROM products WHERE item_code = %s AND id <> %s LIMIT 1",
                [int(item_code), int(exclude_id)],
            )
        return row is not None

    # --- writes -------------------------------------------------------------
    def insert_product(
        self,
        item_name: str,
        quantity: Decimal,
        price: Decimal,
        item_code: int | None = None,
    ) -> dict[str, Any]:
        """Insert one product and return the full stored row (incl. generated total).

        When ``item_code`` is None the database DEFAULT (``nextval``) assigns the
        next automatic code. When it is given, the explicit code is stored and the
        sequence is bumped past it so future automatic codes cannot collide.
        ``id`` and ``total`` are database-generated and read back with RETURNING.
        """
        if item_code is None:
            row = self.db.fetch_one(
                "INSERT INTO products (item_name, quantity, price) "
                "VALUES (%s, %s, %s) "
                f"RETURNING {_LIST_COLUMNS_SQL}",
                [item_name, quantity, price],
            )
        else:
            row = self.db.fetch_one(
                "INSERT INTO products (item_code, item_name, quantity, price) "
                "VALUES (%s, %s, %s, %s) "
                f"RETURNING {_LIST_COLUMNS_SQL}",
                [int(item_code), item_name, quantity, price],
            )
            self.reserve_item_code_ceiling(int(item_code))
        assert row is not None  # INSERT ... RETURNING always yields a row
        return row

    def update_product(self, product_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Update the given writable columns of one product; return the new row.

        ``total`` recomputes automatically (generated column) and ``updated_at``
        is refreshed by the BEFORE UPDATE trigger. Returns None if no row matched.
        Passing an empty ``fields`` is a no-op that returns the current row.
        """
        allowed = ("item_code", "item_name", "quantity", "price")
        sets = [c for c in allowed if c in fields]
        if not sets:
            return self.get_by_id(product_id)

        assignments = ", ".join(f"{c} = %s" for c in sets)
        params: list[Any] = [fields[c] for c in sets]
        params.append(int(product_id))
        row = self.db.fetch_one(
            f"UPDATE products SET {assignments} WHERE id = %s "
            f"RETURNING {_LIST_COLUMNS_SQL}",
            params,
        )
        if row is not None and "item_code" in sets and fields["item_code"] is not None:
            self.reserve_item_code_ceiling(int(fields["item_code"]))
        return row

    # --- reads --------------------------------------------------------------
    def get_by_id(self, product_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"SELECT {_LIST_COLUMNS_SQL} FROM products WHERE id = %s", [int(product_id)]
        )

    def get_by_item_code(self, item_code: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"SELECT {_LIST_COLUMNS_SQL} FROM products WHERE item_code = %s",
            [int(item_code)],
        )

    def list_products(self, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """Return products ordered by item_code (ascending)."""
        return self.db.fetch_all(
            f"SELECT {_LIST_COLUMNS_SQL} FROM products "
            "ORDER BY item_code ASC LIMIT %s OFFSET %s",
            [int(limit), int(offset)],
        )

    def search_products(self, keyword: str, limit: int = 500) -> list[dict[str, Any]]:
        """Search by item name (contains, case-insensitive) or exact/prefix item code.

        A numeric keyword also matches item codes that start with it, so typing
        "10" finds 1001, 1002, ... as well as any name containing "10".
        """
        kw = (keyword or "").strip()
        if kw == "":
            return self.list_products(limit=limit)

        like = f"%{kw}%"
        if kw.isdigit():
            return self.db.fetch_all(
                f"SELECT {_LIST_COLUMNS_SQL} FROM products "
                "WHERE item_name ILIKE %s OR CAST(item_code AS text) LIKE %s "
                "ORDER BY item_code ASC LIMIT %s",
                [like, f"{kw}%", int(limit)],
            )
        return self.db.fetch_all(
            f"SELECT {_LIST_COLUMNS_SQL} FROM products "
            "WHERE item_name ILIKE %s ORDER BY item_code ASC LIMIT %s",
            [like, int(limit)],
        )
