import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCompleter

from app.services.daily_followup_report_service import (
    DailyFollowupReportRequest,
    DailyFollowupReportResult,
    DailyFollowupReportService,
)
from app.ui.screens.daily_followup_smart_report_screen import (
    DailyFollowupSmartReportScreen,
    _FilterRow,
)


def _app():
    return QApplication.instance() or QApplication([])


class _Settings:
    def __init__(self, initial=None):
        self.initial = initial or {}
        self.saved = None

    def load(self):
        return self.initial

    def save(self, settings):
        self.saved = settings


class _ReportService:
    def __init__(self):
        self.inner = DailyFollowupReportService()
        self.columns = self.inner.columns
        self.last_request = None
        self.fetch_calls = 0

    def validate_columns(self, selected_columns):
        return self.inner.validate_columns(selected_columns)

    def load_filter_options(self):
        return {"statuses": [], "employees": [], "areas": [], "customers": []}

    def fetch_report(self, request: DailyFollowupReportRequest):
        self.fetch_calls += 1
        self.last_request = request
        columns = self.validate_columns(request.selected_columns)
        return DailyFollowupReportResult(
            columns=columns,
            rows=[{"customer_name": "Ali", "notes": "Call back", "customer_phone": "010"}],
        )


class _RemoteCustomerReportService(_ReportService):
    def __init__(self):
        super().__init__()
        self.customer_search_calls = []
        self.results_by_query = {
            "": [
                {"id": i, "label": f"Customer {i:03d} - C{i:03d}", "name": f"Customer {i:03d}", "phone": f"010{i:03d}"}
                for i in range(1, 101)
            ],
            "Customer 500": [
                {"id": 500, "label": "Customer 500 - C500", "name": "Customer 500", "phone": "010500"}
            ],
            "C500": [
                {"id": 500, "label": "Customer 500 - C500", "name": "Customer 500", "phone": "010500"}
            ],
            "محمد": [
                {"id": 501, "label": "محمد أحمد - C501", "name": "محمد أحمد", "phone": "010501"},
                {"id": 502, "label": "محمد علي - C502", "name": "محمد علي", "phone": "010502"},
                {"id": 503, "label": "أحمد محمد - C503", "name": "أحمد محمد", "phone": "010503"},
            ],
        }

    def load_filter_options(self):
        return {"statuses": [], "employees": [], "areas": [], "customers": self.lookup_customers("", limit=100)}

    def lookup_customers(self, keyword, limit=100):
        text = "" if keyword is None else str(keyword).strip()
        self.customer_search_calls.append((text, limit))
        return list(self.results_by_query.get(text, []))[:limit]


def test_smart_report_screen_applies_selected_columns_to_results_table():
    _app()
    settings = _Settings()
    service = _ReportService()
    screen = DailyFollowupSmartReportScreen(service, settings)

    screen.set_selected_columns(["customer_name", "notes"])
    screen.apply_filters()

    assert screen.layoutDirection() == Qt.RightToLeft
    assert screen.table.columnCount() == 2
    assert screen.table.horizontalHeaderItem(0).text() == service.validate_columns(["customer_name"])[0].label
    assert screen.table.item(0, 0).text() == "Ali"
    assert screen.table.item(0, 1).text() == "Call back"
    assert service.last_request.selected_columns == ["customer_name", "notes"]
    assert settings.saved["selected_columns"] == ["customer_name", "notes"]


def test_smart_report_screen_restores_saved_filters_without_running_report_on_open():
    _app()
    settings = _Settings(
        {
            "selected_columns": ["customer_name"],
            "join_mode": "OR",
            "filters": [
                {
                    "column": "notes",
                    "operator": "contains",
                    "value": "Call",
                    "value_to": "",
                }
            ],
        }
    )
    service = _ReportService()

    screen = DailyFollowupSmartReportScreen(service, settings)

    assert service.fetch_calls == 0
    assert service.last_request is None
    assert screen.table.rowCount() == 0
    assert screen.join_mode.currentText() == "OR"
    assert screen.selected_column_keys() == ["customer_name"]
    restored_condition = screen.filter_rows[0].condition()
    assert restored_condition is not None
    assert restored_condition.column == "notes"
    assert restored_condition.operator == "contains"
    assert restored_condition.value == "Call"
    assert settings.saved is None

    screen.apply_filters()

    assert service.last_request.join_mode == "OR"
    assert service.last_request.selected_columns == ["customer_name"]
    assert len(service.last_request.filters) == 1
    assert service.last_request.filters[0].column == "notes"
    assert service.last_request.filters[0].operator == "contains"
    assert service.last_request.filters[0].value == "Call"
    assert settings.saved["filters"][0]["value"] == "Call"


def test_filter_dropdown_values_match_visible_text_columns():
    _app()
    columns = DailyFollowupReportService().columns
    options = {
        "statuses": [{"id": 2, "label": "Interested"}],
        "employees": [{"id": 7, "label": "Ahmed Hafez"}],
        "areas": [{"id": 3, "label": "Cairo"}],
        "customers": [{"id": 1001, "label": "Ali - 010", "name": "Ali", "phone": "010"}],
    }

    cases = [
        ("employee_name", "Ahmed Hafez"),
        ("case_status_name", "Interested"),
        ("area_number", "Cairo"),
        ("customer_name", 1001),
        ("customer_phone", "010"),
        ("customer_id", 1001),
        ("place_area_feddan", 3),
    ]

    for column_key, expected_value in cases:
        row = _FilterRow(columns, options, lambda _row: None)
        row.column_combo.setCurrentIndex(row.column_combo.findData(column_key))
        row.value_combo.setCurrentIndex(1)
        condition = row.condition()

        assert condition is not None
        assert condition.value == expected_value


def test_filter_dropdown_keeps_zero_as_valid_value():
    _app()
    columns = DailyFollowupReportService().columns
    options = {"customers": [{"id": 0, "label": "Zero Customer", "name": "Zero", "phone": "000"}]}
    row = _FilterRow(columns, options, lambda _row: None)
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_id"))
    row.value_combo.setCurrentIndex(1)

    condition = row.condition()

    assert condition is not None
    assert condition.value == 0


def test_filter_dropdowns_support_contains_completion():
    _app()
    columns = DailyFollowupReportService().columns
    options = {
        "employees": [
            {"id": 1, "label": "Ahmed Hafez"},
            {"id": 2, "label": "Mohamed Ali"},
        ]
    }
    row = _FilterRow(columns, options, lambda _row: None)
    row.column_combo.setCurrentIndex(row.column_combo.findData("employee_name"))

    assert row.value_combo.isEditable()
    assert row.value_combo.completer().filterMode() == Qt.MatchContains
    assert row.value_combo.completer().completionMode() == QCompleter.PopupCompletion


def test_customer_name_dropdown_uses_only_remote_results_not_local_completion():
    _app()
    columns = DailyFollowupReportService().columns
    options = {
        "customers": [
            {"id": 1, "label": "Initial Customer - C001", "name": "Initial Customer", "phone": "010001"}
        ]
    }
    row = _FilterRow(
        columns,
        options,
        lambda _row: None,
        customer_lookup_callback=lambda _text, limit=100: [],
    )

    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))

    assert row.value_combo.completer() is None


def test_filter_row_ignores_unmatched_typed_column_text():
    _app()
    columns = DailyFollowupReportService().columns
    row = _FilterRow(columns, {}, lambda _row: None)
    row.column_combo.setCurrentIndex(-1)
    row.column_combo.setEditText("missing column")
    row.value_input.setText("value")

    assert row.condition() is None


def test_customer_name_dropdown_remote_searches_full_customer_lookup_after_debounce():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))
    row._customer_lookup_timer.setInterval(1)

    row.value_combo.lineEdit().setText("Customer 500")
    QTest.qWait(20)

    assert ("Customer 500", 100) in service.customer_search_calls
    assert row.value_combo.findData(500) >= 0
    assert row.value_combo.count() <= 101


def test_customer_name_partial_search_opens_dropdown_with_all_matching_names():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    screen.show()
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))
    row._customer_lookup_timer.setInterval(1)

    row.value_combo.lineEdit().setText("محمد")
    QTest.qWait(20)

    assert [row.value_combo.itemData(index) for index in range(3)] == [501, 502, 503]
    assert row._customer_lookup_completer.popup().isVisible()


def test_customer_name_search_keeps_text_focus_after_results_open():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    screen.show()
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))
    row._customer_lookup_timer.setInterval(1)
    line_edit = row.value_combo.lineEdit()
    line_edit.setFocus()
    line_edit.setText("محمد")
    QTest.qWait(20)

    assert row._customer_lookup_completer.popup().isVisible()
    assert line_edit.hasFocus()
    assert line_edit.cursorPosition() == len("محمد")


def test_customer_name_search_arrows_navigate_dropdown_without_leaving_text_field():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    screen.show()
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))
    row._customer_lookup_timer.setInterval(1)
    line_edit = row.value_combo.lineEdit()
    line_edit.setFocus()
    line_edit.setText("محمد")
    QTest.qWait(40)

    QTest.keyClick(line_edit, Qt.Key_Down)
    QTest.qWait(80)
    assert line_edit.hasFocus()
    assert line_edit.text() == "محمد"

    QTest.keyClick(line_edit, Qt.Key_Down)
    QTest.qWait(80)
    assert line_edit.hasFocus()
    assert line_edit.text() == "محمد"

    QTest.keyClick(line_edit, Qt.Key_Return)
    assert row.value_combo.currentData() == 503
    assert row.value_combo.currentText() == "أحمد محمد - C503"


def test_customer_name_typed_text_is_not_used_as_filter_without_selecting_a_customer():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))

    row.value_combo.setCurrentIndex(-1)
    row.value_combo.lineEdit().setText("محمد")

    assert row.condition() is None


def test_customer_name_dropdown_searches_by_customer_code_and_clearing_restores_default():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))
    row._customer_lookup_timer.setInterval(1)

    row.value_combo.lineEdit().setText("C500")
    QTest.qWait(20)
    assert row.value_combo.findData(500) >= 0

    row.value_combo.lineEdit().clear()
    QTest.qWait(20)

    assert service.customer_search_calls[-1] == ("", 100)
    assert row.value_combo.findData(1) >= 0
    assert row.value_combo.findData(500) < 0
    assert row.value_combo.count() <= 101


def test_selected_remote_customer_remains_visible_and_filters_by_customer_id():
    _app()
    service = _RemoteCustomerReportService()
    screen = DailyFollowupSmartReportScreen(service, _Settings())
    row = screen.filter_rows[0]
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))

    row._apply_customer_lookup_results(
        1,
        "Customer 500",
        [{"id": 500, "label": "Customer 500 - C500", "name": "Customer 500", "phone": "010500"}],
    )
    row.value_combo.setCurrentIndex(row.value_combo.findData(500))
    row._apply_customer_lookup_results(2, "", service.results_by_query[""])

    condition = row.condition()
    assert condition is not None
    assert condition.value == 500
    assert row.value_combo.currentData() == 500
    assert row.value_combo.currentText() == "Customer 500 - C500"


def test_customer_name_dropdown_ignores_stale_remote_search_results():
    _app()
    columns = DailyFollowupReportService().columns
    row = _FilterRow(
        columns,
        {"customers": []},
        lambda _row: None,
        customer_lookup_callback=lambda _text, limit=100: [],
    )
    row.column_combo.setCurrentIndex(row.column_combo.findData("customer_name"))
    row._customer_lookup_request_id = 2

    row._apply_customer_lookup_results(
        1,
        "old",
        [{"id": 400, "label": "Old Customer - C400", "name": "Old Customer", "phone": "010400"}],
    )
    assert row.value_combo.findData(400) < 0

    row._apply_customer_lookup_results(
        2,
        "new",
        [{"id": 500, "label": "New Customer - C500", "name": "New Customer", "phone": "010500"}],
    )
    assert row.value_combo.findData(500) >= 0
