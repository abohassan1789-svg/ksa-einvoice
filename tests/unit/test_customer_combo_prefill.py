from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from app.services.review_data_service import TABLE_SPECS
from app.ui.dialogs.customer_lookup_dialog import CustomerLookupDialog
import app.ui.screens.daily_followups_screen as daily_module
from app.ui.screens.base_crud_screen import BaseCrudScreen
from app.ui.screens.daily_followups_screen import DailyFollowupsScreen


def test_set_combo_value_with_label_adds_selected_customer_when_missing():
    app = QApplication.instance() or QApplication([])
    _ = app
    combo = QComboBox()
    combo.addItem("", None)

    BaseCrudScreen._set_combo_value_with_label(combo, 9999, "01000000000")

    assert combo.currentData() == 9999
    assert combo.currentText() == "01000000000"


class _FakeReviewService:
    customers = []
    employees = []
    statuses = []

    def list_records(self, spec, keyword="", limit=500):
        return []

    def next_id(self, spec):
        return 16746

    def list_customers_for_selection(self):
        return list(self.customers)

    def list_employees_for_selection(self):
        return list(self.employees)

    def list_case_statuses_for_selection(self):
        return list(self.statuses)

    def list_places_for_selection(self):
        return []

    def search_customers(self, keyword, limit=100):
        text = "" if keyword is None else str(keyword).strip()
        rows = list(self.customers)
        if text:
            rows = [
                row
                for row in rows
                if text in str(row.get("phone_number") or "")
                or text.lower() in str(row.get("customer_name") or "").lower()
            ]
        return rows[:limit]

    def get_record(self, spec, record_id):
        if spec == TABLE_SPECS["customers"]:
            return {
                "customer_id": record_id,
                "customer_name": "ظ…ط­ظ…ط¯ ط·ط§ط±ظ‚",
                "phone_number": "01100498666",
                "place_area_feddan": 290,
                "area_number": None,
                "legacy_area_number_2": "5ط£",
                "building": "421",
                "unit_number": "9",
                "floor_number": "ط§ظ„ط«ط§ظ†ظٹ",
                "installment_duration_years": 5,
                "remaining_installments": 12,
                "installment_amount": 1500,
            }
        return None


def test_customer_prefill_uses_legacy_area_number_when_area_number_is_empty():
    app = QApplication.instance() or QApplication([])
    _ = app
    screen = DailyFollowupsScreen(_FakeReviewService())

    screen._load_customer(4163, prefill_installments=False)

    assert screen.inputs["area_number"].text() == "5ط£"


def test_popup_customer_selection_follows_phone_combo_prefill_path():
    app = QApplication.instance() or QApplication([])
    _ = app
    original_dialog = daily_module.CustomerLookupDialog

    class _SelectedCustomerDialog:
        Accepted = QDialog.Accepted

        def __init__(self, service, parent=None):
            self.selected_customer_id = 4163
            self.selected_customer = {
                "customer_id": 4163,
                "phone_number": "01100498666",
                "customer_name": "ظ…ط­ظ…ط¯ ط·ط§ط±ظ‚",
            }

        def exec(self):
            return self.Accepted

    daily_module.CustomerLookupDialog = _SelectedCustomerDialog
    try:
        screen = DailyFollowupsScreen(_FakeReviewService())

        screen.open_customer_lookup()
    finally:
        daily_module.CustomerLookupDialog = original_dialog

    assert screen.customer_phone_combo.currentText() == "01100498666"
    assert screen.inputs["customer_id"].text() == "4163"
    assert screen.inputs["installment_duration_years"].text() == "5"
    assert screen.inputs["remaining_installments"].text() == "12"
    assert screen.inputs["installment_amount"].text() == "1500"


def test_customer_lookup_dialog_includes_phone_and_name_columns():
    app = QApplication.instance() or QApplication([])
    _ = app

    class _LookupService(_FakeReviewService):
        def search_customers(self, keyword, limit=100):
            return [
                {
                    "customer_id": 4163,
                    "phone_number": "01100498666",
                    "customer_name": "ط¸â€¦ط·آ­ط¸â€¦ط·آ¯ ط·آ·ط·آ§ط·آ±ط¸â€ڑ",
                }
            ]

    dialog = CustomerLookupDialog(_LookupService())

    headers = [dialog.table.horizontalHeaderItem(column).text() for column in range(dialog.table.columnCount())]
    values = [dialog.table.item(0, column).text() for column in range(dialog.table.columnCount())]

    assert dialog.table.columnCount() == 2
    assert TABLE_SPECS["customers"].fields[1].label in headers
    assert TABLE_SPECS["customers"].fields[2].label in headers
    assert dialog.table.item(0, 1).text()
    assert "01100498666" in values


def test_customer_lookup_dialog_arabic_labels_are_readable():
    app = QApplication.instance() or QApplication([])
    _ = app

    class _LookupService(_FakeReviewService):
        def search_customers(self, keyword, limit=100):
            return []

    dialog = CustomerLookupDialog(_LookupService())

    assert dialog.windowTitle() == "اختيار العميل / Select Customer"
    assert dialog.search_text.placeholderText() == "ابحث بالاسم أو رقم الهاتف"
    assert dialog.select_button.text() == "اختيار / Select"
    assert dialog.count_label.text() == "لا يوجد عملاء"


def test_customer_lookup_dialog_shows_phone_and_name_only_with_limited_initial_load():
    app = QApplication.instance() or QApplication([])
    _ = app

    class _LookupService(_FakeReviewService):
        def __init__(self):
            self.search_calls = []

        def search_customers(self, keyword, limit=100):
            self.search_calls.append((keyword, limit))
            return [
                {
                    "customer_id": 4163,
                    "phone_number": "01100498666",
                    "customer_name": "Customer A",
                }
            ]

    service = _LookupService()
    dialog = CustomerLookupDialog(service)

    assert service.search_calls == [("", 100)]
    assert dialog.table.columnCount() == 2
    assert [dialog.table.item(0, column).text() for column in range(2)] == [
        "01100498666",
        "Customer A",
    ]


def test_customer_lookup_dialog_select_button_accepts_selected_customer():
    app = QApplication.instance() or QApplication([])
    _ = app

    class _LookupService(_FakeReviewService):
        def search_customers(self, keyword, limit=100):
            return [
                {
                    "customer_id": 4163,
                    "phone_number": "01100498666",
                    "customer_name": "Customer A",
                }
            ]

    dialog = CustomerLookupDialog(_LookupService())

    dialog.table.selectRow(0)
    dialog.select_button.click()

    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_customer_id == 4163


def test_customer_lookup_dialog_preserves_hidden_customer_id_and_full_row():
    app = QApplication.instance() or QApplication([])
    _ = app

    class _LookupService(_FakeReviewService):
        def search_customers(self, keyword, limit=100):
            return [
                {
                    "customer_id": 4163,
                    "phone_number": "01100498666",
                    "customer_name": "Customer A",
                    "building": "421",
                }
            ]

    dialog = CustomerLookupDialog(_LookupService())

    dialog.accept_selected_row(0, 0)

    assert dialog.selected_customer_id == 4163
    assert dialog.selected_customer["customer_id"] == 4163
    assert dialog.selected_customer["building"] == "421"


def test_customer_lookup_dialog_double_click_selects_row_customer_id():
    app = QApplication.instance() or QApplication([])
    _ = app

    class _LookupService(_FakeReviewService):
        def search_customers(self, keyword, limit=100):
            return [
                {
                    "customer_id": 4163,
                    "phone_number": "01100498666",
                    "customer_name": "Customer A",
                }
            ]

    dialog = CustomerLookupDialog(_LookupService())

    dialog.accept_selected_row(0, 0)

    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_customer_id == 4163


def test_search_by_phone_then_double_click_fills_daily_followup_customer_fields():
    app = QApplication.instance() or QApplication([])
    _ = app
    original_dialog = daily_module.CustomerLookupDialog

    class _SearchAndDoubleClickDialog(CustomerLookupDialog):
        def exec(self):
            self.search_text.setText("498666")
            self.search_timer.stop()
            self.refresh_table()
            self.accept_selected_row(0, 0)
            return self.result()

    class _Service(_FakeReviewService):
        def __init__(self):
            self.customers = [
                {"customer_id": 4163, "phone_number": "01100498666", "customer_name": "Customer A"},
            ]
            self.customer_load_calls = 0

        def list_customers_for_selection(self):
            self.customer_load_calls += 1
            return []

        def get_record(self, spec, record_id):
            if spec == TABLE_SPECS["customers"]:
                return {
                    "customer_id": record_id,
                    "customer_name": "Customer A",
                    "phone_number": "01100498666",
                    "place_area_feddan": 290,
                    "area_number": "5",
                    "building": "421",
                    "unit_number": "9",
                    "floor_number": "2",
                    "installment_duration_years": 5,
                    "remaining_installments": 12,
                    "installment_amount": 1500,
                }
            return None

    daily_module.CustomerLookupDialog = _SearchAndDoubleClickDialog
    try:
        service = _Service()
        screen = DailyFollowupsScreen(service)

        screen.open_customer_lookup()
    finally:
        daily_module.CustomerLookupDialog = original_dialog

    assert service.customer_load_calls == 0
    assert screen.inputs["customer_id"].text() == "4163"
    assert screen.customer_name_combo.currentText() == "Customer A"
    assert screen.customer_phone_combo.currentText() == "01100498666"
    assert screen.inputs["place_area_feddan"].text() == "290"
    assert screen.inputs["area_number"].text() == "5"
    assert screen.inputs["building"].text() == "421"
    assert screen.inputs["unit_number"].text() == "9"
    assert screen.inputs["floor_number"].text() == "2"


def test_search_by_name_then_select_button_fills_daily_followup_customer_fields():
    app = QApplication.instance() or QApplication([])
    _ = app
    original_dialog = daily_module.CustomerLookupDialog

    class _SearchAndSelectDialog(CustomerLookupDialog):
        def exec(self):
            self.search_text.setText("Customer")
            self.search_timer.stop()
            self.refresh_table()
            self.table.selectRow(0)
            self.select_button.click()
            return self.result()

    class _Service(_FakeReviewService):
        def __init__(self):
            self.customers = [
                {"customer_id": 4163, "phone_number": "01100498666", "customer_name": "Customer A"},
            ]
            self.customer_load_calls = 0

        def list_customers_for_selection(self):
            self.customer_load_calls += 1
            return []

        def get_record(self, spec, record_id):
            if spec == TABLE_SPECS["customers"]:
                return {
                    "customer_id": record_id,
                    "customer_name": "Customer A",
                    "phone_number": "01100498666",
                    "place_area_feddan": 290,
                    "area_number": "5",
                    "building": "421",
                    "unit_number": "9",
                    "floor_number": "2",
                }
            return None

    daily_module.CustomerLookupDialog = _SearchAndSelectDialog
    try:
        service = _Service()
        screen = DailyFollowupsScreen(service)

        screen.open_customer_lookup()
    finally:
        daily_module.CustomerLookupDialog = original_dialog

    assert service.customer_load_calls == 0
    assert screen.inputs["customer_id"].text() == "4163"
    assert screen.customer_name_combo.currentText() == "Customer A"
    assert screen.customer_phone_combo.currentText() == "01100498666"
    assert screen.inputs["place_area_feddan"].text() == "290"
    assert screen.inputs["area_number"].text() == "5"
    assert screen.inputs["building"].text() == "421"
    assert screen.inputs["unit_number"].text() == "9"
    assert screen.inputs["floor_number"].text() == "2"


def test_customer_name_search_button_opens_lookup_dialog():
    app = QApplication.instance() or QApplication([])
    _ = app
    original_dialog = daily_module.CustomerLookupDialog

    class _SelectedCustomerDialog:
        Accepted = QDialog.Accepted

        def __init__(self, service, parent=None):
            self.selected_customer_id = 4163
            self.selected_customer = {
                "customer_id": 4163,
                "phone_number": "01100498666",
                "customer_name": "ط¸â€¦ط·آ­ط¸â€¦ط·آ¯ ط·آ·ط·آ§ط·آ±ط¸â€ڑ",
            }

        def exec(self):
            return self.Accepted

    class _AsciiCustomerService(_FakeReviewService):
        def get_record(self, spec, record_id):
            record = super().get_record(spec, record_id)
            if spec == TABLE_SPECS["customers"] and record:
                record["customer_name"] = "Customer A"
            return record

    daily_module.CustomerLookupDialog = _SelectedCustomerDialog
    try:
        screen = DailyFollowupsScreen(_AsciiCustomerService())

        screen.customer_name_lookup_button.click()
    finally:
        daily_module.CustomerLookupDialog = original_dialog

    assert screen.inputs["customer_id"].text() == "4163"
    assert screen.customer_name_combo.currentText() == "Customer A"


def test_new_button_opens_customer_lookup_without_loading_customer_combos():
    app = QApplication.instance() or QApplication([])
    _ = app
    original_dialog = daily_module.CustomerLookupDialog

    class _CountingService(_FakeReviewService):
        def __init__(self):
            self.customer_load_calls = 0

        def list_customers_for_selection(self):
            self.customer_load_calls += 1
            return []

    class _SelectedCustomerDialog:
        Accepted = QDialog.Accepted
        opened = 0

        def __init__(self, service, parent=None):
            type(self).opened += 1
            self.selected_customer_id = 4163

        def exec(self):
            return self.Accepted

    daily_module.CustomerLookupDialog = _SelectedCustomerDialog
    try:
        service = _CountingService()
        screen = DailyFollowupsScreen(service)

        screen.new_button.click()
    finally:
        daily_module.CustomerLookupDialog = original_dialog

    assert _SelectedCustomerDialog.opened == 1
    assert service.customer_load_calls == 0
    assert screen.inputs["customer_id"].text() == "4163"


def test_customer_lookup_f5_shortcut_is_application_wide():
    app = QApplication.instance() or QApplication([])
    _ = app
    screen = DailyFollowupsScreen(_FakeReviewService())

    assert screen.customer_pick_shortcut.context() == Qt.ApplicationShortcut


def test_popup_customer_selection_loads_customer_directly_from_selected_id():
    app = QApplication.instance() or QApplication([])
    _ = app
    original_dialog = daily_module.CustomerLookupDialog

    class _SelectedCustomerDialog:
        Accepted = QDialog.Accepted

        def __init__(self, service, parent=None):
            self.selected_customer_id = 4163
            self.selected_customer = {
                "customer_id": 4163,
                "phone_number": "01100498666",
                "customer_name": "Customer A",
            }

        def exec(self):
            return self.Accepted

    class _ScreenWithBrokenComboCommit(DailyFollowupsScreen):
        def _commit_customer_from(self, source, by_text):
            return None

    daily_module.CustomerLookupDialog = _SelectedCustomerDialog
    try:
        screen = _ScreenWithBrokenComboCommit(_FakeReviewService())

        screen.open_customer_lookup()
    finally:
        daily_module.CustomerLookupDialog = original_dialog

    assert screen.inputs["customer_id"].text() == "4163"
    assert screen.inputs["installment_duration_years"].text() == "5"


def test_daily_followups_does_not_preload_customer_combos_but_refresh_does():
    """Performance contract: opening daily follow-ups must NOT load/rebuild the
    full customer combo lists. A customer added elsewhere appears after an
    explicit Refresh.
    """
    app = QApplication.instance() or QApplication([])
    _ = app

    class _CountingService(_FakeReviewService):
        def __init__(self):
            self.customers = [
                {"customer_id": 1, "customer_name": "ط¹ظ…ظٹظ„ 1", "phone_number": "0101"},
            ]
            self.customer_load_calls = 0

        def list_customers_for_selection(self):
            self.customer_load_calls += 1
            return list(self.customers)

    service = _CountingService()
    screen = DailyFollowupsScreen(service)
    assert service.customer_load_calls == 0
    assert screen.customer_phone_combo.count() == 1
    assert screen.customer_name_combo.count() == 1

    # A customer is added elsewhere...
    service.customers = [
        {"customer_id": 1, "customer_name": "ط¹ظ…ظٹظ„ 1", "phone_number": "0101"},
        {"customer_id": 2, "customer_name": "ط¹ظ…ظٹظ„ 2", "phone_number": "0102"},
        {"customer_id": 3, "customer_name": "ط¹ظ…ظٹظ„ ط¨ط¯ظˆظ† ظ‡ط§طھظپ", "phone_number": None},
    ]

    # New: must NOT re-query the DB nor rebuild the combos; selection resets.
    screen.new_record()
    assert service.customer_load_calls == 0
    assert screen.customer_phone_combo.count() == 1
    assert screen.customer_name_combo.count() == 1
    assert screen.customer_phone_combo.currentIndex() == 0   # selection blank
    assert screen.customer_name_combo.currentIndex() == 0

    # Manual Refresh: now the combos are rebuilt and include the new customer.
    screen.refresh_screen()
    assert service.customer_load_calls == 1
    assert screen.customer_phone_combo.findText("0102") >= 0
    assert screen.customer_name_combo.findText("ط¹ظ…ظٹظ„ ط¨ط¯ظˆظ† ظ‡ط§طھظپ") >= 0
def test_employee_and_status_combos_are_searchable_by_partial_text():
    app = QApplication.instance() or QApplication([])
    _ = app
    service = _FakeReviewService()
    employee_name = "\u0645\u062d\u0645\u062f \u0635\u0644\u0627\u062d"
    status_name = "\u0644\u0627 \u064a\u0631\u063a\u0628 \u0628\u0627\u0644\u0628\u064a\u0639"
    service.employees = [{"employee_id": 1, "employee_name": employee_name}]
    service.statuses = [{"case_status_id": 10, "case_status_name": status_name}]

    screen = DailyFollowupsScreen(service)
    employee_combo = screen.inputs["employee_id"]
    status_combo = screen.inputs["case_status_id"]

    assert employee_combo.isEditable()
    assert not employee_combo.lineEdit().isReadOnly()
    assert employee_combo.completer().filterMode() == Qt.MatchContains
    assert employee_combo.findText(employee_name) >= 0
    assert status_combo.isEditable()
    assert not status_combo.lineEdit().isReadOnly()
    assert status_combo.completer().filterMode() == Qt.MatchContains
    assert status_combo.findText(status_name) >= 0


def test_refresh_screen_reloads_employee_and_status_options():
    app = QApplication.instance() or QApplication([])
    _ = app
    service = _FakeReviewService()
    service.employees = [{"employee_id": 1, "employee_name": "\u0645\u062d\u0645\u062f \u0635\u0644\u0627\u062d"}]
    service.statuses = [{"case_status_id": 10, "case_status_name": "\u0645\u0634\u063a\u0648\u0644"}]
    screen = DailyFollowupsScreen(service)

    new_employee = "\u0645\u062d\u0645\u0648\u062f \u062b\u0627\u0628\u062a"
    new_status = "\u0644\u0627 \u064a\u0631\u063a\u0628 \u0628\u0627\u0644\u0628\u064a\u0639"
    service.employees = [
        {"employee_id": 1, "employee_name": "\u0645\u062d\u0645\u062f \u0635\u0644\u0627\u062d"},
        {"employee_id": 2, "employee_name": new_employee},
    ]
    service.statuses = [
        {"case_status_id": 10, "case_status_name": "\u0645\u0634\u063a\u0648\u0644"},
        {"case_status_id": 11, "case_status_name": new_status},
    ]

    screen.refresh_screen()

    assert screen.inputs["employee_id"].findText(new_employee) >= 0
    assert screen.inputs["case_status_id"].findText(new_status) >= 0
