"""Database access for the Saudi Phase-2 sales-invoice screen.

All SQL for the five ``sales_invoice*`` tables (and the read-only master-data
lookups it needs) lives here; the service and UI never touch the database
directly. Built on the shared psycopg ``Database`` helper, mirroring
``ProductsRepository`` / ``SecurityRepository``.

Safety contract
---------------
* This module **never** issues DDL. The five tables already exist in
  ``InvPhase2`` and are created/altered outside this application. No
  ``CREATE``/``ALTER``/``create_all`` is performed anywhere.
* Reads and searches go through the shared autocommit ``Database`` helper.
* Writes that must be atomic (save / update / delete / delete-all) run inside a
  single short-lived psycopg transaction (``_txn``) that commits on success and
  rolls back on any error.
* Master-data searches query the whole table with an indexed ``ILIKE`` / prefix
  filter and only then apply ``LIMIT`` — so a match on record #500 is found even
  though only the first 100 rows are pre-loaded into the pickers.
* Draft save/update never writes UUID, ICV, hashes, XML, QR, signatures or any
  ``sales_invoice_zatca_data`` / ``sales_invoice_zatca_submissions`` row.
"""

from __future__ import annotations

from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.config.database import build_database_url, runtime_connect_kwargs
from app.config.settings import get_settings
from app.database.db import Database
from app.models.sales_invoice import (
    INVOICE_INSERT_COLUMNS,
    INVOICE_SELECT_COLUMNS,
    LINE_INSERT_COLUMNS,
    LINE_SELECT_COLUMNS,
    STATUS_DRAFT,
    TBL_AUDIT_LOGS,
    TBL_INVOICES,
    TBL_LINES,
    TBL_ZATCA_DATA,
    TBL_ZATCA_SUBMISSIONS,
    ZATCA_DISPLAY_COLUMNS,
    ZATCA_GENERATE_COLUMNS,
    ZATCA_SUBMITTED_STATUSES,
)

_INV_SELECT = ", ".join(INVOICE_SELECT_COLUMNS)
_LINE_SELECT = ", ".join(LINE_SELECT_COLUMNS)
_ZATCA_SELECT = ", ".join(ZATCA_DISPLAY_COLUMNS)

# A draft is *protected* from deletion only once it has actually been sent
# toward ZATCA (a submission row, a submitted/accepted timestamp, or a submitted
# integration status). Locally-generated-but-unsubmitted Phase-2 data (UUID,
# ICV, hash, XML) does NOT protect the draft — it is regenerated on edit and
# cascade-deleted with the invoice — so users can still delete their own drafts.
_SUBMITTED_STATUS_SQL = ", ".join(f"'{status}'" for status in ZATCA_SUBMITTED_STATUSES)
_PROTECTED_PREDICATE = f"""
    EXISTS (SELECT 1 FROM {TBL_ZATCA_SUBMISSIONS} s WHERE s.invoice_id = i.id)
 OR EXISTS (
        SELECT 1 FROM {TBL_ZATCA_DATA} z
        WHERE z.invoice_id = i.id
          AND (z.submitted_at IS NOT NULL OR z.accepted_at IS NOT NULL
               OR z.integration_status IN ({_SUBMITTED_STATUS_SQL}))
    )
"""


class StaleInvoiceError(Exception):
    """Raised when an optimistic-concurrency update finds no matching row.

    Either another user changed the invoice (row_version moved) or it is no
    longer an editable draft.
    """


class SaudiSalesInvoiceRepository:
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
    # Master data — seller companies
    # ======================================================================
    def list_companies(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id, name_ar, name_en, commercial_registration, vat_number, address_ar "
            "FROM companies ORDER BY name_ar LIMIT %s",
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
    # Master data — products / items
    # ======================================================================
    def list_products(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id, item_code, item_name, price FROM products "
            "ORDER BY item_code LIMIT %s",
            [int(limit)],
        )

    def search_products(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        kw = (keyword or "").strip()
        if kw == "":
            return self.list_products(limit)
        like = f"%{kw}%"
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
    # Invoice numbering uniqueness (per seller company)
    # ======================================================================
    def invoice_number_exists(
        self, seller_company_id: int, invoice_number: str, exclude_id: int | None = None
    ) -> bool:
        if exclude_id is None:
            row = self.db.fetch_one(
                f"SELECT 1 FROM {TBL_INVOICES} "
                "WHERE seller_company_id = %s AND invoice_number = %s LIMIT 1",
                [int(seller_company_id), invoice_number],
            )
        else:
            row = self.db.fetch_one(
                f"SELECT 1 FROM {TBL_INVOICES} "
                "WHERE seller_company_id = %s AND invoice_number = %s AND id <> %s LIMIT 1",
                [int(seller_company_id), invoice_number, int(exclude_id)],
            )
        return row is not None

    # ======================================================================
    # Load / list invoices
    # ======================================================================
    def load_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        header = self.db.fetch_one(
            f"SELECT {_INV_SELECT} FROM {TBL_INVOICES} WHERE id = %s", [int(invoice_id)]
        )
        if header is None:
            return None
        lines = self.db.fetch_all(
            f"SELECT {_LINE_SELECT} FROM {TBL_LINES} WHERE invoice_id = %s "
            "ORDER BY line_number",
            [int(invoice_id)],
        )
        zatca = self.db.fetch_one(
            f"SELECT {_ZATCA_SELECT} FROM {TBL_ZATCA_DATA} WHERE invoice_id = %s",
            [int(invoice_id)],
        )
        return {"header": header, "lines": lines, "zatca": zatca}

    def search_invoices(
        self, keyword: str = "", status: str | None = None, limit: int = 300
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT id, invoice_number, seller_company_id, seller_name_ar_snapshot, "
            "customer_id, customer_name_snapshot, issue_datetime, document_status, "
            f"total_including_vat FROM {TBL_INVOICES}"
        )
        conds: list[str] = []
        params: list[Any] = []
        kw = (keyword or "").strip()
        if kw:
            like = f"%{kw}%"
            conds.append(
                "(invoice_number ILIKE %s OR seller_name_ar_snapshot ILIKE %s "
                "OR customer_name_snapshot ILIKE %s)"
            )
            params += [like, like, like]
        if status in (STATUS_DRAFT, "approved"):
            conds.append("document_status = %s")
            params.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(int(limit))
        return self.db.fetch_all(sql, params)

    def count_drafts(self, seller_company_id: int | None = None) -> int:
        if seller_company_id is None:
            row = self.db.fetch_one(
                f"SELECT COUNT(*) c FROM {TBL_INVOICES} WHERE document_status = %s",
                [STATUS_DRAFT],
            )
        else:
            row = self.db.fetch_one(
                f"SELECT COUNT(*) c FROM {TBL_INVOICES} "
                "WHERE document_status = %s AND seller_company_id = %s",
                [STATUS_DRAFT, int(seller_company_id)],
            )
        return int(row["c"]) if row else 0

    def has_zatca_material(self, invoice_id: int) -> bool:
        """True if the invoice is delete-protected (already sent toward ZATCA)."""
        row = self.db.fetch_one(
            f"SELECT 1 FROM {TBL_INVOICES} i WHERE i.id = %s AND ({_PROTECTED_PREDICATE}) LIMIT 1",
            [int(invoice_id)],
        )
        return row is not None

    # ======================================================================
    # Phase-2 chain reads (ICV counter + previous invoice hash, per seller)
    # ======================================================================
    def next_icv(self, seller_company_id: int) -> int:
        """The next invoice-counter value for a seller (max so far + 1)."""
        row = self.db.fetch_one(
            f"SELECT COALESCE(MAX(z.invoice_counter_value), 0) + 1 AS n "
            f"FROM {TBL_ZATCA_DATA} z JOIN {TBL_INVOICES} i ON i.id = z.invoice_id "
            "WHERE i.seller_company_id = %s",
            [int(seller_company_id)],
        )
        return int(row["n"]) if row and row["n"] is not None else 1

    def previous_invoice_hash(self, seller_company_id: int) -> str | None:
        """The invoice_hash of the seller's latest counter value (PIH source)."""
        row = self.db.fetch_one(
            f"SELECT z.invoice_hash AS h FROM {TBL_ZATCA_DATA} z "
            f"JOIN {TBL_INVOICES} i ON i.id = z.invoice_id "
            "WHERE i.seller_company_id = %s AND z.invoice_hash IS NOT NULL "
            "ORDER BY z.invoice_counter_value DESC NULLS LAST LIMIT 1",
            [int(seller_company_id)],
        )
        return row["h"] if row else None

    # ======================================================================
    # Writes (transactional)
    # ======================================================================
    def insert_invoice(
        self,
        header: dict[str, Any],
        lines: list[dict[str, Any]],
        audit: dict[str, Any] | None = None,
        zatca: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a draft header + its lines (+ optional Phase-2 data + audit).

        All in one transaction. ``zatca`` carries only non-cryptographic data
        (UUID / ICV / PIH / XML / hash / QR); no stamp, signature or key.
        """
        collist = ", ".join(INVOICE_INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(INVOICE_INSERT_COLUMNS))
        values = [header.get(col) for col in INVOICE_INSERT_COLUMNS]
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {TBL_INVOICES} ({collist}) VALUES ({placeholders}) "
                    f"RETURNING {_INV_SELECT}",
                    values,
                )
                stored = cur.fetchone()
                invoice_id = stored["id"]
                self._insert_lines(cur, invoice_id, lines)
                if zatca is not None:
                    self._upsert_zatca(cur, invoice_id, zatca)
                if audit is not None:
                    self._insert_audit(cur, invoice_id, audit)
        return self.load_invoice(invoice_id)

    def update_invoice(
        self,
        invoice_id: int,
        expected_row_version: int,
        header_changes: dict[str, Any],
        lines: list[dict[str, Any]],
        audit: dict[str, Any] | None = None,
        zatca: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a draft's header + replace its lines atomically.

        Guarded by ``row_version`` (optimistic concurrency) and
        ``document_status = 'draft'``. Raises :class:`StaleInvoiceError` if no row
        matches (concurrent change or no longer a draft).
        """
        set_cols = list(header_changes.keys())
        assignments = ", ".join(f"{col} = %s" for col in set_cols)
        params: list[Any] = [header_changes[col] for col in set_cols]
        params += [int(invoice_id), int(expected_row_version)]
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {TBL_INVOICES} SET {assignments}, "
                    "row_version = row_version + 1, updated_at = now() "
                    f"WHERE id = %s AND row_version = %s AND document_status = '{STATUS_DRAFT}' "
                    f"RETURNING {_INV_SELECT}",
                    params,
                )
                stored = cur.fetchone()
                if stored is None:
                    raise StaleInvoiceError(
                        "الفاتورة تم تعديلها من مستخدم آخر أو لم تعد مسودة."
                    )
                cur.execute(
                    f"DELETE FROM {TBL_LINES} WHERE invoice_id = %s", [int(invoice_id)]
                )
                self._insert_lines(cur, invoice_id, lines)
                if zatca is not None:
                    self._upsert_zatca(cur, invoice_id, zatca)
                if audit is not None:
                    self._insert_audit(cur, invoice_id, audit)
        return self.load_invoice(invoice_id)

    def delete_draft(
        self, invoice_id: int, performed_by: int | None = None
    ) -> bool:
        """Delete one eligible draft. Returns True if a row was deleted.

        Physically refuses to delete an approved invoice or any draft that has
        protected Phase-2 material. Audit rows survive (FK is ON DELETE SET NULL)
        and a ``delete_draft`` entry is recorded.
        """
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TBL_INVOICES} i "
                    f"WHERE i.id = %s AND i.document_status = '{STATUS_DRAFT}' "
                    f"AND NOT ({_PROTECTED_PREDICATE}) "
                    "RETURNING i.id, i.invoice_number, i.seller_company_id",
                    [int(invoice_id)],
                )
                row = cur.fetchone()
                if row is None:
                    return False
                # invoice is gone -> audit references it with NULL invoice_id + snapshots.
                self._insert_audit(
                    cur,
                    None,
                    {
                        "action": "delete_draft",
                        "old_status": STATUS_DRAFT,
                        "new_status": None,
                        "performed_by": performed_by,
                        "invoice_number_snapshot": row["invoice_number"],
                        "seller_company_id_snapshot": row["seller_company_id"],
                        "details": {"invoice_id": row["id"]},
                    },
                )
                return True

    def delete_all_eligible_drafts(
        self, seller_company_id: int | None = None, performed_by: int | None = None
    ) -> dict[str, int]:
        """Delete every eligible draft in one transaction.

        Returns ``{"deleted": n, "protected": m}`` where *protected* is the number
        of drafts skipped because they carry Phase-2 material. Approved invoices
        are never counted or touched.
        """
        seller_clause = ""
        seller_params: list[Any] = []
        if seller_company_id is not None:
            seller_clause = " AND i.seller_company_id = %s"
            seller_params = [int(seller_company_id)]

        total_drafts = self.count_drafts(seller_company_id)
        with self._txn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TBL_INVOICES} i "
                    f"WHERE i.document_status = '{STATUS_DRAFT}' "
                    f"AND NOT ({_PROTECTED_PREDICATE}){seller_clause} "
                    "RETURNING i.id, i.invoice_number, i.seller_company_id",
                    seller_params,
                )
                deleted_rows = cur.fetchall()
                for row in deleted_rows:
                    self._insert_audit(
                        cur,
                        None,
                        {
                            "action": "delete_all_drafts",
                            "old_status": STATUS_DRAFT,
                            "new_status": None,
                            "performed_by": performed_by,
                            "invoice_number_snapshot": row["invoice_number"],
                            "seller_company_id_snapshot": row["seller_company_id"],
                            "details": {"invoice_id": row["id"]},
                        },
                    )
        deleted = len(deleted_rows)
        return {"deleted": deleted, "protected": max(total_drafts - deleted, 0)}

    # -- internal insert helpers --------------------------------------------
    def _insert_lines(
        self, cur: psycopg.Cursor, invoice_id: int, lines: Iterable[dict[str, Any]]
    ) -> None:
        collist = ", ".join(LINE_INSERT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(LINE_INSERT_COLUMNS))
        for index, line in enumerate(lines, start=1):
            row = dict(line)
            row["invoice_id"] = invoice_id
            row["line_number"] = index
            cur.execute(
                f"INSERT INTO {TBL_LINES} ({collist}) VALUES ({placeholders})",
                [row.get(col) for col in LINE_INSERT_COLUMNS],
            )

    def _upsert_zatca(
        self, cur: psycopg.Cursor, invoice_id: int, zatca: dict[str, Any]
    ) -> None:
        """Insert or refresh the non-cryptographic Phase-2 row for an invoice.

        Only ``ZATCA_GENERATE_COLUMNS`` are written — the cryptographic-stamp /
        digital-signature / public-key columns are never touched here.
        """
        data = dict(zatca)
        data["invoice_id"] = invoice_id
        cols = ", ".join(ZATCA_GENERATE_COLUMNS)
        placeholders = ", ".join(["%s"] * len(ZATCA_GENERATE_COLUMNS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}"
            for col in ZATCA_GENERATE_COLUMNS
            if col != "invoice_id"
        )
        cur.execute(
            f"INSERT INTO {TBL_ZATCA_DATA} ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT (invoice_id) DO UPDATE SET {updates}, updated_at = now()",
            [data.get(col) for col in ZATCA_GENERATE_COLUMNS],
        )

    def _insert_audit(
        self, cur: psycopg.Cursor, invoice_id: int | None, audit: dict[str, Any]
    ) -> None:
        details = audit.get("details")
        cur.execute(
            f"INSERT INTO {TBL_AUDIT_LOGS} "
            "(invoice_id, invoice_number_snapshot, seller_company_id_snapshot, action, "
            " old_status, new_status, performed_by, details) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                invoice_id,
                audit.get("invoice_number_snapshot"),
                audit.get("seller_company_id_snapshot"),
                audit["action"],
                audit.get("old_status"),
                audit.get("new_status"),
                audit.get("performed_by"),
                Json(details) if details is not None else None,
            ],
        )


__all__ = ["SaudiSalesInvoiceRepository", "StaleInvoiceError"]
