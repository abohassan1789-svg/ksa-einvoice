"""Unit tests for the Status Analysis Report service (no database needed).

A fake repository feeds canned status counts so the percentage maths, summary
totals and the LEFT-JOIN zero-count behaviour can be asserted in isolation.
"""

from __future__ import annotations

from app.services.status_analysis_report_service import (
    StatusAnalysisReportService,
    StatusAnalysisRequest,
)


class _FakeRepository:
    """Stand-in for StatusAnalysisReportRepository with fixed data."""

    def __init__(self, status_rows, total):
        self._status_rows = status_rows
        self._total = total
        self.last_filters = None

    def fetch_total_count(self, filters):
        self.last_filters = filters
        return self._total

    def fetch_status_rows(self, filters):
        self.last_filters = filters
        return [dict(row) for row in self._status_rows]

    def fetch_company_name(self):
        return "شركة الاختبار"


def _service():
    rows = [
        {"status_id": 1, "status_name": "مشغول - الاتصال لاحقا", "count": 2460},
        {"status_id": 2, "status_name": "لا يرغب بالبيع", "count": 1514},
        {"status_id": 3, "status_name": "معلق", "count": 333},
        {"status_id": 4, "status_name": "محول لقسم الأراضي", "count": 0},
    ]
    return StatusAnalysisReportService(_FakeRepository(rows, total=4678))


def test_percentage_formula_one_decimal():
    result = _service().fetch_report(StatusAnalysisRequest())
    by_name = {row["status_name"]: row for row in result.rows}
    assert by_name["معلق"]["percentage"] == 7.1          # 333 / 4678 * 100
    assert by_name["معلق"]["percentage_label"] == "7.1%"
    assert by_name["مشغول - الاتصال لاحقا"]["percentage"] == 52.6


def test_total_calls_repeated_on_every_row():
    result = _service().fetch_report(StatusAnalysisRequest())
    assert result.total_calls == 4678
    assert all(row["total_calls"] == 4678 for row in result.rows)


def test_zero_count_status_appears():
    result = _service().fetch_report(StatusAnalysisRequest())
    zero = [row for row in result.rows if row["status_name"] == "محول لقسم الأراضي"][0]
    assert zero["count"] == 0
    assert zero["percentage"] == 0.0
    assert result.insights["zero_statuses"][0]["status_name"] == "محول لقسم الأراضي"


def test_summary_totals_and_extremes():
    summary = _service().fetch_report(StatusAnalysisRequest()).summary
    assert summary["total_count"] == 2460 + 1514 + 333 + 0
    assert summary["total_calls"] == 4678
    assert summary["highest"]["status_name"] == "مشغول - الاتصال لاحقا"
    # lowest EXCLUDING zero-count statuses
    assert summary["lowest_non_zero"]["status_name"] == "معلق"


def test_overall_percentage_handles_unknown_status_rows():
    # total (4678) > sum of known-status counts (4307) -> overall < 100%
    summary = _service().fetch_report(StatusAnalysisRequest()).summary
    assert summary["overall_percentage"] < 100.0


def test_division_by_zero_safe():
    svc = StatusAnalysisReportService(
        _FakeRepository([{"status_id": 1, "status_name": "أ", "count": 0}], total=0)
    )
    result = svc.fetch_report(StatusAnalysisRequest())
    assert result.total_calls == 0
    assert result.rows[0]["percentage"] == 0.0
    assert result.summary["overall_percentage"] == 0.0


def test_insight_text_mentions_top_status():
    texts = _service().fetch_report(StatusAnalysisRequest()).insights["texts"]
    assert any("أعلى حالة" in text and "مشغول" in text for text in texts)
    assert any("بدون أي متابعات" in text for text in texts)


def test_request_filters_passed_to_repository():
    svc = _service()
    svc.fetch_report(
        StatusAnalysisRequest(
            date_from="2025-01-01",
            date_to="2025-01-31",
            status_ids=(1, 2),
            employee_ids=(7,),
        )
    )
    filters = svc.repository.last_filters
    assert filters.date_from == "2025-01-01"
    assert filters.status_ids == (1, 2)
    assert filters.employee_ids == (7,)


def test_export_rows_are_formatted_strings():
    result = _service().fetch_report(StatusAnalysisRequest())
    first = result.export_rows[0]
    assert first["count"] == "2,460"
    assert first["percentage"] == "52.6%"
    assert first["total_calls"] == "4,678"
