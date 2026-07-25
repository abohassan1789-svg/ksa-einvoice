from app.ui.main_window import REPORT_SCREEN_BY_KEY, REPORTS
from app.ui.screens.customer_statement_report_screen import CustomerStatementReportScreen


def test_only_customer_statement_report_is_registered_for_navigation():
    # Per product decision, the Follow-up Smart Report and the Status Analysis
    # report are hidden from the Reports menu; only the customer statement
    # (كشف حساب العميل) remains navigable.
    keys = [key for key, _label, _cls in REPORTS]

    assert keys == ["customer_statement"]
    assert REPORT_SCREEN_BY_KEY["customer_statement"] is CustomerStatementReportScreen
    assert "daily_followup_smart_report" not in keys
    assert "status_analysis_report" not in keys
