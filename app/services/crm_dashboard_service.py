"""Read-only data service for the standalone sales dashboard screen (داشبورد).

The customer / company / date-range filters affect the money KPIs (cash sales,
credit sales, receipt vouchers) and both sales charts. The two catalog counts
(customers, products) are always full totals and deliberately ignore the
filters. Every method is read-only — it only ``SELECT``s.
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


class CrmDashboardService:
    """Aggregate KPI values, filter options, and sales chart data."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- KPI cards ----------------------------------------------------------
    def get_dashboard_counts(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Five KPI values. ``customers``/``products`` are full catalog totals
        (filters ignored); the three money values honour the filters."""
        return {
            "customers": self._count_table("customers"),
            "products": self._count_table("products"),
            "cash_sales": self._sum_sales(PAYMENT_CASH, filters),
            "credit_sales": self._sum_sales(PAYMENT_CREDIT, filters),
            "receipt_vouchers": self._sum_vouchers(filters),
        }

    def _count_table(self, table: str) -> int:
        try:
            row = self.db.fetch_one(f"SELECT COUNT(*) AS c FROM {table}")
            return int(row["c"]) if row and row.get("c") is not None else 0
        except Exception:  # noqa: BLE001 - dashboard must not crash the shell
            logger.exception("Failed to count rows in table %s", table)
            return 0

    def _sum_sales(self, payment_type: str, filters: dict[str, Any] | None) -> float:
        conditions, params = self._invoice_conditions(filters)
        conditions = ["lower(si.payment_type) = %s", *conditions]
        params = [payment_type, *params]
        sql = (
            "SELECT COALESCE(SUM(si.total_including_vat), 0) AS s "
            f"FROM sales_invoices si {self._where(conditions)}"
        )
        return self._sum_scalar(sql, params)

    def _sum_vouchers(self, filters: dict[str, Any] | None) -> float:
        conditions, params = self._voucher_conditions(filters)
        sql = (
            "SELECT COALESCE(SUM(rv.amount), 0) AS s "
            f"FROM receipt_vouchers rv {self._where(conditions)}"
        )
        return self._sum_scalar(sql, params)

    def _sum_scalar(self, sql: str, params: list[Any] | None = None) -> float:
        try:
            row = self.db.fetch_one(sql, params or [])
            return float(row["s"]) if row and row.get("s") is not None else 0.0
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load dashboard money total")
            return 0.0

    # --- filter options -----------------------------------------------------
    def get_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "customers": self._fetch_options(
                "SELECT customer_id AS id, customer_name AS label "
                "FROM customers ORDER BY customer_name NULLS LAST, customer_id"
            ),
            "companies": self._fetch_options(
                "SELECT id, name_ar AS label "
                "FROM companies ORDER BY name_ar NULLS LAST, id"
            ),
        }

    def _fetch_options(self, sql: str) -> list[dict[str, Any]]:
        try:
            rows = self.db.fetch_all(sql)
            return [
                {"id": row.get("id"), "label": row.get("label") or UNSPECIFIED_LABEL}
                for row in rows
            ]
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load dashboard filter options")
            return []

    # --- sales charts -------------------------------------------------------
    def get_sales_by_company(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        conditions, params = self._invoice_conditions(filters)
        sql = (
            "SELECT COALESCE(co.name_ar, si.seller_name_ar_snapshot, %s) AS company, "
            "       COALESCE(SUM(si.total_including_vat), 0) AS total "
            "FROM sales_invoices si "
            "LEFT JOIN companies co ON co.id = si.seller_company_id "
            f"{self._where(conditions)} "
            "GROUP BY 1 ORDER BY total DESC, company"
        )
        return self._money_series(sql, [UNSPECIFIED_LABEL, *params], "company")

    def get_sales_by_customer(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        conditions, params = self._invoice_conditions(filters)
        sql = (
            "SELECT COALESCE(c.customer_name, %s) AS customer, "
            "       COALESCE(SUM(si.total_including_vat), 0) AS total "
            "FROM sales_invoices si "
            "LEFT JOIN customers c ON c.customer_id = si.customer_id "
            f"{self._where(conditions)} "
            "GROUP BY 1 ORDER BY total DESC, customer"
        )
        return self._money_series(sql, [UNSPECIFIED_LABEL, *params], "customer")

    def _money_series(
        self, sql: str, params: list[Any], label_key: str
    ) -> list[dict[str, Any]]:
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

    # --- filter helpers -----------------------------------------------------
    def _invoice_conditions(self, filters: dict[str, Any] | None) -> tuple[list[str], list[Any]]:
        filters = filters or {}
        conditions: list[str] = []
        params: list[Any] = []
        if (customer_id := self._clean_int(filters.get("customer"))) is not None:
            conditions.append("si.customer_id = %s")
            params.append(customer_id)
        if (company_id := self._clean_int(filters.get("company"))) is not None:
            conditions.append("si.seller_company_id = %s")
            params.append(company_id)
        if date_from := self._clean(filters.get("date_from")):
            conditions.append("si.issue_datetime >= %s::date")
            params.append(date_from)
        if date_to := self._clean(filters.get("date_to")):
            conditions.append("si.issue_datetime < (%s::date + INTERVAL '1 day')")
            params.append(date_to)
        return conditions, params

    def _voucher_conditions(self, filters: dict[str, Any] | None) -> tuple[list[str], list[Any]]:
        filters = filters or {}
        conditions: list[str] = []
        params: list[Any] = []
        if (customer_id := self._clean_int(filters.get("customer"))) is not None:
            conditions.append("rv.customer_id = %s")
            params.append(customer_id)
        if (company_id := self._clean_int(filters.get("company"))) is not None:
            conditions.append("rv.company_id = %s")
            params.append(company_id)
        if date_from := self._clean(filters.get("date_from")):
            conditions.append("rv.voucher_date >= %s::date")
            params.append(date_from)
        if date_to := self._clean(filters.get("date_to")):
            conditions.append("rv.voucher_date <= %s::date")
            params.append(date_to)
        return conditions, params

    @staticmethod
    def _where(conditions: list[str]) -> str:
        return ("WHERE " + " AND ".join(conditions)) if conditions else ""

    @staticmethod
    def _clean(value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text in {"0", "None", "الكل"}:
            return None
        return value

    @staticmethod
    def _clean_int(value: Any) -> int | None:
        cleaned = CrmDashboardService._clean(value)
        if cleaned is None:
            return None
        try:
            return int(cleaned)
        except (TypeError, ValueError):
            return None
