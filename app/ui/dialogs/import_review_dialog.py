"""Review window shown before saving an Excel import.

Displays every row read from the workbook with a per-row status (valid / error)
and clear Arabic error messages, then lets the user save only the valid rows or
cancel. Nothing is written to the database until the user confirms.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.excel_io_service import ExcelIoSpec, ExcelTemplateService, ImportRow
from app.ui.common.theme import GREEN, GREEN_DARK, TEXT, _button_style


class ImportReviewDialog(QDialog):
    """Modal review of imported rows with a "save valid rows only" action."""

    def __init__(
        self,
        service: ExcelTemplateService,
        io_spec: ExcelIoSpec,
        rows: list[ImportRow],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.io_spec = io_spec
        self.rows = rows
        self.saved_count = 0
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle(f"مراجعة الاستيراد - {io_spec.sheet_title}")
        self.setMinimumSize(940, 560)
        self.setStyleSheet("QDialog { background:#F4F7FB; }")
        self._build_ui()
        self._populate()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("مراجعة البيانات قبل الحفظ")
        title.setStyleSheet(f"font-size:20px; font-weight:900; color:{TEXT};")
        layout.addWidget(title)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-size:13px; font-weight:800; color:#475569;")
        layout.addWidget(self.summary_label)

        # Columns: # + status + one per template column + error message.
        self.columns = list(self.io_spec.headers)
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.columns) + 3)
        self.table.setHorizontalHeaderLabels(
            ["#", "الحالة", *self.columns, "رسالة الخطأ"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; "
            "border:1px solid #E2E8F0; gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; "
            "border:none; padding:9px 8px; }}"
        )
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_button = QPushButton("حفظ الصفوف الصحيحة")
        self.save_button.setFixedHeight(40)
        self.save_button.setMinimumWidth(200)
        self.save_button.setStyleSheet(_button_style("#1A7A3C", "#166534"))
        self.save_button.clicked.connect(self._save_valid)

        self.cancel_button = QPushButton("إلغاء")
        self.cancel_button.setFixedHeight(40)
        self.cancel_button.setMinimumWidth(120)
        self.cancel_button.setStyleSheet(_button_style("#6B7280", "#4B5563"))
        self.cancel_button.clicked.connect(self.reject)

        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def _populate(self) -> None:
        valid = sum(1 for row in self.rows if row.is_valid)
        invalid = len(self.rows) - valid
        self.summary_label.setText(
            f"إجمالي الصفوف: {len(self.rows)}  |  صحيحة: {valid}  |  بها أخطاء: {invalid}"
        )
        self.save_button.setEnabled(valid > 0)

        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            valid_row = row.is_valid
            status_text = "صحيح" if valid_row else "خطأ"

            number_item = QTableWidgetItem(str(row.row_number))
            number_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(index, 0, number_item)

            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QColor("#FFFFFF"))
            status_item.setBackground(QColor("#1A7A3C") if valid_row else QColor("#DC2626"))
            self.table.setItem(index, 1, status_item)

            for col_index, header in enumerate(self.columns, start=2):
                cell = QTableWidgetItem(row.values.get(header, ""))
                cell.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(index, col_index, cell)

            error_item = QTableWidgetItem(" | ".join(row.errors))
            error_item.setForeground(QColor("#B91C1C"))
            self.table.setItem(index, len(self.columns) + 2, error_item)

        self.table.resizeColumnsToContents()

    # -- Actions -------------------------------------------------------------

    def _save_valid(self) -> None:
        valid_rows = [row for row in self.rows if row.is_valid]
        if not valid_rows:
            QMessageBox.information(self, "لا يوجد", "لا توجد صفوف صحيحة للحفظ.")
            return
        answer = QMessageBox.question(
            self,
            "تأكيد الحفظ",
            f"سيتم حفظ {len(valid_rows)} صف صحيح فقط. المتابعة؟",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            saved, failures = self.service.save_valid_rows(self.io_spec, self.rows)
        except Exception as exc:  # unexpected engine/database failure
            QMessageBox.critical(self, "فشل الحفظ", str(exc))
            return
        self.saved_count = saved
        if failures:
            # Some rows failed at insert time; refresh the grid to show them.
            self._populate()
            QMessageBox.warning(
                self,
                "تم الحفظ جزئيًا",
                f"تم حفظ {saved} صف. فشل حفظ {len(failures)} صف (انظر رسائل الخطأ).",
            )
            return
        QMessageBox.information(self, "تم الحفظ", f"تم حفظ {saved} صف بنجاح.")
        self.accept()
