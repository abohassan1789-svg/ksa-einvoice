"""Lookup dialogs for the Saudi sales-invoice screen.

* :class:`EntityPickerDialog` — a generic, scalable search picker used for the
  seller company, the customer and the product. It calls a service *search*
  callable (``search_fn(keyword, limit)``) so typing filters the **whole**
  database, never just the pre-loaded first 100 rows.
* :class:`SaudiInvoiceSearchDialog` — the F1 invoice lookup over the new Saudi
  invoice tables only, with status filters (مسودة / معتمدة).

Pure UI: all data access is delegated to :class:`SaudiSalesInvoiceService`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from app.models.sales_invoice import STATUS_APPROVED, STATUS_DRAFT, STATUS_LABELS_AR
from app.ui.common.saudi_invoice_style import SI_GREEN, style_button

_TABLE_QSS = (
    "QTableWidget { background:#FFFFFF; border:1px solid #D7DEE7; border-radius:8px; "
    "gridline-color:#EEF1F4; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:13px; }"
    f"QHeaderView::section {{ background:{SI_GREEN}; color:#FFFFFF; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; "
    "font-weight:800; border:none; padding:8px; }}"
    "QTableWidget::item { padding:6px; color:#111827; }"
    "QTableWidget::item:selected { background:#E7F6EE; color:#0B3B23; }"
)
_SEARCH_QSS = (
    "QLineEdit { background:#FFFFFF; border:1px solid #D7DEE7; border-radius:8px; "
    "padding:7px 12px; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:13px; }"
    f"QLineEdit:focus {{ border:1px solid {SI_GREEN}; }}"
)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


class EntityPickerDialog(QDialog):
    """Searchable table picker returning the selected row dict(s).

    ``columns`` is a sequence of ``(key, header)`` pairs; ``search_fn`` returns a
    list of row dicts for a keyword. The chosen row is exposed as ``self.selected``.
    With ``multi_select=True`` the table accepts Ctrl/Shift ranges and every chosen
    row is exposed, in table order, as ``self.selected_rows`` (``selected`` stays
    the first one so single-pick callers keep working).
    """

    def __init__(
        self,
        title: str,
        columns: Sequence[tuple[str, str]],
        search_fn: Callable[[str, int], list[dict[str, Any]]],
        id_key: str,
        parent: QWidget | None = None,
        limit: int = 100,
        multi_select: bool = False,
    ) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._search_fn = search_fn
        self._id_key = id_key
        self._limit = limit
        self._multi_select = multi_select
        self.selected: dict[str, Any] | None = None
        self.selected_rows: list[dict[str, Any]] = []
        self._rows: list[dict[str, Any]] = []

        self.setWindowTitle(title)
        self.resize(860, 560)
        self.setLayoutDirection(Qt.RightToLeft)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._run_search)

        self._build_ui(title)
        self._run_search()

    def _build_ui(self, title: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet(
            f"color:{SI_GREEN}; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:16px; font-weight:800;"
        )
        root.addWidget(heading)

        self.search = QLineEdit()
        self.search.setPlaceholderText("ابحث بالاسم أو الكود أو الرقم الضريبي…")
        self.search.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search.setStyleSheet(_SEARCH_QSS)
        self.search.textChanged.connect(self._timer.start)
        root.addWidget(self.search)

        if self._multi_select:
            hint = QLabel("يمكنك اختيار أكثر من صنف: Ctrl + نقر للتحديد المتفرق، Shift + نقر لتحديد مجموعة.")
            hint.setStyleSheet(
                "color:#64748B; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:12px; font-weight:700;"
            )
            root.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self._columns))
        self.table.setHorizontalHeaderLabels([header for _key, header in self._columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.ExtendedSelection if self._multi_select else QAbstractItemView.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet(_TABLE_QSS)
        self.table.itemDoubleClicked.connect(lambda *_a: self._accept())
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#64748B; font-weight:700;")
        select_button = QPushButton("اختيار")
        select_button.setFixedHeight(36)
        select_button.setMinimumWidth(120)
        style_button(select_button, "green")
        select_button.clicked.connect(self._accept)
        cancel_button = QPushButton("إلغاء")
        cancel_button.setFixedHeight(36)
        cancel_button.setMinimumWidth(110)
        style_button(cancel_button, "white")
        cancel_button.clicked.connect(self.reject)
        bottom.addWidget(select_button)
        bottom.addWidget(cancel_button)
        bottom.addStretch(1)
        bottom.addWidget(self.count_label)
        root.addLayout(bottom)

    def _run_search(self) -> None:
        try:
            self._rows = self._search_fn(self.search.text(), self._limit)
        except Exception as exc:  # noqa: BLE001 - surface to user, never crash
            QMessageBox.critical(self, "فشل البحث", str(exc))
            self._rows = []
        self._fill()

    def _fill(self) -> None:
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(self._rows))
            for r, record in enumerate(self._rows):
                for c, (key, _header) in enumerate(self._columns):
                    item = QTableWidgetItem(_fmt(record.get(key)))
                    item.setTextAlignment(Qt.AlignCenter)
                    if c == 0:
                        item.setData(Qt.UserRole, record)
                    self.table.setItem(r, c, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self.count_label.setText(f"عدد النتائج: {len(self._rows)}")

    def _selected_row_indexes(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if rows:
            return rows
        current = self.table.currentRow()
        return [current] if current >= 0 else []

    def _accept(self) -> None:
        records = []
        for row in self._selected_row_indexes():
            first = self.table.item(row, 0)
            record = first.data(Qt.UserRole) if first is not None else None
            if record is not None:
                records.append(record)
        if not records:
            return
        self.selected_rows = records
        self.selected = records[0]
        self.accept()


class SaudiInvoiceSearchDialog(QDialog):
    """F1 lookup over the Saudi invoice tables with status filters."""

    _COLUMNS = (
        ("invoice_number", "رقم الفاتورة"),
        ("seller_name_ar_snapshot", "البائع"),
        ("customer_name_snapshot", "العميل"),
        ("issue_datetime", "التاريخ والوقت"),
        ("document_status", "الحالة"),
        ("total_including_vat", "الإجمالي شامل الضريبة"),
    )

    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_id: int | None = None
        self._rows: list[dict[str, Any]] = []
        self._status: str | None = None

        self.setWindowTitle("بحث عن فاتورة مبيعات سعودية")
        self.resize(1000, 600)
        self.setLayoutDirection(Qt.RightToLeft)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._run_search)

        self._build_ui()
        self._run_search()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        heading = QLabel("بحث عن فاتورة")
        heading.setStyleSheet(
            f"color:{SI_GREEN}; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:16px; font-weight:800;"
        )
        root.addWidget(heading)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("رقم الفاتورة / البائع / العميل…")
        self.search.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search.setStyleSheet(_SEARCH_QSS)
        self.search.textChanged.connect(self._timer.start)
        filters.addWidget(self.search, 1)

        self.status_combo = QComboBox()
        self.status_combo.addItem("كل الحالات", None)
        self.status_combo.addItem(STATUS_LABELS_AR[STATUS_DRAFT], STATUS_DRAFT)
        self.status_combo.addItem(STATUS_LABELS_AR[STATUS_APPROVED], STATUS_APPROVED)
        self.status_combo.setFixedHeight(34)
        self.status_combo.setMinimumWidth(150)
        self.status_combo.currentIndexChanged.connect(self._run_search)
        filters.addWidget(self.status_combo)
        root.addLayout(filters)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels([h for _k, h in self._COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet(_TABLE_QSS)
        self.table.itemDoubleClicked.connect(lambda *_a: self._accept())
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#64748B; font-weight:700;")
        open_button = QPushButton("فتح")
        open_button.setFixedHeight(36)
        open_button.setMinimumWidth(120)
        style_button(open_button, "green")
        open_button.clicked.connect(self._accept)
        cancel_button = QPushButton("إلغاء")
        cancel_button.setFixedHeight(36)
        cancel_button.setMinimumWidth(110)
        style_button(cancel_button, "white")
        cancel_button.clicked.connect(self.reject)
        bottom.addWidget(open_button)
        bottom.addWidget(cancel_button)
        bottom.addStretch(1)
        bottom.addWidget(self.count_label)
        root.addLayout(bottom)

    def _run_search(self) -> None:
        status = self.status_combo.currentData()
        try:
            self._rows = self.service.search_invoices(self.search.text(), status, 300)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "فشل البحث", str(exc))
            self._rows = []
        self._fill()

    def _fill(self) -> None:
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(self._rows))
            for r, record in enumerate(self._rows):
                for c, (key, _header) in enumerate(self._COLUMNS):
                    value = record.get(key)
                    if key == "document_status":
                        text = STATUS_LABELS_AR.get(value, value or "")
                    elif key == "issue_datetime" and value is not None:
                        text = value.strftime("%Y-%m-%d %H:%M")
                    else:
                        text = _fmt(value)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignCenter)
                    if c == 0:
                        item.setData(Qt.UserRole, record.get("id"))
                    self.table.setItem(r, c, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self.count_label.setText(f"عدد الفواتير: {len(self._rows)}")

    def _accept(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        first = self.table.item(row, 0)
        invoice_id = first.data(Qt.UserRole) if first is not None else None
        if invoice_id is None:
            return
        self.selected_id = int(invoice_id)
        self.accept()


__all__ = ["EntityPickerDialog", "SaudiInvoiceSearchDialog"]
