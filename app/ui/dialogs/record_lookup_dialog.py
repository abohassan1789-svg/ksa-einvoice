"""F1 record lookup dialog for the review screens.

Pure UI: it asks the service for rows and renders a searchable table. All
filtering/projection is delegated to the pure helpers in
``app.ui.common.lookup_filters`` and all data access stays in the service.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
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

from app.services.review_data_service import ReviewDataService, TableSpec
from app.ui.common.lookup_filters import filter_lookup_rows, project_visible_columns
from app.ui.common.theme import EXTRA_COLUMN_LABELS, GREEN, _button_style


class RecordLookupDialog(QDialog):
    """F1 record lookup with visible-column controls (customers / employees)."""

    def __init__(self, service: ReviewDataService, spec: TableSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.spec = spec
        self.selected_id: Any | None = None
        self.rows: list[dict[str, Any]] = []
        if spec.lookup_columns:
            self.lookup_columns = list(spec.lookup_columns)
        else:
            self.lookup_columns = [field.name for field in spec.fields if not field.hidden_on_form]
        self._columns_sized = False
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(200)
        self.search_timer.timeout.connect(self.refresh_table)
        self.setWindowTitle(spec.title.replace("إدارة", "بحث"))
        self.resize(980, 620)
        self.setLayoutDirection(Qt.RightToLeft)
        self._build_ui()
        self._load_rows()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        search_row = QHBoxLayout()
        self.search_text = QLineEdit()
        self.search_text.setPlaceholderText("بحث بأي عمود ظاهر")
        self.search_text.setFixedHeight(38)
        self.search_text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search_text.setStyleSheet(
            "QLineEdit { background:#FFFFFF; border:1px solid #CBD5E1; border-radius:7px; padding:6px 10px; }"
            f"QLineEdit:focus {{ border:1px solid {GREEN}; }}"
        )
        search_row.addWidget(self.search_text, 1)
        root.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultSectionSize(130)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; border:1px solid #E2E8F0; "
            "gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:9px 8px; }}"
            "QTableWidget::item:selected { background:#DDF3E6; color:#111827; }"
        )
        self.table.itemDoubleClicked.connect(self.accept_selected)
        columns = self.visible_columns()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([self._label_for_column(column) for column in columns])
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#64748B; font-weight:700;")
        close_button = QPushButton("إغلاق")
        close_button.setFixedHeight(36)
        close_button.setStyleSheet(_button_style("#374151", "#1F2937"))
        close_button.clicked.connect(self.reject)
        bottom.addWidget(close_button)
        bottom.addStretch(1)
        bottom.addWidget(self.count_label)
        root.addLayout(bottom)

        self.search_text.textChanged.connect(self.search_timer.start)

    def _load_rows(self) -> None:
        try:
            self.rows = self.service.list_records_with_columns(
                self.spec,
                self.lookup_columns,
                "",
                5000,
            )
        except Exception as exc:
            QMessageBox.critical(self, "فشل تحميل البيانات", str(exc))
            self.rows = []
        self.refresh_table()

    def visible_columns(self) -> list[str]:
        return self.lookup_columns or [self.spec.primary_key]

    def refresh_table(self) -> None:
        visible_columns = self.visible_columns()
        rows = filter_lookup_rows(self.rows, self.search_text.text(), visible_columns)
        pk = self.spec.primary_key
        pk_background = QColor("#ECFDF3")
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(rows))
            for row_index, record in enumerate(rows):
                pk_value = record.get(pk)
                values = project_visible_columns(record, visible_columns)
                for col_index, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setData(Qt.UserRole, pk_value)
                    if visible_columns[col_index] == pk:
                        item.setBackground(pk_background)
                    self.table.setItem(row_index, col_index, item)
        finally:
            self.table.setUpdatesEnabled(True)
        if not self._columns_sized and rows:
            # Size once to content (capped) on first load; afterwards the user
            # controls each column width by dragging the header borders.
            self.table.resizeColumnsToContents()
            for col in range(self.table.columnCount()):
                self.table.setColumnWidth(col, min(self.table.columnWidth(col) + 16, 320))
            self._columns_sized = True
        self.count_label.setText(f"عدد السجلات المعروضة: {len(rows)}")

    def accept_selected(self, *_args) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        current_item = self.table.currentItem() or self.table.item(row, 0)
        if current_item is not None:
            self.selected_id = current_item.data(Qt.UserRole)
        if self.selected_id is None:
            id_value = self.table.item(row, 0)
            self.selected_id = id_value.text() if id_value is not None else None
        if self.selected_id is not None:
            self.accept()

    def _label_for_column(self, column: str) -> str:
        for field in self.spec.fields:
            if field.name == column:
                return field.label
        return EXTRA_COLUMN_LABELS.get(column, column)
