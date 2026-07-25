import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMainWindow

from app.services.dashboard_service import DashboardService
from app.services.crm_dashboard_service import CrmDashboardService
from app.ui.main_window import ReviewMainWindow
from app.ui.screens.crm_dashboard_page import CrmDashboardPage
from app.ui.screens.dashboard_page import DashboardBarChart


def _app():
    return QApplication.instance() or QApplication([])


def test_dashboard_bar_chart_keeps_filter_labels_for_other_bucket():
    _app()
    chart = DashboardBarChart("test", "status", max_bars=3)

    chart.set_data(
        None,
        [
            {"status": "alpha", "count": 10},
            {"status": "beta", "count": 8},
            {"status": "gamma", "count": 6},
            {"status": "delta", "count": 4},
        ],
        "status",
    )

    other = "\u0623\u062e\u0631\u0649"
    other_row = next(row for row in chart._rows if row["label"] == other)

    assert other_row["count"] == 10
    assert other_row["source_labels"] == ["gamma", "delta"]


class _SalesFakeDb:
    """Fake DB that separates COUNT(*) card queries from SUM() money queries."""

    def __init__(self):
        self.count_sql = []
        self.sum_sql = []
        self.series_sql = []

    def fetch_one(self, sql, params=None):
        if "COUNT(*)" in sql:
            self.count_sql.append(sql)
            return {"c": 3}
        self.sum_sql.append((sql, params))
        return {"s": 100}

    def fetch_all(self, sql, params=None):
        self.series_sql.append((sql, params))
        if "seller_company_id" in sql:
            return [{"company": "شركة أ", "total": 250}]
        return [{"customer": "عميل أ", "total": 250}]


def test_home_dashboard_counts_use_phase2_sales_sources():
    db = _SalesFakeDb()
    service = DashboardService(db)

    counts = service.get_dashboard_counts()

    assert counts["customers"] == 3
    assert counts["products"] == 3
    assert counts["cash_sales"] == 100.0
    assert counts["credit_sales"] == 100.0
    assert counts["receipt_vouchers"] == 100.0
    assert any("FROM customers" in sql for sql in db.count_sql)
    assert any("FROM products" in sql for sql in db.count_sql)
    assert any(
        "FROM sales_invoices" in sql and params == ["cash"] for sql, params in db.sum_sql
    )
    assert any(
        "FROM sales_invoices" in sql and params == ["credit"] for sql, params in db.sum_sql
    )
    assert any("FROM receipt_vouchers" in sql for sql, _params in db.sum_sql)


def test_home_dashboard_sales_series_group_invoice_totals():
    db = _SalesFakeDb()
    service = DashboardService(db)

    company_rows = service.get_sales_by_company()
    customer_rows = service.get_sales_by_customer()

    assert company_rows == [{"company": "شركة أ", "count": 250.0}]
    assert customer_rows == [{"customer": "عميل أ", "count": 250.0}]
    assert all("FROM sales_invoices" in sql for sql, _params in db.series_sql)
    assert any("seller_company_id" in sql for sql, _params in db.series_sql)
    assert any("si.customer_id" in sql for sql, _params in db.series_sql)


def test_crm_dashboard_counts_keep_catalog_totals_and_filter_money():
    db = _SalesFakeDb()
    service = CrmDashboardService(db)

    counts = service.get_dashboard_counts()

    assert counts["customers"] == 3
    assert counts["products"] == 3
    assert counts["cash_sales"] == 100.0
    assert counts["credit_sales"] == 100.0
    assert counts["receipt_vouchers"] == 100.0
    # customers/products are plain catalog counts (no WHERE / no filter params).
    assert any("FROM customers" in sql for sql in db.count_sql)
    assert any("FROM products" in sql for sql in db.count_sql)


def test_crm_dashboard_money_applies_customer_company_and_date_filters():
    db = _SalesFakeDb()
    service = CrmDashboardService(db)

    service.get_dashboard_counts(
        {"customer": 5, "company": 2, "date_from": "2026-01-01", "date_to": "2026-01-31"}
    )

    cash_sql, cash_params = next(
        (sql, params)
        for sql, params in db.sum_sql
        if "sales_invoices" in sql and params and params[0] == "cash"
    )
    assert "si.customer_id = %s" in cash_sql
    assert "si.seller_company_id = %s" in cash_sql
    assert "si.issue_datetime >= %s::date" in cash_sql
    assert cash_params == ["cash", 5, 2, "2026-01-01", "2026-01-31"]

    voucher_sql, voucher_params = next(
        (sql, params) for sql, params in db.sum_sql if "receipt_vouchers" in sql
    )
    assert "rv.customer_id = %s" in voucher_sql
    assert "rv.company_id = %s" in voucher_sql
    assert "rv.voucher_date >= %s::date" in voucher_sql
    assert voucher_params == [5, 2, "2026-01-01", "2026-01-31"]


def test_crm_dashboard_filter_options_load_customers_and_companies():
    db = _SalesFakeDb()
    service = CrmDashboardService(db)

    options = service.get_filter_options()

    assert set(options.keys()) == {"customers", "companies"}
    option_sql = [sql for sql, _params in db.series_sql]
    assert any("FROM customers" in sql for sql in option_sql)
    assert any("FROM companies" in sql for sql in option_sql)


def test_crm_dashboard_sales_series_apply_filters():
    db = _SalesFakeDb()
    service = CrmDashboardService(db)

    service.get_sales_by_company({"customer": 5})
    company_sql, company_params = db.series_sql[-1]
    assert "si.customer_id = %s" in company_sql
    assert company_params[-1] == 5

    service.get_sales_by_customer({"company": 2})
    customer_sql, customer_params = db.series_sql[-1]
    assert "si.seller_company_id = %s" in customer_sql
    assert customer_params[-1] == 2


class _FakeDashboardService:
    def __init__(self):
        self.count_calls = 0

    def get_filter_options(self):
        return {"customers": [], "companies": []}

    def get_dashboard_counts(self, filters=None):
        self.count_calls += 1
        return {
            "customers": 0,
            "products": 0,
            "cash_sales": 0.0,
            "credit_sales": 0.0,
            "receipt_vouchers": 0.0,
        }

    def get_sales_by_company(self, filters=None):
        return []

    def get_sales_by_customer(self, filters=None):
        return []


def test_crm_dashboard_customer_chart_caps_to_top_bars():
    _app()
    page = CrmDashboardPage(_FakeDashboardService())

    assert page._company_chart._max_bars is None
    assert page._customer_chart._max_bars == 12


def test_activating_standalone_dashboard_refreshes_its_cards():
    _app()
    service = _FakeDashboardService()
    page = CrmDashboardPage(service)
    initial_calls = service.count_calls

    page.event(QEvent(QEvent.WindowActivate))

    assert service.count_calls == initial_calls + 1


class _ExistingDashboardWindow:
    def __init__(self):
        self.refresh_calls = 0

    def refresh_dashboard(self):
        self.refresh_calls += 1

    def showMaximized(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass


def test_reopening_existing_dashboard_refreshes_its_cards():
    window = _ExistingDashboardWindow()

    class _Shell:
        open_windows = {"crm_dashboard": window}

        @staticmethod
        def _can_view(_key):
            return True

        @staticmethod
        def _build_window(_key):
            raise AssertionError("existing dashboard should be reused")

        @staticmethod
        def _highlight(_key):
            pass

    ReviewMainWindow.open_screen(_Shell(), "crm_dashboard")

    assert window.refresh_calls == 1


def test_activating_main_window_refreshes_embedded_dashboard():
    _app()
    dashboard = _ExistingDashboardWindow()

    class _DashboardShell(ReviewMainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self.dashboard_page = dashboard

    shell = _DashboardShell()
    shell.event(QEvent(QEvent.WindowActivate))

    assert dashboard.refresh_calls == 1
