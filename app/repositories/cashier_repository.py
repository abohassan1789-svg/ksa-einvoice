"""Database access for the experimental Cashier/POS screen.

All SQL for the two ``cashier_invoice*`` tables (and the read-only master-data
lookups it needs) lives here; the service and UI never touch the database
directly. Built on the shared psycopg ``Database`` helper, mirroring
``SaudiSalesInvoiceRepository`` / ``ProductsRepository``.

Safety contract
---------------
* This module **never** issues DDL. The two Cashier tables already exist in
  ``InvPhase2`` (migration 015) and are created/altered outside this
  application. No ``CREATE``/``ALTER``/``create_all`` is performed anywhere.
* Reads and searches go through the shared autocommit ``Database`` helper.
* Writes that must be atomic (save / update / issue / delete) run inside a
  single short-lived psycopg transaction (``_txn``) that commits on success and
  rolls back on any error.
* Master-data searches query the whole table with an indexed ``ILIKE`` / prefix
  filter and only then apply ``LIMIT`` — so a match on record #500 is found even
  though only the first page is pre-loaded into the screen.
* The invoice number is produced by the database sequence
  ``cashier_invoice_number_seq`` (``nextval`` is atomic, concurrency-safe);
  never ``MAX(id)+1`` / ``MAX(invoice_number)+1``.
* Nothing here writes accounting, inventory, receipt or ``sales_invoice*`` rows.
* The ZATCA columns are written by exactly one method,
  :meth:`CashierRepository.sign_zatca_atomically`, and only for an ISSUED invoice
  that is not signed yet (see ``app.services.cashier_zatca_signing``). Every
  other path still leaves them alone.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

import psycopg
from psycopg.rows import dict_row

from app.config.database import build_database_url, runtime_connect_kwargs
from app.config.settings import get_settings
from app.database.db import Database
from app.models.cashier_invoice import (
    INVOICE_INSERT_COLUMNS,
    INVOICE_SELECT_COLUMNS,
    ISSUE_HEADER_COLUMNS,
    LINE_INSERT_COLUMNS,
    LINE_SELECT_COLUMNS,
    STATUS_DRAFT,
    STATUS_ISSUED,
    TBL_CASHIER_INVOICES,
    TBL_CASHIER_LINES,
)
from app.services.saudi_zatca_generator import GENESIS_PIH

_INV_SELECT = ", ".join(INVOICE_SELECT_COLUMNS)
_LINE_SELECT = ", ".join(LINE_SELECT_COLUMNS)

# Arbitrary but fixed namespace for the per-company advisory lock used while
# assigning a cashier invoice's ICV/PIH, so it can never collide with another
# module's advisory locks.
_ZATCA_LOCK_NAMESPACE = 8150


class StaleCashierInvoiceError(Exception):
    """Raised when a guarded update/issue finds no matching draft row.

    Either the invoice was changed/issued by someone else (a second window or
    device) or it is no longer an editable draft.
    """


class CashierRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        # Plain-psycopg URL for opening short-lived transactional connections.
        self._url = build_database_url(get_settings()).replace(
            "postgresql+psycopg://", "postgresql://"
        )

    # -- transactional connection -------------------------------------------
    def _txn(self) -> psycopg.Connection:
        """Open a NON-autocommit connection (commit/rollback + close via ``with``)."""
        return psycopg.connect(self._url, row_factory=dict_row, **runtime_connect_kwargs())

    # ======================================================================
    # Master data — products / items (reused as-is; never created/duplicated)
    # ======================================================================
    def list_products(self, limit: int = 60) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id, item_code, item_name, price FROM products "
            "ORDER BY item_code LIMIT %s",
            [int(limit)],
        )

    def search_products(self, keyword: str, limit: int = 60) -> list[dict[str, Any]]:
        kw = (keyword or "").strip()
        if kw == "":
            return self.list_products(limit)
        like = f"%{kw}%"
        # Database-side filter by item name OR item code (prefix). The item-code
        # prefix match doubles as the future barcode-lookup path.
        return self.db.fetch_all(
            "SELECT id, item_code, item_name, price FROM products "
            "WHERE item_name ILIKE %s OR CAST(item_code AS text) LIKE %s "
            "ORDER BY item_code LIMIT %s",
            [like, f"{kw}%", int(limit)],
        )

    def get_product(self, product_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT id, item_code, item_name, price FROM products WHERE id = %s",
            [int(product_id)],
        )

    # ======================================================================
    # Master data — customers
    # ======================================================================
    def list_customers(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT customer_id, customer_name, phone_number, vat_number, cr, address "
            "FROM customers ORDER BY customer_id LIMIT %s",
            [int(limit)],
        )

    def search_customers(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        kw = (keyword or "").strip()
        if kw == "":
            return self.list_customers(limit)
        like = f"%{kw}%"
        return self.db.fetch_all(
            "SELECT customer_id, customer_name, phone_number, vat_number, cr, address "
            "FROM customers WHERE COALESCE(customer_name,'') ILIKE %s "
            "OR CAST(customer_id AS text) LIKE %s OR COALESCE(phone_number,'') ILIKE %s "
            "OR COALESCE(vat_number,'') ILIKE %s OR COALESCE(cr,'') ILIKE %s "
            "ORDER BY customer_id LIMIT %s",
            [like, f"{kw}%", like, like, like, int(limit)],
        )

    def get_customer(self, customer_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT customer_id, customer_name, phone_number, vat_number, cr, address "
            "FROM customers WHERE customer_id = %s",
            [int(customer_id)],
        )

    # ======================================================================
    # Master data — companies (sellers)
    # ======================================================================
    def list_companies(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id, name_ar, name_en, commercial_registration, vat_number, "
            "address_ar FROM companies ORDER BY name_ar LIMIT %s",
            [int(limit)],
        )

    def search_companies(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        kw = (keyword or "").strip()
        if kw == "":
            return self.list_companies(limit)
        like = f"%{kw}%"
        return self.db.fetch_all(
            "SELECT id, name_ar, name_en, commercial_registration, vat_number, address_ar "
            "FROM companies "
            "WHERE name_ar ILIKE %s OR COALESCE(name_en,'') ILIKE %s "
            "OR vat_number ILIKE %s OR COALESCE(commercial_registration,'') ILIKE %s "
            "OR CAST(id AS text) LIKE %s "
            "ORDER BY name_ar LIMIT %s",
            [like, like, like, like, f"{kw}%", int(limit)],
        )

    def get_company(self, company_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            "SELECT id, name_ar, name_en, commercial_registration, vat_number, "
            "address_ar, address_en, logo, logo_mime FROM companies WHERE id = %s",
            [int(company_id)],
        )

    def get_user_display_name(self, user_id: int | None) -> str | None:
        """Presentational cashier name for the receipt (full name, else username)."""
        if user_id in (None, ""):
            return None
        row = self.db.fetch_one(
            "SELECT full_name, username FROM app_users WHERE id = %s", [int(user_id)]
        )
        if row is None:
            return None
        return (row.get("full_name") or "").strip() or row.get("username")

    # ======================================================================
    # Invoice numbering (concurrency-safe DB sequence — never MAX+1)
    # ======================================================================
    def reserve_invoice_number(self) -> str:
        """Reserve the next Cashier invoice number from the DB sequence.

        ``nextval`` is atomic, so two stations reserving at the same instant get
        two distinct numbers. Rendered exactly like the table default
        (``CINV-000001``) so the reserved value matches what would be stored.
        """
        row = self.db.fetch_one(
            "SELECT 'CINV-' || to_char(nextval('cashier_invoice_number_seq'), 'FM000000') AS n"
        )
        return row["n"]

    # ======================================================================
    # Load / list invoices
    # ======================================================================
    def load_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        header = self.db.fetch_one(
            f"SELECT {_INV_SELECT} FROM {TBL_CASHIER_INVOICES} WHERE id = %s",
            [int(invoice_id)],
        )
        if header is None:
            return None
        lines = self.db.fetch_all(
            f"SELECT {_LINE_SELECT} FROM {TBL_CASHIER_LINES} "
            "WHERE cashier_invoice_id = %s ORDER BY line_number",
            [int(invoice_id)],
        )
        return {"header": header, "lines": lines}

    def list_drafts(self, limit: int = 300) -> list[dict[str, Any]]:
        """List DRAFT invoices only, with company/customer names for display."""
        return self.db.fetch_all(
            "SELECT i.id, i.invoice_number, i.invoice_date, i.grand_total, i.created_at, "
            "c.name_ar AS company_name, cu.customer_name AS customer_name "
            f"FROM {TBL_CASHIER_INVOICES} i "
            "LEFT JOIN companies c ON c.id = i.company_id "
            "LEFT JOIN customers cu ON cu.customer_id = i.customer_id "
            "WHERE i.invoice_status = %s "
            "ORDER BY i.id DESC LIMIT %s",
            [STATUS_DRAFT, int(limit)],
        )

    def search_invoices(
        self,
        keyword: str = "",
        company_id: int | None = None,
        customer_id: int | None = None,
        status: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Search ALL cashier invoices (any status) for the F1 lookup.

        Filters (all optional, ANDed): invoice-number ``ILIKE`` keyword, exact
        company id, exact customer id, and exact status. Company/customer names
        are joined in for display.
        """
        sql = (
            "SELECT i.id, i.invoice_number, i.invoice_date, i.invoice_datetime, "
            "i.invoice_status, i.grand_total, "
            "c.name_ar AS company_name, cu.customer_name AS customer_name "
            f"FROM {TBL_CASHIER_INVOICES} i "
            "LEFT JOIN companies c ON c.id = i.company_id "
            "LEFT JOIN customers cu ON cu.customer_id = i.customer_id"
        )
        conds: list[str] = []
        params: list[Any] = []
        kw = (keyword or "").strip()
        if kw:
            conds.append("i.invoice_number ILIKE %s")
            params.append(f"%{kw}%")
        if company_id not in (None, ""):
            conds.append("i.company_id = %s")
            params.append(int(company_id))
        if customer_id not in (None, ""):
            conds.append("i.customer_id = %s")
            params.append(int(customer_id))
        if status:
            conds.append("i.invoice_status = %s")
            params.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY i.id DESC LIMIT %s"
        params.append(int(limit))
        return self.db.fetch_all(sql, params)

    def count_drafts(self) -> int:
        row = self.db.fetch_one(
            f"SELECT COUNT(*) c FROM {TBL_CASHIER_INVOICES} WHERE invoice_status = %s",
            [STATUS_DRAFT],
        )
        return int(row["c"]) if row else 0

    # ======================================================================
    # Writes (transactional)
    # ======================================================================
    def insert_draft(
        self, header: dict[str, Any], lines: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Insert a draft header + its lines in one transaction.

        ``header`` must already carry the reserved ``invoice_number`` and a
        required ``company_id`` (the column is NOT NULL). No ZATCA / crypto /
        accounting / inventory row is written.
        """
        collist = ", ".join(INVOICE_INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(INVOICE_INSERT_COLUMNS))
        values = [header.get(col) for col in INVOICE_INSERT_COLUMNS]
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {TBL_CASHIER_INVOICES} ({collist}) VALUES ({placeholders}) "
                    f"RETURNING {_INV_SELECT}",
                    values,
                )
                stored = cur.fetchone()
                invoice_id = stored["id"]
                self._insert_lines(cur, invoice_id, lines)
        return self.load_invoice(invoice_id)

    def update_draft(
        self,
        invoice_id: int,
        header_changes: dict[str, Any],
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update an existing draft's header + replace its lines atomically.

        Guarded by ``invoice_status = 'DRAFT'`` so an ISSUED/CANCELLED invoice can
        never be edited through this path. Raises :class:`StaleCashierInvoiceError`
        if no draft row matches.
        """
        set_cols = list(header_changes.keys())
        assignments = ", ".join(f"{col} = %s" for col in set_cols)
        params: list[Any] = [header_changes[col] for col in set_cols]
        params += [int(invoice_id)]
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {TBL_CASHIER_INVOICES} SET {assignments}, updated_at = now() "
                    f"WHERE id = %s AND invoice_status = '{STATUS_DRAFT}' "
                    f"RETURNING {_INV_SELECT}",
                    params,
                )
                stored = cur.fetchone()
                if stored is None:
                    raise StaleCashierInvoiceError(
                        "لا يمكن تعديل الفاتورة: ربما تم إتمامها أو تعديلها من نافذة أخرى."
                    )
                cur.execute(
                    f"DELETE FROM {TBL_CASHIER_LINES} WHERE cashier_invoice_id = %s",
                    [int(invoice_id)],
                )
                self._insert_lines(cur, invoice_id, lines)
        return self.load_invoice(invoice_id)

    def issue_invoice(
        self,
        invoice_id: int,
        header_changes: dict[str, Any],
        lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Locally issue a draft: atomic status flip DRAFT -> ISSUED + snapshots.

        The single guarded UPDATE (``WHERE ... invoice_status = 'DRAFT'``) is the
        concurrency guard: if two windows/devices try to issue the same draft,
        only the first flips it; the second matches no row and raises
        :class:`StaleCashierInvoiceError`. The issue date/time/datetime are set
        to the moment of issue by the database. No QR / XML / ZATCA call / print /
        accounting / inventory / receipt happens here.
        """
        set_cols = list(header_changes.keys())
        assignments = ", ".join(f"{col} = %s" for col in set_cols)
        params: list[Any] = [header_changes[col] for col in set_cols]
        params += [int(invoice_id)]
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {TBL_CASHIER_INVOICES} SET {assignments}, "
                    f"invoice_status = '{STATUS_ISSUED}', "
                    "invoice_date = CURRENT_DATE, invoice_time = localtime, "
                    "invoice_datetime = now(), updated_at = now() "
                    f"WHERE id = %s AND invoice_status = '{STATUS_DRAFT}' "
                    f"RETURNING {_INV_SELECT}",
                    params,
                )
                stored = cur.fetchone()
                if stored is None:
                    raise StaleCashierInvoiceError(
                        "تعذّر إتمام الفاتورة: ربما تم إتمامها مسبقًا من نافذة أخرى."
                    )
                # Persist the final line set (in case of unsaved edits).
                cur.execute(
                    f"DELETE FROM {TBL_CASHIER_LINES} WHERE cashier_invoice_id = %s",
                    [int(invoice_id)],
                )
                self._insert_lines(cur, invoice_id, lines)
        return self.load_invoice(invoice_id)

    def unissue_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Revert an ISSUED invoice back to DRAFT so it can be edited again.

        Atomic and guarded by ``invoice_status = 'ISSUED'`` — if the invoice is
        not currently issued (or was changed elsewhere) no row matches and
        :class:`StaleCashierInvoiceError` is raised. The seller/buyer snapshots
        and lines are left as-is; they are rebuilt on the next local issue.
        """
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {TBL_CASHIER_INVOICES} "
                    f"SET invoice_status = '{STATUS_DRAFT}', updated_at = now() "
                    f"WHERE id = %s AND invoice_status = '{STATUS_ISSUED}' "
                    f"RETURNING {_INV_SELECT}",
                    [int(invoice_id)],
                )
                stored = cur.fetchone()
                if stored is None:
                    raise StaleCashierInvoiceError(
                        "تعذّر إلغاء الاعتماد: الفاتورة ليست مُصدرة أو تغيّرت من نافذة أخرى."
                    )
        return self.load_invoice(invoice_id)

    # -- ZATCA local signing (the only writer of the zatca_* columns) --------
    def sign_zatca_atomically(
        self,
        invoice_id: int,
        build_fields: Callable[..., dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Generate + store the Phase-2 columns for one ISSUED, unsigned invoice.

        The whole operation runs in a single transaction so the per-company
        ICV/PIH chain stays consistent when two stations issue at the same
        instant: a transaction-scoped advisory lock on the company serialises the
        counter read, ``build_fields(header, lines, icv=, pih=)`` computes the XML
        / hash / signature from the *stored* row, and the guarded UPDATE writes
        them back.

        The ``zatca_invoice_hash IS NULL`` guard makes this idempotent: an
        already-signed invoice matches no row, so a reprint can never re-sign it
        and change its QR. Returns the reloaded invoice, or ``None`` if the
        invoice was not an unsigned ISSUED row.
        """
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_INV_SELECT} FROM {TBL_CASHIER_INVOICES} "
                    f"WHERE id = %s AND invoice_status = '{STATUS_ISSUED}' "
                    "AND zatca_invoice_hash IS NULL FOR UPDATE",
                    [int(invoice_id)],
                )
                header = cur.fetchone()
                if header is None:
                    return None

                # Serialise the per-company ICV/PIH chain for this transaction.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    [_ZATCA_LOCK_NAMESPACE, int(header["company_id"])],
                )
                cur.execute(
                    f"SELECT COALESCE(MAX(zatca_icv), 0) + 1 AS icv FROM {TBL_CASHIER_INVOICES} "
                    "WHERE company_id = %s",
                    [int(header["company_id"])],
                )
                icv = int(cur.fetchone()["icv"])
                cur.execute(
                    f"SELECT zatca_invoice_hash AS h FROM {TBL_CASHIER_INVOICES} "
                    "WHERE company_id = %s AND zatca_invoice_hash IS NOT NULL "
                    "ORDER BY zatca_icv DESC NULLS LAST LIMIT 1",
                    [int(header["company_id"])],
                )
                previous = cur.fetchone()
                pih = (previous["h"] if previous else None) or GENESIS_PIH

                cur.execute(
                    f"SELECT {_LINE_SELECT} FROM {TBL_CASHIER_LINES} "
                    "WHERE cashier_invoice_id = %s ORDER BY line_number",
                    [int(invoice_id)],
                )
                lines = cur.fetchall()

                fields = build_fields(header, lines, icv=icv, pih=pih)
                assignments = ", ".join(f"{col} = %s" for col in fields)
                params: list[Any] = list(fields.values()) + [int(invoice_id)]
                cur.execute(
                    f"UPDATE {TBL_CASHIER_INVOICES} SET {assignments}, updated_at = now() "
                    f"WHERE id = %s AND invoice_status = '{STATUS_ISSUED}' "
                    "AND zatca_invoice_hash IS NULL",
                    params,
                )
        return self.load_invoice(invoice_id)

    def record_print(
        self, invoice_id: int, qr_base64: str | None = None
    ) -> dict[str, Any]:
        """Record a successful print of an ISSUED invoice.

        Updates ONLY the three print-related columns permitted by the printing
        scope: ``print_count`` (+1), ``last_printed_at`` (now), and — only when a
        validated production QR is supplied — ``zatca_qr_base64``. Guarded by
        ``invoice_status = 'ISSUED'`` so a draft/cancelled invoice can never have
        its print counter bumped. No financial, cryptographic, line, snapshot or
        status column is touched. Raises :class:`StaleCashierInvoiceError` if the
        invoice is not currently ISSUED.
        """
        with self._txn() as conn:
            with conn.cursor() as cur:
                if qr_base64 is not None:
                    cur.execute(
                        f"UPDATE {TBL_CASHIER_INVOICES} "
                        "SET print_count = print_count + 1, last_printed_at = now(), "
                        "zatca_qr_base64 = %s "
                        f"WHERE id = %s AND invoice_status = '{STATUS_ISSUED}' "
                        f"RETURNING {_INV_SELECT}",
                        [qr_base64, int(invoice_id)],
                    )
                else:
                    cur.execute(
                        f"UPDATE {TBL_CASHIER_INVOICES} "
                        "SET print_count = print_count + 1, last_printed_at = now() "
                        f"WHERE id = %s AND invoice_status = '{STATUS_ISSUED}' "
                        f"RETURNING {_INV_SELECT}",
                        [int(invoice_id)],
                    )
                stored = cur.fetchone()
                if stored is None:
                    raise StaleCashierInvoiceError(
                        "تعذّر تسجيل الطباعة: الفاتورة ليست مُصدرة أو تغيّرت من نافذة أخرى."
                    )
        return self.load_invoice(invoice_id)

    def delete_draft(self, invoice_id: int) -> bool:
        """Delete one DRAFT invoice. Returns True if a row was deleted.

        Physically refuses to delete an ISSUED/CANCELLED invoice (the
        ``invoice_status = 'DRAFT'`` guard). The FK cascade removes only this
        invoice's own detail lines.
        """
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TBL_CASHIER_INVOICES} "
                    f"WHERE id = %s AND invoice_status = '{STATUS_DRAFT}' RETURNING id",
                    [int(invoice_id)],
                )
                row = cur.fetchone()
                return row is not None

    def count_invoices_by_status(self) -> dict[str, int]:
        """``{status: count}`` over every cashier invoice (empty statuses absent)."""
        rows = self.db.fetch_all(
            f"SELECT invoice_status, COUNT(*) c FROM {TBL_CASHIER_INVOICES} "
            "GROUP BY invoice_status"
        )
        return {row["invoice_status"]: int(row["c"]) for row in rows}

    def delete_all_invoices(self, include_issued: bool = False) -> dict[str, int]:
        """Delete every cashier invoice in one transaction.

        With ``include_issued=False`` (the default) only DRAFT rows go, under the
        same ``invoice_status = 'DRAFT'`` guard :meth:`delete_draft` relies on, so
        a completed sale survives. ``include_issued=True`` drops that guard and
        wipes **everything, issued sales included** — irreversible, and only ever
        driven by an explicit user confirmation.

        Returns ``{"deleted": n, "issued_deleted": i, "protected": m}``; *protected*
        counts the rows left behind (issued/cancelled ones when they are spared).
        """
        counts = self.count_invoices_by_status()
        total = sum(counts.values())
        guard = "" if include_issued else f" WHERE invoice_status = '{STATUS_DRAFT}'"
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TBL_CASHIER_INVOICES}{guard} RETURNING id, invoice_status",
                )
                deleted_rows = cur.fetchall()
        deleted = len(deleted_rows)
        issued_deleted = sum(1 for row in deleted_rows if row["invoice_status"] == STATUS_ISSUED)
        return {
            "deleted": deleted,
            "issued_deleted": issued_deleted,
            "protected": max(total - deleted, 0),
        }

    # -- internal insert helper ---------------------------------------------
    def _insert_lines(
        self, cur: psycopg.Cursor, invoice_id: int, lines: Iterable[dict[str, Any]]
    ) -> None:
        collist = ", ".join(LINE_INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(LINE_INSERT_COLUMNS))
        for index, line in enumerate(lines, start=1):
            row = dict(line)
            row["cashier_invoice_id"] = invoice_id
            row["line_number"] = index
            cur.execute(
                f"INSERT INTO {TBL_CASHIER_LINES} ({collist}) VALUES ({placeholders})",
                [row.get(col) for col in LINE_INSERT_COLUMNS],
            )


__all__ = ["CashierRepository", "StaleCashierInvoiceError"]
