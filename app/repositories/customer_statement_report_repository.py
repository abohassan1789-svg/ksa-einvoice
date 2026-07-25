"""Database layer for the Customer Statement report (كشف حساب العميل).

This is the ONLY place SQL for this report lives. It returns plain, read-only row
dicts; all Arabic descriptions, money formatting and totals are computed in the
service layer (``app/services/customer_statement_report_service.py``), and the UI
never sees SQL.

The statement combines the existing sources with a single ``UNION ALL`` query
(one database round-trip, all filtering done in the database — never in Python):

* ``sales_invoices``   -> one **debit** row per invoice (``total_including_vat``).
* ``sales_invoices`` (cash only) -> one paired **credit** settlement row
  (``total_including_vat``): a **cash** sale is paid immediately, so per the
  user's requirement it shows both the sale (debit "فاتورة مبيعات نقدية") and its
  settlement (credit "سداد فاتورة مبيعات"). This settlement row is a display-only
  derived movement — NO receipt voucher is created in the database. A **credit**
  (آجل) invoice gets only the debit row and stays outstanding until an actual
  receipt voucher is entered.
* ``receipt_vouchers`` -> one **credit** row per voucher (``amount``).

A ``UNION ALL`` (not ``UNION``) is used deliberately: an invoice, its cash
settlement and any receipt share the same date/customer and must never be
de-duplicated. Every movement is tagged with a stable ``source_type`` /
``source_id`` and a ``source_sequence`` (0 invoice debit, 1 cash settlement,
2 receipt voucher). The invoice debit and its cash settlement carry the SAME
``source_id`` (both ``si.id``), so the statement orders by ``source_id`` before
``source_sequence`` — each cash invoice is followed immediately by its own
settlement, not by every other invoice first (see ``fetch_statement``).

Read-only contract: this module only ``SELECT``s. It never inserts, updates or
deletes any invoice, voucher, customer or accounting row.

Exact tables / fields used
--------------------------
* ``sales_invoices``:  ``id``, ``invoice_number``, ``issue_datetime``,
  ``customer_id``, ``payment_type``, ``total_including_vat``, ``document_status``.
  ALL invoice statuses are included (no ``document_status`` filter).
* ``receipt_vouchers``: ``id``, ``voucher_number``, ``voucher_date``,
  ``customer_id``, ``payment_type``, ``amount``. There is **no** invoice-link
  column in the current schema, so ``sales_invoice_number`` is always NULL for a
  voucher row (the service then renders the general "سند قبض" description). The
  schema is intentionally NOT modified by this report.
* ``customers``:      ``customer_id``, ``customer_name`` (joined once for the
  display name; the name is never stored/duplicated in the statement).
* ``companies``:      ``id``, ``name_ar`` (joined once for each movement's owning
  company — the seller company for an invoice / its settlement, the voucher's
  company for a receipt). Falls back to the invoice's
  ``seller_name_ar_snapshot`` when the company row is missing.
  Also ``name_en`` / ``commercial_registration`` / ``vat_number`` / ``address_ar``
  / ``address_en`` / ``logo`` / ``logo_mime`` — read for the **printed
  letterhead** of the selected company (``fetch_company_letterhead``).
* ``company_settings``: ``company_name_ar`` / ``company_name_en`` / ``tax_number``
  (optional; the *screen* header only). Empty on this installation and written by
  no screen — see ``fetch_company_info``.

Filtering: ``customer_id`` and ``company_id`` are both **optional**. With neither
set the statement returns every movement (all customers / all companies) in the
date range — the report can show the full view on search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.database.db import Database


@dataclass(frozen=True)
class CustomerStatementFilters:
    """Normalised query input. ``customer_id`` / ``company_id`` are both optional
    (None = no restriction on that dimension); dates are optional too (a blank
    bound means open-ended on that side)."""

    customer_id: int | None = None
    company_id: int | None = None
    date_from: str | None = None
    date_to: str | None = None


class CustomerStatementReportRepository:
    """Read-only SQL for the combined customer statement + its lookups."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- combined statement -------------------------------------------------
    def fetch_statement(self, filters: CustomerStatementFilters) -> list[dict[str, Any]]:
        """Return every invoice + voucher movement for one customer, ordered.

        The ordering is done in SQL on the real ``transaction_date`` (a DATE, not
        a formatted string), then each cash invoice is grouped with its own
        settlement (by shared ``source_id``, invoice ``source_sequence`` 0 before
        settlement 1) so the settlement prints directly under its invoice rather
        than after every invoice; real receipt vouchers follow the invoice groups
        of the same date. Fully deterministic — identical for preview, printing
        and PDF/Excel export.
        """
        def _conditions(
            customer_col: str, company_col: str, date_col: str, *, timestamp: bool
        ) -> tuple[str, list[Any]]:
            conditions: list[str] = []
            params: list[Any] = []
            if filters.customer_id is not None:
                conditions.append(f"{customer_col} = %s")
                params.append(int(filters.customer_id))
            if filters.company_id is not None:
                conditions.append(f"{company_col} = %s")
                params.append(int(filters.company_id))
            if filters.date_from:
                conditions.append(f"{date_col} >= %s::date")
                params.append(filters.date_from)
            if filters.date_to:
                if timestamp:
                    conditions.append(f"{date_col} < (%s::date + INTERVAL '1 day')")
                else:
                    conditions.append(f"{date_col} <= %s::date")
                params.append(filters.date_to)
            return (" AND ".join(conditions) if conditions else "TRUE"), params

        # Invoice arm + its cash-settlement arm share the same WHERE (both read
        # sales_invoices). The voucher arm has its own columns.
        invoice_where, invoice_params = _conditions(
            "si.customer_id", "si.seller_company_id", "si.issue_datetime", timestamp=True
        )
        voucher_where, voucher_params = _conditions(
            "rv.customer_id", "rv.company_id", "rv.voucher_date", timestamp=False
        )

        sql = f"""
            WITH invoice_rows AS (
                SELECT
                    'sales_invoice'::text        AS source_type,
                    si.id                         AS source_id,
                    0                             AS source_sequence,
                    si.issue_datetime::date       AS transaction_date,
                    si.customer_id                AS customer_id,
                    si.seller_company_id          AS company_id,
                    si.seller_name_ar_snapshot::text AS company_name_snapshot,
                    si.invoice_number::text       AS sales_invoice_number,
                    si.total_including_vat::numeric AS debit,
                    0::numeric                    AS credit,
                    si.payment_type::text         AS payment_type,
                    si.document_status::text      AS invoice_status,
                    NULL::text                    AS voucher_number
                FROM sales_invoices si
                WHERE {invoice_where}
            ),
            cash_payment_rows AS (
                -- Derived settlement for CASH invoices only (paid immediately).
                -- Display-only: no receipt voucher is created in the database.
                SELECT
                    'cash_invoice_payment'::text  AS source_type,
                    si.id                         AS source_id,
                    1                             AS source_sequence,
                    si.issue_datetime::date       AS transaction_date,
                    si.customer_id                AS customer_id,
                    si.seller_company_id          AS company_id,
                    si.seller_name_ar_snapshot::text AS company_name_snapshot,
                    si.invoice_number::text       AS sales_invoice_number,
                    0::numeric                    AS debit,
                    si.total_including_vat::numeric AS credit,
                    si.payment_type::text         AS payment_type,
                    si.document_status::text      AS invoice_status,
                    NULL::text                    AS voucher_number
                FROM sales_invoices si
                WHERE {invoice_where} AND lower(si.payment_type) = 'cash'
            ),
            voucher_rows AS (
                SELECT
                    'receipt_voucher'::text       AS source_type,
                    rv.id                         AS source_id,
                    2                             AS source_sequence,
                    rv.voucher_date               AS transaction_date,
                    rv.customer_id                AS customer_id,
                    rv.company_id                 AS company_id,
                    NULL::text                    AS company_name_snapshot,
                    -- No invoice-link column exists in the current schema, so a
                    -- voucher is never treated as invoice-linked (the linked
                    -- description is only used when an explicit relationship
                    -- exists). Do NOT guess a link from amount/date/description.
                    NULL::text                    AS sales_invoice_number,
                    0::numeric                    AS debit,
                    rv.amount::numeric            AS credit,
                    rv.payment_type::text         AS payment_type,
                    NULL::text                    AS invoice_status,
                    rv.voucher_number::text       AS voucher_number
                FROM receipt_vouchers rv
                WHERE {voucher_where}
            ),
            combined AS (
                SELECT * FROM invoice_rows
                UNION ALL
                SELECT * FROM cash_payment_rows
                UNION ALL
                SELECT * FROM voucher_rows
            )
            SELECT
                combined.source_type,
                combined.source_id,
                combined.source_sequence,
                combined.transaction_date,
                combined.customer_id,
                c.customer_name                   AS customer_name,
                combined.company_id,
                COALESCE(co.name_ar, combined.company_name_snapshot) AS company_name,
                combined.sales_invoice_number,
                combined.debit,
                combined.credit,
                combined.payment_type,
                combined.invoice_status,
                combined.voucher_number
            FROM combined
            LEFT JOIN customers c ON c.customer_id = combined.customer_id
            LEFT JOIN companies co ON co.id = combined.company_id
            -- Order so each cash invoice is IMMEDIATELY followed by its own
            -- settlement, rather than listing every invoice first and every
            -- settlement afterwards. The invoice debit row and its derived
            -- cash-settlement row share the same source_id (both si.id), so
            -- grouping by source_id before source_sequence pairs them: the
            -- invoice (seq 0) then its settlement (seq 1). Real receipt vouchers
            -- live in a different id space, so they are pushed after all invoice
            -- groups on the same date via the source_type flag (FALSE < TRUE),
            -- preserving their previous "after the invoices" position.
            ORDER BY combined.transaction_date ASC,
                     (combined.source_type = 'receipt_voucher') ASC,
                     combined.source_id ASC,
                     combined.source_sequence ASC
        """
        # Param order matches the three arms: invoices, cash-settlement (reuses
        # the same invoice WHERE), then vouchers.
        return self.db.fetch_all(sql, invoice_params + invoice_params + voucher_params)

    # --- lookups (read-only) -----------------------------------------------
    def fetch_customer_options(self, limit: int = 500) -> list[dict[str, Any]]:
        """Searchable customer selector source (id + display label).

        Capped like the other reports' selectors so the UI never loads an
        unbounded customer set; the combo's contains-completer filters this
        client-side, and the stored/filter value is always the customer id.
        """
        rows = self.db.fetch_all(
            "SELECT customer_id AS id, customer_name, phone_number "
            "FROM customers ORDER BY customer_id ASC LIMIT %s",
            [int(limit)],
        )
        options: list[dict[str, Any]] = []
        for row in rows:
            name = (row.get("customer_name") or "").strip()
            phone = (row.get("phone_number") or "").strip()
            label = " - ".join(part for part in (name, phone) if part) or str(row.get("id") or "")
            options.append({"id": row.get("id"), "label": label})
        return options

    def fetch_company_options(self, limit: int = 500) -> list[dict[str, Any]]:
        """Company selector source (id + Arabic name), capped like the others."""
        rows = self.db.fetch_all(
            "SELECT id, name_ar FROM companies ORDER BY name_ar NULLS LAST, id LIMIT %s",
            [int(limit)],
        )
        return [
            {"id": row.get("id"), "label": (row.get("name_ar") or "").strip() or str(row.get("id") or "")}
            for row in rows
        ]

    def fetch_customer_name(self, customer_id: int) -> str | None:
        row = self.db.fetch_one(
            "SELECT customer_name FROM customers WHERE customer_id = %s",
            [int(customer_id)],
        )
        if not row:
            return None
        return (row.get("customer_name") or "").strip() or None

    def fetch_company_info(self) -> dict[str, str]:
        """Company name / tax number for the *on-screen* report header.

        Reads the legacy Access-migrated ``company_settings``. That table is empty
        on this installation and no screen writes to it, so this returns ``{}`` in
        practice — which is why the printed statement showed no company at all.
        The print letterhead therefore reads :meth:`fetch_company_letterhead`
        instead; this stays for the screen header and the Excel/print filter lines,
        both of which degrade to nothing gracefully.
        """
        try:
            row = self.db.fetch_one(
                "SELECT company_name_ar, company_name_en, tax_number "
                "FROM company_settings LIMIT 1"
            )
        except Exception:  # noqa: BLE001 - optional table; never break the report
            return {}
        if not row:
            return {}
        name = (row.get("company_name_ar") or row.get("company_name_en") or "").strip()
        info: dict[str, str] = {}
        if name:
            info["name"] = name
        tax = (row.get("tax_number") or "").strip()
        if tax:
            info["tax_number"] = tax
        return info

    def fetch_company_letterhead(self, company_id: int) -> dict[str, Any] | None:
        """The selected company's full identity, for the printed letterhead.

        ``companies`` — not ``company_settings`` — is where the live data is: both
        names, the VAT and CR numbers, the addresses and the logo blob. It is also
        the table the statement's own company filter selects from, so the letterhead
        is the company whose movements are being listed, which is the only one that
        can honestly head the sheet.
        """
        try:
            return self.db.fetch_one(
                "SELECT id, name_ar, name_en, commercial_registration, vat_number, "
                "address_ar, address_en, logo, logo_mime "
                "FROM companies WHERE id = %s",
                [int(company_id)],
            )
        except Exception:  # noqa: BLE001 - the letterhead must never break a print
            return None
