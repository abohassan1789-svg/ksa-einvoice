"""Business logic for the Status Analysis Report (تقرير تحليل الحالات).

The UI calls only this service; this service calls only the repository. No SQL
and no Qt widgets live here — just the percentage maths, summary totals and the
automatic Arabic insights.

Calculation logic
-----------------
* ``العدد``           = follow-up records linked to each status (LEFT JOIN, so a
                        status with no records shows 0).
* ``إجمالي الاتصالات`` = total follow-up records after the date/employee/
                        customer/area filters (NOT the status filter). Repeated
                        on every row.
* ``النسبة``          = العدد / إجمالي الاتصالات * 100, rounded to 1 decimal
                        (0 when the total is 0, to avoid division by zero).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.repositories.status_analysis_report_repository import (
    StatusAnalysisFilters,
    StatusAnalysisReportRepository,
)


@dataclass(frozen=True)
class ReportColumn:
    """Lightweight column descriptor reused by the Excel/PDF/print exporters."""

    key: str
    label: str


@dataclass(frozen=True)
class StatusAnalysisRequest:
    date_from: str | None = None
    date_to: str | None = None
    status_ids: tuple[int, ...] = field(default_factory=tuple)
    employee_ids: tuple[int, ...] = field(default_factory=tuple)
    area_values: tuple[str, ...] = field(default_factory=tuple)
    customer_ids: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StatusAnalysisResult:
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]          # numeric rows for the on-screen table/charts
    export_rows: list[dict[str, Any]]   # formatted strings keyed by column.key
    summary: dict[str, Any]
    insights: dict[str, Any]
    total_calls: int


# Report column order (logical). The UI renders RTL so the first column shows
# on the right, matching the reference report (الحالة | العدد | إجمالي | النسبة).
REPORT_COLUMNS: tuple[ReportColumn, ...] = (
    ReportColumn("status_name", "الحالة"),
    ReportColumn("count", "العدد"),
    ReportColumn("total_calls", "إجمالي الاتصالات"),
    ReportColumn("percentage", "النسبة"),
)


class StatusAnalysisReportService:
    """Build the Status Analysis Report from filtered status counts."""

    def __init__(self, repository: StatusAnalysisReportRepository | None = None) -> None:
        self.repository = repository or StatusAnalysisReportRepository()
        self.columns = list(REPORT_COLUMNS)

    # --- public API ---------------------------------------------------------
    def fetch_report(self, request: StatusAnalysisRequest) -> StatusAnalysisResult:
        filters = self._build_filters(request)
        total_calls = self.repository.fetch_total_count(filters)
        status_rows = self.repository.fetch_status_rows(filters)

        rows: list[dict[str, Any]] = []
        for index, row in enumerate(status_rows, start=1):
            count = int(row["count"])
            percentage = self._percentage(count, total_calls)
            rows.append(
                {
                    "row_number": index,
                    "status_id": row["status_id"],
                    "status_name": row["status_name"],
                    "count": count,
                    "total_calls": total_calls,
                    "percentage": percentage,
                    "percentage_label": self._percentage_label(percentage),
                }
            )

        summary = self._build_summary(rows, total_calls)
        insights = self._build_insights(rows, total_calls)
        export_rows = [self._export_row(row) for row in rows]
        return StatusAnalysisResult(
            columns=self.columns,
            rows=rows,
            export_rows=export_rows,
            summary=summary,
            insights=insights,
            total_calls=total_calls,
        )

    def load_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "statuses": self._safe(self.repository.fetch_status_options),
            "employees": self._safe(self.repository.fetch_employee_options),
            "areas": self._safe(self.repository.fetch_area_options),
            "customers": self._safe(self.repository.fetch_customer_options),
        }

    def company_name(self) -> str | None:
        try:
            return self.repository.fetch_company_name()
        except Exception:  # noqa: BLE001
            return None

    # --- maths --------------------------------------------------------------
    @staticmethod
    def _percentage(count: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return round(count * 100.0 / total, 1)

    @staticmethod
    def _percentage_label(percentage: float) -> str:
        return f"{percentage:.1f}%"

    def _build_summary(self, rows: list[dict[str, Any]], total_calls: int) -> dict[str, Any]:
        total_count = sum(row["count"] for row in rows)
        overall_percentage = self._percentage(total_count, total_calls)
        non_zero = [row for row in rows if row["count"] > 0]
        highest = max(rows, key=lambda row: row["count"], default=None)
        lowest_non_zero = min(non_zero, key=lambda row: row["count"], default=None)
        return {
            "total_count": total_count,
            "total_calls": total_calls,
            "overall_percentage": overall_percentage,
            "overall_percentage_label": self._percentage_label(overall_percentage),
            "highest": highest,
            "lowest_non_zero": lowest_non_zero,
            "status_count": len(rows),
        }

    def _build_insights(self, rows: list[dict[str, Any]], total_calls: int) -> dict[str, Any]:
        non_zero = [row for row in rows if row["count"] > 0]
        top5 = sorted(rows, key=lambda row: row["count"], reverse=True)[:5]
        zero_statuses = [row for row in rows if row["count"] == 0]
        highest = top5[0] if top5 else None
        lowest_non_zero = min(non_zero, key=lambda row: row["count"], default=None)

        texts: list[str] = []
        if highest and highest["count"] > 0:
            texts.append(
                f"أعلى حالة هي: {highest['status_name']} "
                f"بنسبة {highest['percentage_label']} ({highest['count']:,} متابعة)"
            )
        if lowest_non_zero:
            texts.append(
                f"أقل حالة (باستثناء صفر) هي: {lowest_non_zero['status_name']} "
                f"بنسبة {lowest_non_zero['percentage_label']} ({lowest_non_zero['count']:,} متابعة)"
            )
        if zero_statuses:
            texts.append(
                f"يوجد {len(zero_statuses):,} حالة بدون أي متابعات خلال الفترة المحددة"
            )
        texts.append(f"إجمالي المتابعات خلال الفترة هو {total_calls:,}")

        return {
            "top5": top5,
            "zero_statuses": zero_statuses,
            "highest_percentage": highest,
            "lowest_percentage": lowest_non_zero,
            "texts": texts,
        }

    # --- helpers ------------------------------------------------------------
    def _export_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "status_name": row["status_name"],
            "count": f"{row['count']:,}",
            "total_calls": f"{row['total_calls']:,}",
            "percentage": row["percentage_label"],
        }

    @staticmethod
    def _build_filters(request: StatusAnalysisRequest) -> StatusAnalysisFilters:
        return StatusAnalysisFilters(
            date_from=request.date_from or None,
            date_to=request.date_to or None,
            status_ids=tuple(request.status_ids or ()),
            employee_ids=tuple(request.employee_ids or ()),
            area_values=tuple(str(value) for value in (request.area_values or ())),
            customer_ids=tuple(request.customer_ids or ()),
        )

    @staticmethod
    def _safe(loader) -> list[dict[str, Any]]:
        try:
            return list(loader())
        except Exception:  # noqa: BLE001 - filter options must never break the screen
            return []
