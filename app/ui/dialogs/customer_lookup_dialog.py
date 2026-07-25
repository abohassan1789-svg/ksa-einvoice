"""Reusable customer selection popup (اختيار العميل).

Used by the Daily Follow-up screen (bound to F5) to quickly pick an existing
customer instead of typing customer data. All filtering/search is delegated to
:data:`ReviewDataService.search_customers` (database layer) - the dialog itself
is pure UI and never issues SQL.

The dialog is reusable: callers create it, ``exec()`` it, then read
``selected_customer`` (the full customer dict) or ``selected_customer_id``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.review_data_service import ReviewDataService, TABLE_SPECS
from app.ui.common.theme import GREEN, _button_style

_CUSTOMER_LOOKUP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("phone_number", next(field.label for field in TABLE_SPECS["customers"].fields if field.name == "phone_number")),
    ("customer_name", next(field.label for field in TABLE_SPECS["customers"].fields if field.name == "customer_name")),
)


class CustomerLookupDialog(QDialog):
    """Searchable, RTL customer picker. Select by double-click or Enter; Esc closes."""

    def __init__(self, service: ReviewDataService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_customer: dict[str, Any] | None = None
        self.selected_customer_id: Any | None = None
        self._columns_sized = False
        self._row_records: list[dict[str, Any]] = []

        self.setWindowTitle("اختيار العميل / Select Customer")
        self.resize(900, 540)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumSize(560, 360)

        # Debounce search while typing (200ms) so a fast keystroke stream
        # only triggers one server lookup once typing pauses.
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(200)
        self.search_timer.timeout.connect(self.refresh_table)

        self._build_ui()
        # Load only the first page; searching still queries the full database.
        self.refresh_table()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.search_text = QLineEdit()
        self.search_text.setPlaceholderText("ابحث بالاسم أو رقم الهاتف")
        self.search_text.setFixedHeight(38)
        self.search_text.setLayoutDirection(Qt.LeftToRight)
        self.search_text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search_text.setStyleSheet(
            "QLineEdit { background:#FFFFFF; border:1px solid #CBD5E1; border-radius:7px; padding:6px 10px; }"
            f"QLineEdit:focus {{ border:1px solid {GREEN}; }}"
        )
        root.addWidget(self.search_text)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_CUSTOMER_LOOKUP_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _col, label in _CUSTOMER_LOOKUP_COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultSectionSize(180)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; "
            "border:1px solid #E2E8F0; gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; "
            "border:none; padding:9px 8px; }}"
            "QTableWidget::item:selected { background:#DDF3E6; color:#111827; }"
        )
        self.table.cellDoubleClicked.connect(self.accept_selected_row)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#64748B; font-weight:700;")
        self.select_button = QPushButton("اختيار / Select")
        self.select_button.setFixedHeight(36)
        self.select_button.setStyleSheet(_button_style(GREEN, "#166534"))
        self.select_button.clicked.connect(self.accept_selected)
        close_button = QPushButton("إغلاق")
        close_button.setFixedHeight(36)
        close_button.setStyleSheet(_button_style("#374151", "#1F2937"))
        close_button.clicked.connect(self.reject)
        bottom.addWidget(close_button)
        bottom.addWidget(self.select_button)
        bottom.addStretch(1)
        bottom.addWidget(self.count_label)
        root.addLayout(bottom)

        self.search_text.textChanged.connect(self.search_timer.start)
        self.search_text.returnPressed.connect(self.accept_selected)

    # -- data ---------------------------------------------------------------

    def refresh_table(self) -> None:
        keyword = self.search_text.text()
        try:
            rows = self.service.search_customers(keyword, limit=100)
        except Exception as exc:
            QMessageBox.critical(self, "فشل البحث عن العملاء", str(exc))
            rows = []
        self._row_records = [dict(row) for row in rows]
        columns = [col for col, _label in _CUSTOMER_LOOKUP_COLUMNS]
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(self._row_records))
            for row_index, record in enumerate(self._row_records):
                customer_id = record.get("customer_id")
                for col_index, column in enumerate(columns):
                    value = record.get(column)
                    item = QTableWidgetItem("" if value is None else str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setData(Qt.UserRole, customer_id)
                    if column == "customer_id":
                        item.setBackground(QColor("#ECFDF3"))
                    self.table.setItem(row_index, col_index, item)
        finally:
            self.table.setUpdatesEnabled(True)
        if not self._columns_sized and rows:
            self.table.resizeColumnsToContents()
            for col in range(self.table.columnCount()):
                self.table.setColumnWidth(col, min(self.table.columnWidth(col) + 16, 320))
            self._columns_sized = True
        if rows:
            self.table.selectRow(0)
        self.count_label.setText(
            "لا يوجد عملاء" if not rows else f"عدد العملاء المعروضين: {len(rows)}"
        )

    # -- selection ----------------------------------------------------------

    def accept_selected_row(self, row: int, _column: int = 0) -> None:
        self.table.selectRow(row)
        self.accept_selected()

    def accept_selected(self, *_args) -> None:
        row = self.table.currentRow()
        if row < 0:
            if self.table.rowCount() == 0:
                return
            row = 0
        item = self.table.item(row, 0)
        if item is None:
            return
        customer_id = item.data(Qt.UserRole)
        if customer_id is None:
            return
        self.selected_customer_id = customer_id
        self.selected_customer = dict(self._row_records[row]) if row < len(self._row_records) else {}
        self.selected_customer["customer_id"] = customer_id
        self.accept()

    # -- keyboard -----------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept_selected()
            return
        super().keyPressEvent(event)
