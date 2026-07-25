"""Widget tests for the Saudi invoice lookup dialogs (headless / offscreen Qt).

Focus: :class:`EntityPickerDialog` in ``multi_select`` mode — the item lookup on
the sales-invoice screen relies on it returning *every* highlighted row.
"""

from __future__ import annotations

import os
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QAbstractItemView, QApplication

from app.ui.dialogs.saudi_invoice_dialogs import EntityPickerDialog

_COLUMNS = [("item_code", "رقم الصنف"), ("item_name", "الاسم"), ("price", "السعر")]
_PRODUCTS = [
    {"id": 1, "item_code": 1001, "item_name": "صنف أ", "price": Decimal("50")},
    {"id": 2, "item_code": 1002, "item_name": "صنف ب", "price": Decimal("30")},
    {"id": 3, "item_code": 1003, "item_name": "صنف ج", "price": Decimal("20")},
]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _picker(multi_select: bool) -> EntityPickerDialog:
    return EntityPickerDialog(
        "بحث عن الصنف", _COLUMNS, lambda kw, limit: list(_PRODUCTS), "id",
        multi_select=multi_select,
    )


def test_multi_select_dialog_allows_extended_selection(qapp):
    dialog = _picker(True)
    assert dialog.table.selectionMode() == QAbstractItemView.ExtendedSelection


def test_single_select_dialog_stays_single(qapp):
    dialog = _picker(False)
    assert dialog.table.selectionMode() == QAbstractItemView.SingleSelection


def _select_rows(dialog: EntityPickerDialog, *rows: int) -> None:
    # selectRow() is a no-op on a view that was never shown (Qt resolves the
    # selection command from the mouse event), so drive the model directly.
    model = dialog.table.selectionModel()
    for row in rows:
        model.select(
            dialog.table.model().index(row, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )


def test_multi_select_returns_every_highlighted_row_in_table_order(qapp):
    dialog = _picker(True)
    _select_rows(dialog, 2, 0)  # row 0 highlighted last, but must come back first
    dialog._accept()
    assert [r["id"] for r in dialog.selected_rows] == [1, 3]
    assert dialog.selected["id"] == 1  # single-pick callers keep working


def test_accept_with_no_selection_is_a_no_op(qapp):
    dialog = _picker(True)
    dialog.table.clearSelection()
    dialog.table.setCurrentCell(-1, -1)
    dialog._accept()
    assert dialog.selected_rows == []
    assert dialog.selected is None
