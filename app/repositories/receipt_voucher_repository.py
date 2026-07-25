"""Database access for the Receipt Vouchers module (سندات القبض).

All SQL for ``receipt_vouchers`` lives here; the numbering service and (future)
screen never touch the database directly. Built on the shared psycopg
``Database`` helper, mirroring ``ProductsRepository`` / ``CompaniesRepository``.

Concurrency contract: automatic voucher numbers come from the PostgreSQL
sequence ``receipt_voucher_number_seq`` via ``nextval`` (atomic — two devices
creating a voucher at the same instant get different numbers). The UNIQUE index
``uq_receipt_vouchers_voucher_number`` is the final guard. When a caller saves a
*manually* entered ``PA-<n>`` number, the sequence is bumped past ``<n>``
(``reserve_number_ceiling``) so a future automatic number can never collide.

``id`` is IDENTITY and ``voucher_number`` has a database DEFAULT
(``PA-<seq>``); both are read back with ``RETURNING``. ``updated_at`` is kept
current by the BEFORE UPDATE trigger and is never written here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.database.db import Database
from app.models.receipt_voucher import (
    FIRST_VOUCHER_SEQ,
    LIST_COLUMNS,
    RECEIPT_VOUCHERS_SCHEMA_SQL,
    TABLE_NAME,
    VOUCHER_NUMBER_SEQUENCE,
    WRITABLE_COLUMNS,
)

_LIST_COLUMNS_SQL = ", ".join(LIST_COLUMNS)


class ReceiptVoucherRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the table + sequence + indexes + trigger if missing (safe to repeat)."""
        self.db.execute_script(RECEIPT_VOUCHERS_SCHEMA_SQL)

    # --- voucher-number sequence -------------------------------------------
    def next_sequence_value(self) -> int:
        """Reserve and return the next raw sequence value atomically.

        Uses ``nextval('receipt_voucher_number_seq')`` (the first value is
        ``FIRST_VOUCHER_SEQ`` = 1 -> PA-001). The value is consumed, so
        concurrent callers never receive the same number.
        """
        row = self.db.fetch_one(f"SELECT nextval('{VOUCHER_NUMBER_SEQUENCE}') AS n")
        return int(row["n"]) if row else FIRST_VOUCHER_SEQ

    def peek_next_sequence_value(self) -> int:
        """Return the sequence value the next automatic insert *would* use.

        Advisory only (not a reservation): the final number is always validated
        for uniqueness at save time.
        """
        row = self.db.fetch_one(
            f"SELECT last_value, is_called FROM {VOUCHER_NUMBER_SEQUENCE}"
        )
        if not row or row.get("last_value") is None:
            return FIRST_VOUCHER_SEQ
        last_value = int(row["last_value"])
        candidate = last_value + 1 if row.get("is_called") else last_value
        return max(candidate, FIRST_VOUCHER_SEQ)

    def reserve_number_ceiling(self, value: int) -> None:
        """Ensure future automatic numbers stay strictly above ``value``.

        Called after saving a manually-entered ``PA-<value>`` so an automatic
        number can never duplicate it. ``setval(seq, GREATEST(last_value, value),
        true)`` never regresses the sequence.
        """
        self.db.execute(
            f"SELECT setval('{VOUCHER_NUMBER_SEQUENCE}', "
            f"GREATEST((SELECT last_value FROM {VOUCHER_NUMBER_SEQUENCE}), %s), true)",
            [int(value)],
        )

    # --- existence checks ---------------------------------------------------
    def voucher_number_exists(self, voucher_number: str, exclude_id: int | None = None) -> bool:
        """True if ``voucher_number`` is already used (optionally ignoring one row id)."""
        if exclude_id is None:
            row = self.db.fetch_one(
                f"SELECT 1 FROM {TABLE_NAME} WHERE voucher_number = %s LIMIT 1",
                [voucher_number],
            )
        else:
            row = self.db.fetch_one(
                f"SELECT 1 FROM {TABLE_NAME} WHERE voucher_number = %s AND id <> %s LIMIT 1",
                [voucher_number, int(exclude_id)],
            )
        return row is not None

    # --- writes -------------------------------------------------------------
    def insert_voucher(
        self,
        *,
        customer_id: int,
        company_id: int,
        payment_type: str,
        amount: Decimal,
        voucher_number: str | None = None,
        voucher_date: Any = None,
        description: str | None = None,
        created_by: int | None = None,
        updated_by: int | None = None,
    ) -> dict[str, Any]:
        """Insert one voucher and return the full stored row.

        When ``voucher_number`` is None the database DEFAULT (``PA-<nextval>``)
        assigns the next automatic number. When it is given, the explicit number
        is stored (uniqueness is still enforced by the UNIQUE index); the caller
        should reserve the ceiling via the numbering service so future automatic
        numbers cannot collide. When ``voucher_date`` is None the database
        DEFAULT (CURRENT_DATE) applies. ``id`` and the effective ``voucher_number``
        are read back with RETURNING.
        """
        cols: list[str] = ["customer_id", "company_id", "payment_type", "amount"]
        params: list[Any] = [int(customer_id), int(company_id), payment_type, amount]
        if voucher_number is not None:
            cols.append("voucher_number")
            params.append(voucher_number)
        if voucher_date is not None:
            cols.append("voucher_date")
            params.append(voucher_date)
        if description is not None:
            cols.append("description")
            params.append(description)
        if created_by is not None:
            cols.append("created_by")
            params.append(int(created_by))
        if updated_by is not None:
            cols.append("updated_by")
            params.append(int(updated_by))

        placeholders = ", ".join(["%s"] * len(cols))
        row = self.db.fetch_one(
            f"INSERT INTO {TABLE_NAME} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"RETURNING {_LIST_COLUMNS_SQL}",
            params,
        )
        assert row is not None  # INSERT ... RETURNING always yields a row
        return row

    def update_voucher(self, voucher_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Update the given writable columns of one voucher; return the new row.

        ``updated_at`` is refreshed by the BEFORE UPDATE trigger. Returns None if
        no row matched. Passing an empty/unknown ``fields`` is a no-op that
        returns the current row.
        """
        sets = [c for c in WRITABLE_COLUMNS if c in fields]
        if not sets:
            return self.get_by_id(voucher_id)

        assignments = ", ".join(f"{c} = %s" for c in sets)
        params: list[Any] = [fields[c] for c in sets]
        params.append(int(voucher_id))
        return self.db.fetch_one(
            f"UPDATE {TABLE_NAME} SET {assignments} WHERE id = %s "
            f"RETURNING {_LIST_COLUMNS_SQL}",
            params,
        )

    # --- reads --------------------------------------------------------------
    def get_by_id(self, voucher_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"SELECT {_LIST_COLUMNS_SQL} FROM {TABLE_NAME} WHERE id = %s", [int(voucher_id)]
        )

    def get_by_number(self, voucher_number: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"SELECT {_LIST_COLUMNS_SQL} FROM {TABLE_NAME} WHERE voucher_number = %s",
            [voucher_number],
        )

    def list_vouchers(self, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """Return vouchers ordered by id (ascending)."""
        return self.db.fetch_all(
            f"SELECT {_LIST_COLUMNS_SQL} FROM {TABLE_NAME} "
            "ORDER BY id ASC LIMIT %s OFFSET %s",
            [int(limit), int(offset)],
        )
