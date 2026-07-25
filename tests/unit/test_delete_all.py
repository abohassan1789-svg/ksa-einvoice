"""Tests for the per-screen "Delete All" (حذف الكل) feature.

Two layers are covered:

* Service (`ReviewDataService.delete_all_records`): deletes only the current
  screen's own table, refuses when linked child rows exist, and reports how
  many rows were removed. No real database is used — a fake connection records
  the SQL and returns canned counts.
* UI (`BaseCrudScreen.delete_all_records`): the confirmation dialog, the
  admin/superuser gate, cancel-does-nothing, confirm-deletes, and the clear
  error shown when the delete is blocked by related records.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.security.session_context import SESSION
from app.services.review_data_service import ReviewDataService, TABLE_SPECS


# --------------------------------------------------------------------------- #
# Fake database connection
# --------------------------------------------------------------------------- #
def _render(query) -> str:
    try:
        return query.as_string(None)
    except Exception:  # pragma: no cover - defensive
        return str(query)


class _Cursor:
    def __init__(self, rows, rowcount=-1):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeConn:
    """Records every executed statement; answers COUNT() with a fixed value."""

    def __init__(self, child_count: int = 0, delete_rowcount: int = 0):
        self.child_count = child_count
        self.delete_rowcount = delete_rowcount
        self.statements: list[str] = []
        self.closed = False

    def execute(self, query, params=None):
        sql = _render(query)
        self.statements.append(sql)
        if sql.strip().upper().startswith("DELETE"):
            return _Cursor([], rowcount=self.delete_rowcount)
        # Any SELECT here is a blocker COUNT(*) AS c.
        return _Cursor([{"c": self.child_count}])

    @property
    def deletes(self) -> list[str]:
        return [s for s in self.statements if s.strip().upper().startswith("DELETE")]


def _service_with(conn: _FakeConn) -> ReviewDataService:
    service = ReviewDataService()
    service._connection = conn
    return service


# --------------------------------------------------------------------------- #
# Service layer
# --------------------------------------------------------------------------- #
def test_delete_all_removes_only_the_current_screen_table():
    # places has no child relationships, so it deletes freely.
    conn = _FakeConn(delete_rowcount=4)
    service = _service_with(conn)

    deleted = service.delete_all_records(TABLE_SPECS["places"])

    assert deleted == 4
    assert conn.deletes == ['DELETE FROM "places"']
    # It must never touch any other screen's table.
    assert not any("daily_followups" in s for s in conn.statements)
    assert not any("customers" in s for s in conn.statements)


def test_delete_all_blocked_when_related_records_exist():
    # customers is referenced by daily_followups: 5 linked rows must block it.
    conn = _FakeConn(child_count=5)
    service = _service_with(conn)

    with pytest.raises(ValueError) as exc:
        service.delete_all_records(TABLE_SPECS["customers"])

    message = str(exc.value)
    assert "5" in message
    assert "تعاملات يومية" in message  # clear Arabic reason
    # Nothing was deleted.
    assert conn.deletes == []


def test_delete_all_allowed_when_no_related_records():
    conn = _FakeConn(child_count=0, delete_rowcount=7)
    service = _service_with(conn)

    deleted = service.delete_all_records(TABLE_SPECS["customers"])

    assert deleted == 7
    assert conn.deletes == ['DELETE FROM "customers"']


def test_delete_all_blockers_checks_the_child_table():
    conn = _FakeConn(child_count=3)
    service = _service_with(conn)

    blockers = service.delete_all_blockers(TABLE_SPECS["employees"])

    assert blockers and "3" in blockers[0]
    # The blocker check queries the linked daily_followups table.
    assert any("daily_followups" in s and "employee_id" in s for s in conn.statements)


def test_delete_all_leaf_table_has_no_blockers():
    # daily_followups is a leaf (nothing references it) -> deletes with no checks.
    conn = _FakeConn(child_count=99, delete_rowcount=10)
    service = _service_with(conn)

    deleted = service.delete_all_records(TABLE_SPECS["daily_followups"])

    assert deleted == 10
    assert conn.deletes == ['DELETE FROM "daily_followups"']
    # No blocker SELECT ran at all.
    assert len(conn.statements) == 1


# --------------------------------------------------------------------------- #
# UI layer
# --------------------------------------------------------------------------- #
class _FakeUiService:
    """Minimal service for building a PlacesScreen without a database."""

    def __init__(self, deleted: int = 3, error: Exception | None = None):
        self._deleted = deleted
        self._error = error
        self.delete_all_calls: list[str] = []

    def list_records(self, spec, keyword: str = "", limit: int = 500):
        return []

    def delete_all_records(self, spec):
        self.delete_all_calls.append(spec.key)
        if self._error is not None:
            raise self._error
        return self._deleted


class _FakeMessageBox:
    """Replaces QMessageBox inside the screen module during a test."""

    Ok = 0x0400
    Cancel = 0x0040_0000

    warning_return = Ok
    warnings: list[tuple] = []
    infos: list[tuple] = []
    criticals: list[tuple] = []

    @classmethod
    def reset(cls, warning_return):
        cls.warning_return = warning_return
        cls.warnings = []
        cls.infos = []
        cls.criticals = []

    @staticmethod
    def warning(parent, title, text, *args, **kwargs):
        _FakeMessageBox.warnings.append((title, text))
        return _FakeMessageBox.warning_return

    @staticmethod
    def information(parent, title, text, *args, **kwargs):
        _FakeMessageBox.infos.append((title, text))

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):
        _FakeMessageBox.criticals.append((title, text))


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def as_admin():
    SESSION.set_session({"username": "admin", "is_admin": True}, set())
    try:
        yield
    finally:
        SESSION.clear()


@pytest.fixture
def as_non_admin():
    # Has the ordinary delete permission but is NOT an admin.
    SESSION.set_session(
        {"username": "user", "is_admin": False},
        {"crm.places.delete", "crm.places.view", "crm.places.edit"},
    )
    try:
        yield
    finally:
        SESSION.clear()


def _make_places_screen(service):
    from app.ui.screens.places_screen import PlacesScreen

    return PlacesScreen(service)


def test_button_shown_for_admin(qt_app, as_admin):
    screen = _make_places_screen(_FakeUiService())
    assert hasattr(screen, "delete_all_button")
    assert screen._perm_delete_all is True


def test_button_hidden_for_non_admin(qt_app, as_non_admin):
    screen = _make_places_screen(_FakeUiService())
    assert not hasattr(screen, "delete_all_button")
    assert screen._perm_delete_all is False


def test_cancel_does_not_delete(qt_app, as_admin, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs

    service = _FakeUiService()
    screen = _make_places_screen(service)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset(warning_return=_FakeMessageBox.Cancel)

    screen.delete_all_records()

    # Confirmation was shown, but nothing was deleted and no success message.
    assert _FakeMessageBox.warnings
    assert service.delete_all_calls == []
    assert _FakeMessageBox.infos == []


def test_confirm_deletes_current_screen_only(qt_app, as_admin, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs

    service = _FakeUiService(deleted=6)
    screen = _make_places_screen(service)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset(warning_return=_FakeMessageBox.Ok)

    screen.delete_all_records()

    # Deleted exactly this screen's table, then reported success with the count.
    assert service.delete_all_calls == ["places"]
    assert _FakeMessageBox.infos
    assert "6" in _FakeMessageBox.infos[0][1]


def test_blocked_records_show_clear_error(qt_app, as_admin, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs

    service = _FakeUiService(error=ValueError("يوجد تعاملات يومية مرتبطة بالعميل: 5"))
    screen = _make_places_screen(service)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset(warning_return=_FakeMessageBox.Ok)

    screen.delete_all_records()  # must not raise

    # The delete was attempted, failed, and surfaced a clear error (no success).
    assert service.delete_all_calls == ["places"]
    assert _FakeMessageBox.criticals
    assert "5" in _FakeMessageBox.criticals[0][1]
    assert _FakeMessageBox.infos == []


def test_permission_check_blocks_non_admin(qt_app, as_non_admin, monkeypatch):
    import app.ui.screens.base_crud_screen as bcs

    service = _FakeUiService()
    screen = _make_places_screen(service)
    monkeypatch.setattr(bcs, "QMessageBox", _FakeMessageBox)
    _FakeMessageBox.reset(warning_return=_FakeMessageBox.Ok)

    screen.delete_all_records()

    # Non-admin is refused before any deletion happens.
    assert service.delete_all_calls == []
    assert _FakeMessageBox.warnings
    assert "Admin" in _FakeMessageBox.warnings[0][1]
