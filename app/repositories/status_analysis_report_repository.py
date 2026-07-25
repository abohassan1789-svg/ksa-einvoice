"""Database layer for the Status Analysis Report (تقرير تحليل الحالات).

This is the ONLY place SQL for this report lives. It returns plain row dicts;
all percentage maths, summaries and insights are computed in the service layer
(``app/services/status_analysis_report_service.py``), and the UI never sees SQL.

Design notes
------------
* The per-status count uses a LEFT JOIN from ``case_statuses`` so that **every**
  status appears, even ones with zero follow-ups in the selected period
  (requirement 1/2). The date/employee/customer/area filters are applied to a
  pre-filtered ``filtered`` CTE of follow-ups so the LEFT JOIN still preserves
  zero-count statuses (putting those conditions in the outer WHERE would drop
  them).
* "إجمالي الاتصالات" (total calls) is an **independent** COUNT of all follow-ups
  after the same date/employee/customer/area filters but WITHOUT the status
  filter. This makes ``count / total * 100`` correct and keeps the total a
  constant repeated on every row. With no status filter the per-status counts
  sum to (almost) the total, so the overall percentage is ~100% (it is slightly
  under when some follow-ups carry an unknown/NULL ``case_status_id`` — exactly
  the 99.8% case in the reference report).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.database.db import Database


@dataclass(frozen=True)
class StatusAnalysisFilters:
    """Normalised query input for the report. Empty tuples mean 'no filter'."""

    date_from: str | None = None
    date_to: str | None = None
    status_ids: tuple[int, ...] = field(default_factory=tuple)
    employee_ids: tuple[int, ...] = field(default_factory=tuple)
    area_values: tuple[str, ...] = field(default_factory=tuple)
    customer_ids: tuple[int, ...] = field(default_factory=tuple)


class StatusAnalysisReportRepository:
    """Read-only SQL for status counts, total calls and filter options."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- follow-up population (the denominator) ----------------------------
    def _followup_clause(self, filters: StatusAnalysisFilters) -> tuple[str, str, list[Any]]:
        """Build the JOIN + WHERE that define the filtered follow-up rows.

        Returns ``(join_sql, where_sql, params)``. The status filter is NOT part
        of this clause — it only restricts which status *rows* are shown, never
        the total-calls denominator.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if filters.date_from:
            conditions.append("d.follow_up_date >= %s")
            params.append(filters.date_from)
        if filters.date_to:
            conditions.append("d.follow_up_date < (%s::date + INTERVAL '1 day')")
            params.append(filters.date_to)
        if filters.employee_ids:
            conditions.append("d.employee_id = ANY(%s)")
            params.append(list(filters.employee_ids))
        if filters.customer_ids:
            conditions.append("d.customer_id = ANY(%s)")
            params.append(list(filters.customer_ids))
        if filters.area_values:
            conditions.append("c.place_area_feddan::text = ANY(%s)")
            params.append([str(value) for value in filters.area_values])

        # The customers join is only needed when an area filter is active.
        join_sql = (
            " LEFT JOIN customers c ON c.customer_id = d.customer_id"
            if filters.area_values
            else ""
        )
        where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        return join_sql, where_sql, params

    def fetch_total_count(self, filters: StatusAnalysisFilters) -> int:
        """COUNT of all follow-ups after filters (excluding the status filter)."""
        join_sql, where_sql, params = self._followup_clause(filters)
        sql = f"SELECT COUNT(*) AS total FROM daily_followups d{join_sql}{where_sql}"
        row = self.db.fetch_one(sql, params)
        return int(row["total"]) if row and row.get("total") is not None else 0

    def fetch_status_rows(self, filters: StatusAnalysisFilters) -> list[dict[str, Any]]:
        """One row per status: ``status_id, status_name, count`` (count may be 0)."""
        join_sql, where_sql, params = self._followup_clause(filters)
        query_params = list(params)

        status_filter_sql = ""
        if filters.status_ids:
            status_filter_sql = " WHERE s.case_status_id = ANY(%s)"

        sql = (
            "WITH filtered AS ("
            f"  SELECT d.daily_followup_id, d.case_status_id"
            f"  FROM daily_followups d{join_sql}{where_sql}"
            ") "
            "SELECT s.case_status_id AS status_id, "
            "       s.case_status_name AS status_name, "
            "       COUNT(filtered.daily_followup_id) AS count "
            "FROM case_statuses s "
            "LEFT JOIN filtered ON filtered.case_status_id = s.case_status_id"
            f"{status_filter_sql} "
            "GROUP BY s.case_status_id, s.case_status_name "
            "ORDER BY count DESC, s.case_status_name ASC"
        )
        if filters.status_ids:
            query_params.append(list(filters.status_ids))

        rows = self.db.fetch_all(sql, query_params)
        return [
            {
                "status_id": row.get("status_id"),
                "status_name": (row.get("status_name") or "").strip(),
                "count": int(row.get("count") or 0),
            }
            for row in rows
        ]

    # --- filter dropdown options -------------------------------------------
    def fetch_status_options(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT case_status_id AS id, case_status_name AS label "
            "FROM case_statuses ORDER BY case_status_name NULLS LAST, case_status_id"
        )
        return [{"id": row.get("id"), "label": (row.get("label") or "").strip()} for row in rows]

    def fetch_employee_options(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT employee_id AS id, employee_name AS label "
            "FROM employees ORDER BY employee_name NULLS LAST, employee_id"
        )
        return [{"id": row.get("id"), "label": (row.get("label") or "").strip()} for row in rows]

    def fetch_area_options(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT place_number AS id, place_number AS label "
            "FROM places WHERE place_number IS NOT NULL "
            "ORDER BY place_number"
        )
        return [{"id": row.get("id"), "label": (row.get("label") or "").strip()} for row in rows]

    def fetch_customer_options(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT customer_id AS id, customer_name, phone_number "
            "FROM customers ORDER BY customer_id ASC LIMIT %s",
            [limit],
        )
        options: list[dict[str, Any]] = []
        for row in rows:
            name = (row.get("customer_name") or "").strip()
            phone = (row.get("phone_number") or "").strip()
            label = " - ".join(part for part in (name, phone) if part) or str(row.get("id") or "")
            options.append({"id": row.get("id"), "label": label})
        return options

    def fetch_company_name(self) -> str | None:
        """Company name for export/print headers, if company settings exist."""
        try:
            row = self.db.fetch_one(
                "SELECT company_name_ar, company_name_en FROM company_settings LIMIT 1"
            )
        except Exception:  # noqa: BLE001 - optional table; never break the report
            return None
        if not row:
            return None
        return (row.get("company_name_ar") or row.get("company_name_en") or "").strip() or None
