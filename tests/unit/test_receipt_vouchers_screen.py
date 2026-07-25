"""Tests for the Receipt Vouchers screen stack (شاشة سندات القبض).

Mirrors the companies screen wiring: a ``TableSpec`` drives the generic
``BaseCrudScreen`` + ``ReviewDataService``, so these tests cover the pieces that
are specific to receipt vouchers:

* the ``receipt_vouchers`` TableSpec shape (grid columns, search entries,
  required fields),
* the service's joined list query (customer name via the customers JOIN; search
  by voucher number, customer name, or date),
* the save special-case: an empty voucher number is dropped so the database
  DEFAULT (the PA-<n> sequence) assigns it — never an explicit NULL,
* the screen: dropdown editors for customer / company / payment type, the
  duplicate-number guard, and the amount-must-be-positive guard.

No real database is used — a fake connection/service records calls and returns
canned rows. Qt runs headless (offscreen).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.services.review_data_service import ReviewDataService, TABLE_SPECS


# --------------------------------------------------------------------------- #
# TableSpec
# --------------------------------------------------------------------------- #
def test_receipt_vouchers_spec_basics():
    spec = TABLE_SPECS["receipt_vouchers"]
    assert spec.table_name == "receipt_vouchers"
    assert spec.primary_key == "id"
    # Searchable by voucher number, customer name, or date.
    assert spec.search_columns == ("voucher_number", "customer_name", "voucher_date")
    field_by_name = {f.name: f for f in spec.fields}
    # Required business fields (voucher_number is NOT required: empty = automatic).
    assert field_by_name["voucher_number"].required is False
    assert field_by_name["voucher_date"].required is True
    assert field_by_name["customer_id"].required is True
    assert field_by_name["company_id"].required is True
    assert field_by_name["payment_type"].required is True
    assert field_by_name["amount"].required is True
    # id is read-only (IDENTITY, never written by the app).
    assert field_by_name["id"].readonly is True
    # customer_name is grid-only: from the JOIN, never an editor, never written.
    assert field_by_name["customer_name"].virtual is True
    assert field_by_name["customer_name"].hidden_on_form is True


def test_receipt_vouchers_use_the_lookup_layout_like_companies():
    from app.ui.common.theme import LOOKUP_LAYOUT_KEYS

    assert "receipt_vouchers" in LOOKUP_LAYOUT_KEYS


def test_search_grid_shows_only_the_four_requested_columns():
    spec = TABLE_SPECS["receipt_vouchers"]
    # رقم السند، اسم العميل، التاريخ، المبلغ — in this order only.
    assert spec.list_columns == ("voucher_number", "customer_name", "voucher_date", "amount")


# --------------------------------------------------------------------------- #
# Service: joined list query + search
# --------------------------------------------------------------------------- #
def _render(query) -> str:
    try:
        return query.as_string(None)
    except Exception:  # pragma: no cover - defensive
        return str(query)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple] = []
        self.closed = False

    def execute(self, query, params=None):
        self.calls.append((_render(query), params))
        return _Cursor(self.rows)


def _service_with(conn) -> ReviewDataService:
    service = ReviewDataService()
    service._connection = conn
    return service


def test_list_joins_customers_for_the_name_column():
    conn = _FakeConn([])
    service = _service_with(conn)

    service.list_records(TABLE_SPECS["receipt_vouchers"], "")

    sql, params = conn.calls[0]
    assert "FROM receipt_vouchers rv" in sql
    assert "LEFT JOIN customers c ON c.customer_id = rv.customer_id" in sql
    # The grid columns are selected with their aliases (plus the id for selection).
    for alias in ("id", "voucher_number", "customer_name", "voucher_date", "amount"):
        assert f'"{alias}"' in sql
    # No keyword: no WHERE, just the LIMIT param.
    assert "WHERE" not in sql
    assert params == [500]


def test_search_matches_number_name_or_date():
    conn = _FakeConn([])
    service = _service_with(conn)

    service.list_records(TABLE_SPECS["receipt_vouchers"], "PA-00")

    sql, params = conn.calls[0]
    # One ILIKE per search entry: voucher number, customer name, voucher date.
    assert sql.count("ILIKE") == 3
    assert "rv.voucher_number" in sql
    assert "c.customer_name" in sql
    assert "rv.voucher_date" in sql
    assert params == ["%PA-00%", "%PA-00%", "%PA-00%", 500]


# --------------------------------------------------------------------------- #
# Service: automatic-number save special-case
# --------------------------------------------------------------------------- #
def _valid_payload(**overrides):
    payload = {
        "voucher_number": "",
        "voucher_date": "2026-07-13",
        "customer_id": 29582,
        "company_id": 10,
        "payment_type": "cash",
        "amount": "150.00",
        "description": "",
    }
    payload.update(overrides)
    return payload


def test_empty_voucher_number_is_dropped_so_db_default_numbers():
    conn = _FakeConn([{"id": 1}])
    service = _service_with(conn)

    service.save_record(TABLE_SPECS["receipt_vouchers"], _valid_payload(), None)

    sql, params = conn.calls[0]
    # INSERT without voucher_number: the DB DEFAULT (PA- sequence) assigns it
    # (the only None left is the legitimately-nullable empty description).
    assert "INSERT INTO" in sql
    assert "voucher_number" not in sql
    assert "PA-" not in str(params)


def test_manual_voucher_number_is_inserted_explicitly():
    conn = _FakeConn([{"id": 1}])
    service = _service_with(conn)

    service.save_record(
        TABLE_SPECS["receipt_vouchers"], _valid_payload(voucher_number="PA-777"), None
    )

    sql, params = conn.calls[0]
    assert '"voucher_number"' in sql
    assert "PA-777" in params


def test_blank_number_on_update_keeps_the_stored_number():
    conn = _FakeConn([{"id": 9}])
    service = _service_with(conn)

    service.save_record(TABLE_SPECS["receipt_vouchers"], _valid_payload(), 9)

    sql, _params = conn.calls[0]
    assert "UPDATE" in sql
    # The stored number is left untouched (not set to NULL).
    assert "voucher_number" not in sql


def test_missing_required_fields_raise_arabic_message():
    conn = _FakeConn([{"id": 1}])
    service = _service_with(conn)

    with pytest.raises(ValueError) as excinfo:
        service.save_record(
            TABLE_SPECS["receipt_vouchers"], _valid_payload(customer_id=None), None
        )
    assert "العميل" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# UI: editors, duplicate guard, amount guard
# --------------------------------------------------------------------------- #
class _FakeUiService:
    """Minimal service for building a ReceiptVouchersScreen without a database."""

    def __init__(self, duplicate_number: bool = False):
        self._duplicate_number = duplicate_number
        self.save_calls: list[tuple] = []
        self.value_exists_calls: list[tuple] = []

    def list_records(self, spec, keyword: str = "", limit: int = 500):
        return []

    def list_customers_for_selection(self):
        return [
            {"customer_id": 29582, "customer_name": "عميل تجريبي", "phone_number": "0500000000"},
        ]

    def list_companies_for_selection(self):
        return [
            {"id": 10, "name_ar": "شركة الأولى"},
            {"id": 11, "name_ar": "شركة الثانية"},
        ]

    def get_company_details(self, company_id):
        return {
            10: {
                "id": 10,
                "name_ar": "شركة الأولى",
                "commercial_registration": "1010101010",
                "vat_number": "310000000000003",
                "address_ar": "الرياض",
            },
        }.get(company_id)

    def value_exists(self, spec, column, value, exclude_id=None):
        self.value_exists_calls.append((column, value, exclude_id))
        return self._duplicate_number and column == "voucher_number"

    def save_record(self, spec, payload, record_id):
        self.save_calls.append((dict(payload), record_id))
        return 1

    def get_record(self, spec, record_id):
        return None


class _FakeNumbering:
    peeked = 0
    registered: list[str] = []

    @classmethod
    def reset(cls):
        cls.peeked = 0
        cls.registered = []

    def peek_next_number(self):
        type(self).peeked += 1
        return "PA-001"

    def register_manual_number(self, number):
        type(self).registered.append(number)


class _FakeMessageBox:
    warnings: list[tuple] = []
    infos: list[tuple] = []

    @classmethod
    def reset(cls):
        cls.warnings = []
        cls.infos = []

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        _FakeMessageBox.warnings.append((title, text))

    @staticmethod
    def information(parent, title, text, *args, **kwargs):
        _FakeMessageBox.infos.append((title, text))

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):  # pragma: no cover
        pass


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _make_screen(service, monkeypatch):
    import app.ui.screens.receipt_vouchers_screen as mod

    # Never touch the real numbering sequence from UI tests.
    monkeypatch.setattr(mod, "ReceiptVoucherNumberingService", _FakeNumbering)
    _FakeNumbering.reset()
    return mod.ReceiptVouchersScreen(service)


def _fill_valid_form(screen, number: str = ""):
    screen.inputs["voucher_number"].setText(number)
    screen.inputs["voucher_date"].setText("2026-07-13")
    screen.voucher_customer_combo.setCurrentIndex(1)   # عميل تجريبي (29582)
    screen.voucher_company_combo.setCurrentIndex(1)    # first company
    screen.payment_type_combo.setCurrentIndex(1)       # كاش (cash)
    screen.inputs["amount"].setText("150.00")


def test_screen_builds_with_dropdowns_and_grid(qt_app, monkeypatch):
    from PySide6.QtWidgets import QComboBox

    screen = _make_screen(_FakeUiService(), monkeypatch)
    assert "رقم السند" in screen.SEARCH_PLACEHOLDER
    assert "اسم العميل" in screen.SEARCH_PLACEHOLDER
    assert "التاريخ" in screen.SEARCH_PLACEHOLDER
    # Single-column form: every field on its own line (companies design).
    assert screen.FORM_COLUMNS == 1
    # Every form field has an editor.
    for name in (
        "id",
        "voucher_number",
        "voucher_date",
        "customer_id",
        "company_id",
        "payment_type",
        "amount",
        "description",
    ):
        assert name in screen.inputs
    # Customer / company / payment type are dropdowns storing ids/codes.
    assert isinstance(screen.inputs["customer_id"], QComboBox)
    assert isinstance(screen.inputs["company_id"], QComboBox)
    assert isinstance(screen.inputs["payment_type"], QComboBox)
    # Payment type shows the Arabic labels for the two stored codes.
    combo = screen.payment_type_combo
    labels = [combo.itemText(i) for i in range(combo.count())]
    codes = [combo.itemData(i) for i in range(combo.count())]
    assert labels == ["", "كاش", "تحويل"]
    assert codes == [None, "cash", "bank_transfer"]
    # The search grid exposes exactly the four requested columns.
    assert screen.table.columnCount() == 4
    headers = [screen.table.horizontalHeaderItem(i).text() for i in range(4)]
    assert headers == ["رقم السند", "اسم العميل", "التاريخ", "المبلغ"]


def test_duplicate_voucher_number_blocks_save(qt_app, monkeypatch):
    import app.ui.screens.receipt_vouchers_screen as mod

    service = _FakeUiService(duplicate_number=True)
    screen = _make_screen(service, monkeypatch)
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    _fill_valid_form(screen, number="PA-001")

    screen.save_record()

    # A clear Arabic warning was shown and the record was NOT saved.
    assert _FakeMessageBox.warnings
    assert "رقم السند" in _FakeMessageBox.warnings[0][1]
    assert service.save_calls == []


@pytest.mark.parametrize("bad_amount", ["0", "-5", "0.00", "abc", ""])
def test_zero_negative_or_invalid_amount_blocks_save(qt_app, monkeypatch, bad_amount):
    import app.ui.screens.receipt_vouchers_screen as mod

    service = _FakeUiService()
    screen = _make_screen(service, monkeypatch)
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    _fill_valid_form(screen)
    screen.inputs["amount"].setText(bad_amount)

    screen.save_record()

    assert _FakeMessageBox.warnings
    assert "أكبر من صفر" in _FakeMessageBox.warnings[0][1]
    assert service.save_calls == []


def test_valid_voucher_saves_ids_not_names(qt_app, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs
    import app.ui.screens.receipt_vouchers_screen as mod

    service = _FakeUiService()
    screen = _make_screen(service, monkeypatch)
    # Both the subclass and the base use QMessageBox; silence both.
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    _fill_valid_form(screen)

    screen.save_record()

    assert len(service.save_calls) == 1
    payload, record_id = service.save_calls[0]
    assert record_id is None
    # The dropdowns submit the ids/codes, never the display names.
    assert payload.get("customer_id") == 29582
    assert payload.get("company_id") == 10
    assert payload.get("payment_type") == "cash"
    assert payload.get("amount") == "150.00"
    # Empty number: nothing was registered against the sequence (DB DEFAULT numbers).
    assert _FakeNumbering.registered == []


def test_manual_number_is_registered_after_successful_save(qt_app, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs
    import app.ui.screens.receipt_vouchers_screen as mod

    service = _FakeUiService()
    screen = _make_screen(service, monkeypatch)
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    _fill_valid_form(screen, number="PA-500")

    screen.save_record()

    assert len(service.save_calls) == 1
    payload, _ = service.save_calls[0]
    assert payload.get("voucher_number") == "PA-500"
    # The sequence ceiling was advanced past the manual number after the save.
    assert _FakeNumbering.registered == ["PA-500"]


def test_print_voucher_button_installed_and_collects_data(qt_app, monkeypatch):
    import datetime as _dt

    service = _FakeUiService()
    screen = _make_screen(service, monkeypatch)

    # The "طباعة سند" button was added to the toolbar via the base hook.
    assert hasattr(screen, "print_voucher_button")
    assert screen.print_voucher_button.text() == "طباعة سند"

    screen.mode = "new"
    _fill_valid_form(screen, number="PA-009")
    screen.inputs["voucher_date"].setText("2026-04-02")
    screen.inputs["description"].setText("قيمة بضاعة")

    data = screen._collect_voucher_print_data()
    assert data is not None
    assert data["number"] == "PA-009"
    assert data["received_from"] == "عميل تجريبي"
    assert str(data["amount"]) == "150.00"
    assert data["purpose"] == "قيمة بضاعة"
    # The YYYY-MM-DD editor text is parsed to a real date so the template can
    # render it as dd-mm-yyyy (like the invoice voucher).
    assert data["issue_date"] == _dt.date(2026, 4, 2)
    # Seller header comes from the companies master (same identity as the invoice).
    assert data["seller"]["name"] == "شركة الأولى"
    assert data["seller"]["cr"] == "1010101010"
    assert data["seller"]["vat"] == "310000000000003"
    assert data["seller"]["address"] == "الرياض"


def test_print_voucher_on_empty_screen_shows_info_and_no_dialog(qt_app, monkeypatch):
    import app.ui.screens.receipt_vouchers_screen as mod

    service = _FakeUiService()
    screen = _make_screen(service, monkeypatch)
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    # Fresh screen: nothing selected/entered → no print data, gentle info message
    # (and crucially no QWebEngineView dialog is constructed).
    screen.current_id = None
    assert screen._collect_voucher_print_data() is None
    screen.print_voucher()
    assert _FakeMessageBox.infos
    assert "اختر سنداً" in _FakeMessageBox.infos[0][1]


def test_new_record_defaults_date_and_shows_auto_hint(qt_app, monkeypatch):
    import datetime

    import app.ui.screens.base_crud_screen as bcs

    service = _FakeUiService()
    screen = _make_screen(service, monkeypatch)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.new_record()

    # Date defaults to today; the number field stays EMPTY (automatic) with the
    # upcoming PA- number shown as a hint.
    assert screen.inputs["voucher_date"].text() == datetime.date.today().strftime("%Y-%m-%d")
    assert screen.inputs["voucher_number"].text() == ""
    assert "PA-001" in screen.inputs["voucher_number"].placeholderText()
    assert _FakeNumbering.peeked == 1
