"""Read-only data service for the existing home dashboard.

The home dashboard is a Phase-2 sales overview: five KPI cards (customers,
products, cash sales, credit sales, receipt vouchers) and two sales charts
(total sales per company, total sales per customer). Every method is read-only —
it only ever ``SELECT``s and never mutates any table.
"""

from __future__ import annotations

import logging
from typing import Any

from app.database.db import Database

logger = logging.getLogger(__name__)

UNSPECIFIED_LABEL = "غير محدد"

# sales_invoices.payment_type values (mirrors app/models/sales_invoice.py).
PAYMENT_CASH = "cash"
PAYMENT_CREDIT = "credit"


class DashboardService:
    """Aggregate KPI counts and sales chart data for the home dashboard."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- KPI cards ----------------------------------------------------------
    def get_dashboard_counts(self) -> dict[str, Any]:
        """Return the five KPI values shown on the home dashboard cards.

        * ``customers`` / ``products``  -> row counts (integers).
        * ``cash_sales`` / ``credit_sales`` -> summed ``total_including_vat`` of
          the matching ``sales_invoices`` (money).
        * ``receipt_vouchers`` -> summed ``receipt_vouchers.amount`` (money).
        """
        return {
            "customers": self._count_table("customers"),
            "products": self._count_table("products"),
            "cash_sales": self._sum_sales(PAYMENT_CASH),
            "credit_sales": self._sum_sales(PAYMENT_CREDIT),
            "receipt_vouchers": self._sum_scalar(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM receipt_vouchers"
            ),
        }

    def _count_table(self, table: str) -> int:
        try:
            row = self.db.fetch_one(f"SELECT COUNT(*) AS c FROM {table}")
            return int(row["c"]) if row and row.get("c") is not None else 0
        except Exception:  # noqa: BLE001 - dashboard must not crash the shell
            logger.exception("Failed to count rows in table %s", table)
            return 0

    def _sum_sales(self, payment_type: str) -> float:
        return self._sum_scalar(
            "SELECT COALESCE(SUM(total_including_vat), 0) AS s "
            "FROM sales_invoices WHERE lower(payment_type) = %s",
            [payment_type],
        )

    def _sum_scalar(self, sql: str, params: list[Any] | None = None) -> float:
        try:
            row = self.db.fetch_one(sql, params or [])
            return float(row["s"]) if row and row.get("s") is not None else 0.0
        except Exception:  # noqa: BLE001 - dashboard must not crash the shell
            logger.exception("Failed to load dashboard money total")
            return 0.0

    # --- sales charts -------------------------------------------------------
    def get_sales_by_company(self) -> list[dict[str, Any]]:
        """Total invoiced sales (``total_including_vat``) per seller company."""
        sql = (
            "SELECT COALESCE(co.name_ar, si.seller_name_ar_snapshot, %s) AS company, "
            "       COALESCE(SUM(si.total_including_vat), 0) AS total "
            "FROM sales_invoices si "
            "LEFT JOIN companies co ON co.id = si.seller_company_id "
            "GROUP BY 1 "
            "ORDER BY total DESC, company"
        )
        return self._money_series(sql, [UNSPECIFIED_LABEL], "company")

    def get_sales_by_customer(self) -> list[dict[str, Any]]:
        """Total invoiced sales (``total_including_vat``) per customer."""
        sql = (
            "SELECT COALESCE(c.customer_name, %s) AS customer, "
            "       COALESCE(SUM(si.total_including_vat), 0) AS total "
            "FROM sales_invoices si "
            "LEFT JOIN customers c ON c.customer_id = si.customer_id "
            "GROUP BY 1 "
            "ORDER BY total DESC, customer"
        )
        return self._money_series(sql, [UNSPECIFIED_LABEL], "customer")

    def _money_series(
        self,
        sql: str,
        params: list[Any],
        label_key: str,
    ) -> list[dict[str, Any]]:
        """Run a ``(label, total)`` money aggregate into chart-ready rows.

        The chart consumes a numeric ``count`` value per row; for money charts
        that value is a float (kept precise for display / summing), while the
        label lives under ``label_key``.
        """
        try:
            rows = self.db.fetch_all(sql, params)
            return [
                {
                    label_key: row.get(label_key) or UNSPECIFIED_LABEL,
                    "count": float(row.get("total") or 0),
                }
                for row in rows
            ]
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load dashboard sales series for %s", label_key)
            return []
