"""Tests for the Companies screen stack (شاشة الشركات).

Mirrors the products screen wiring: a ``TableSpec`` drives the generic
``BaseCrudScreen`` + ``ReviewDataService``, so these tests cover the pieces that
are specific to companies:

* the ``companies`` TableSpec shape (primary key, search columns, required fields),
* ``ReviewDataService.value_exists`` (the UNIQUE-column pre-check query),
* the Excel import/export spec derived for companies,
* the screen's duplicate-guard: a duplicate commercial registration / VAT number
  is rejected with an Arabic message before the save is delegated to the base.

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
def test_companies_spec_basics():
    spec = TABLE_SPECS["companies"]
    assert spec.table_name == "companies"
    assert spec.primary_key == "id"
    # Searchable by name (ar/en), commercial registration and VAT number.
    assert spec.search_columns == (
        "name_ar",
        "name_en",
        "commercial_registration",
        "vat_number",
    )
    field_by_name = {f.name: f for f in spec.fields}
    # Required business fields.
    assert field_by_name["name_ar"].required is True
    assert field_by_name["commercial_registration"].required is True
    assert field_by_name["vat_number"].required is True
    # id is read-only (IDENTITY, never written by the app).
    assert field_by_name["id"].readonly is True
    # Registration / VAT are stored as text (never numeric) to keep leading zeros.
    assert field_by_name["commercial_registration"].data_type == "text"
    assert field_by_name["vat_number"].data_type == "text"


def test_companies_use_the_lookup_layout_like_products():
    from app.ui.common.theme import LOOKUP_LAYOUT_KEYS

    assert "companies" in LOOKUP_LAYOUT_KEYS


def test_search_grid_shows_only_the_three_requested_columns():
    spec = TABLE_SPECS["companies"]
    # Arabic name, VAT number, commercial registration — in this order only.
    assert spec.list_columns == ("name_ar", "vat_number", "commercial_registration")


# --------------------------------------------------------------------------- #
# value_exists (UNIQUE pre-check)
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


def test_value_exists_true_when_row_found():
    conn = _FakeConn([{"?column?": 1}])
    service = _service_with(conn)

    exists = service.value_exists(
        TABLE_SPECS["companies"], "commercial_registration", "  CR-1  "
    )

    assert exists is True
    sql, params = conn.calls[0]
    # Whitespace-insensitive comparison on the given column, of the companies table.
    assert "btrim" in sql
    assert "commercial_registration" in sql
    assert '"companies"' in sql
    # The value is trimmed before comparison.
    assert params == ["CR-1"]


def test_value_exists_excludes_current_row_on_edit():
    conn = _FakeConn([])
    service = _service_with(conn)

    exists = service.value_exists(
        TABLE_SPECS["companies"], "vat_number", "VAT-9", exclude_id=7
    )

    assert exists is False
    sql, params = conn.calls[0]
    assert "<>" in sql  # excludes the row being edited
    assert params == ["VAT-9", 7]


def test_value_exists_blank_is_never_a_duplicate():
    conn = _FakeConn([{"?column?": 1}])
    service = _service_with(conn)

    # Empty / whitespace-only never runs a query and never counts as duplicate.
    assert service.value_exists(TABLE_SPECS["companies"], "vat_number", "   ") is False
    assert conn.calls == []


# --------------------------------------------------------------------------- #
# Excel import/export spec
# --------------------------------------------------------------------------- #
def test_companies_excel_io_spec_matches_grid_columns():
    from app.services.excel_io_service import SUPPORTED_KEYS, io_spec_for

    assert "companies" in SUPPORTED_KEYS
    io_spec = io_spec_for("companies")
    keys = [c.key for c in io_spec.columns]
    # Columns mirror the (restricted) search grid exactly, in the same order.
    assert keys == list(TABLE_SPECS["companies"].list_columns)
    assert keys == ["name_ar", "vat_number", "commercial_registration"]
    required = {c.key for c in io_spec.columns if c.required}
    assert {"name_ar", "commercial_registration", "vat_number"} <= required
    assert io_spec.unique_key == ("commercial_registration",)


# --------------------------------------------------------------------------- #
# UI: duplicate guard
# --------------------------------------------------------------------------- #
class _FakeUiService:
    """Minimal service for building a CompaniesScreen without a database."""

    def __init__(self, duplicate_field: str | None = None):
        self._duplicate_field = duplicate_field
        self.save_calls: list[tuple] = []
        self.value_exists_calls: list[tuple] = []

    def list_records(self, spec, keyword: str = "", limit: int = 500):
        return []

    def value_exists(self, spec, column, value, exclude_id=None):
        self.value_exists_calls.append((column, value, exclude_id))
        return column == self._duplicate_field

    def save_record(self, spec, payload, record_id):
        self.save_calls.append((dict(payload), record_id))
        return 1

    def get_record(self, spec, record_id):
        return None


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


def _make_screen(service):
    from app.ui.screens.companies_screen import CompaniesScreen

    return CompaniesScreen(service)


def test_screen_builds_with_expected_placeholder(qt_app):
    screen = _make_screen(_FakeUiService())
    assert "السجل التجاري" in screen.SEARCH_PLACEHOLDER
    assert "الرقم الضريبي" in screen.SEARCH_PLACEHOLDER
    # Single-column form: every field on its own line.
    assert screen.FORM_COLUMNS == 1
    # Every form field has an editor.
    for name in (
        "id",
        "name_ar",
        "name_en",
        "commercial_registration",
        "vat_number",
        "address_ar",
        "address_en",
    ):
        assert name in screen.inputs
    # The search grid exposes exactly the three requested columns.
    assert screen.table.columnCount() == 3
    headers = [screen.table.horizontalHeaderItem(i).text() for i in range(3)]
    assert headers == ["اسم الشركة (عربي)", "الرقم الضريبي", "السجل التجاري"]


def test_duplicate_commercial_registration_blocks_save(qt_app, monkeypatch):
    import app.ui.screens.companies_screen as mod

    service = _FakeUiService(duplicate_field="commercial_registration")
    screen = _make_screen(service)
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    screen.inputs["name_ar"].setText("شركة")
    screen.inputs["commercial_registration"].setText("CR-DUP")
    screen.inputs["vat_number"].setText("VAT-1")

    screen.save_record()

    # A clear Arabic warning was shown and the record was NOT saved.
    assert _FakeMessageBox.warnings
    assert "السجل التجاري" in _FakeMessageBox.warnings[0][1]
    assert service.save_calls == []


def test_duplicate_vat_number_blocks_save(qt_app, monkeypatch):
    import app.ui.screens.companies_screen as mod

    service = _FakeUiService(duplicate_field="vat_number")
    screen = _make_screen(service)
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    screen.inputs["name_ar"].setText("شركة")
    screen.inputs["commercial_registration"].setText("CR-1")
    screen.inputs["vat_number"].setText("VAT-DUP")

    screen.save_record()

    assert _FakeMessageBox.warnings
    assert "الرقم الضريبي" in _FakeMessageBox.warnings[0][1]
    assert service.save_calls == []


def test_unique_values_are_saved(qt_app, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs
    import app.ui.screens.companies_screen as mod

    service = _FakeUiService(duplicate_field=None)
    screen = _make_screen(service)
    # Both the subclass and the base use QMessageBox; silence both.
    monkeypatch.setattr(mod, "QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset()

    screen.mode = "new"
    screen.inputs["name_ar"].setText("شركة النور")
    screen.inputs["commercial_registration"].setText("CR-100")
    screen.inputs["vat_number"].setText("VAT-100")

    screen.save_record()

    # No duplicate: the base save ran and persisted a new record (record_id None).
    assert len(service.save_calls) == 1
    payload, record_id = service.save_calls[0]
    assert record_id is None
    assert payload.get("name_ar") == "شركة النور"
    assert payload.get("commercial_registration") == "CR-100"
    assert payload.get("vat_number") == "VAT-100"
