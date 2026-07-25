"""Lookup dialogs for the experimental Cashier/POS screen.

* :class:`CashierDraftDialog` — lists **DRAFT** Cashier invoices only, so the
  user can reopen one for editing. Issued / cancelled invoices are never listed
  here (they must not be reopened for editing).

Pure UI: all data access is delegated to :class:`CashierService`. The seller
company and customer pickers reuse the shared :class:`EntityPickerDialog` from
``app.ui.dialogs.saudi_invoice_dialogs`` (searchable, scalable — filters the
whole table, not just a preloaded page).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

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

from app.models.cashier_invoice import STATUS_DRAFT, STATUS_ISSUED, STATUS_LABELS_AR
from app.ui.common.saudi_invoice_style import SI_GREEN, style_button

_SEARCH_QSS = (
    "QLineEdit { background:#FFFFFF; border:1px solid #D7DEE7; border-radius:8px; "
    "padding:7px 12px; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:13px; }"
    f"QLineEdit:focus {{ border:1px solid {SI_GREEN}; }}"
    "QComboBox { background:#FFFFFF; border:1px solid #D7DEE7; border-radius:8px; "
    "padding:6px 10px; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:13px; min-height:22px; }"
)

_TABLE_QSS = (
    "QTableWidget { background:#FFFFFF; border:1px solid #D7DEE7; border-radius:8px; "
    "gridline-color:#EEF1F4; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:13px; }"
    f"QHeaderView::section {{ background:{SI_GREEN}; color:#FFFFFF; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; "
    "font-weight:800; border:none; padding:8px; }}"
    "QTableWidget::item { padding:6px; color:#111827; }"
    "QTableWidget::item:selected { background:#E7F6EE; color:#0B3B23; }"
)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


class CashierDraftDialog(QDialog):
    """Pick one DRAFT Cashier invoice to reopen for editing."""

    _COLUMNS = (
        ("invoice_number", "رقم الفاتورة"),
        ("invoice_date", "التاريخ"),
        ("company_name", "الشركة"),
        ("customer_name", "العميل"),
        ("grand_total", "الإجمالي شامل الضريبة"),
        ("created_at", "أُنشئت في"),
    )

    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_id: int | None = None
        self._rows: list[dict[str, Any]] = []

        self.setWindowTitle("فتح مسودة كاشير")
        self.resize(940, 560)
        self.setLayoutDirection(Qt.RightToLeft)

        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        heading = QLabel("مسودات الكاشير")
        heading.setStyleSheet(
            f"color:{SI_GREEN}; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:16px; font-weight:800;"
        )
        root.addWidget(heading)

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

    def _reload(self) -> None:
        try:
            self._rows = self.service.list_cashier_drafts(300)
        except Exception as exc:  # noqa: BLE001 - surface to user, never crash
            QMessageBox.critical(self, "فشل التحميل", str(exc))
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
                    if key == "created_at" and value is not None:
                        text = value.strftime("%Y-%m-%d %H:%M")
                    elif key == "invoice_date" and value is not None:
                        text = value.strftime("%Y-%m-%d")
                    else:
                        text = _fmt(value)
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignCenter)
                    if c == 0:
                        item.setData(Qt.UserRole, record.get("id"))
                    self.table.setItem(r, c, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self.count_label.setText(f"عدد المسودات: {len(self._rows)}")

    def _accept(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "تنبيه", "اختر مسودة أولًا.")
            return
        first = self.table.item(row, 0)
        invoice_id = first.data(Qt.UserRole) if first is not None else None
        if invoice_id is None:
            return
        self.selected_id = int(invoice_id)
        self.accept()


class CashierInvoiceSearchDialog(QDialog):
    """F1 lookup over ALL cashier invoices (any status).

    Search by invoice number (text) plus company and customer **drop-downs**
    populated from the companies / customers tables, and an optional status
    filter. Selecting an invoice returns its id so the screen can open it (a
    DRAFT opens editable; an ISSUED invoice opens read-only, with the option to
    cancel its approval).
    """

    _COLUMNS = (
        ("invoice_number", "رقم الفاتورة"),
        ("invoice_datetime", "التاريخ والوقت"),
        ("company_name", "الشركة"),
        ("customer_name", "العميل"),
        ("invoice_status", "الحالة"),
        ("grand_total", "الإجمالي شامل الضريبة"),
    )

    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_id: int | None = None
        self._rows: list[dict[str, Any]] = []

        self.setWindowTitle("بحث عن فاتورة كاشير (F1)")
        self.resize(1040, 620)
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

        # Filter row: invoice-number text + company/customer/status drop-downs.
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("رقم الفاتورة…")
        self.search.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search.setStyleSheet(_SEARCH_QSS)
        self.search.textChanged.connect(self._timer.start)
        filters.addWidget(self.search, 2)

        self.company_combo = QComboBox()
        self.company_combo.setStyleSheet(_SEARCH_QSS)
        self.company_combo.addItem("كل الشركات", None)
        self._fill_company_combo()
        self.company_combo.currentIndexChanged.connect(self._run_search)
        filters.addWidget(self.company_combo, 2)

        self.customer_combo = QComboBox()
        self.customer_combo.setStyleSheet(_SEARCH_QSS)
        self.customer_combo.addItem("كل العملاء", None)
        self._fill_customer_combo()
        self.customer_combo.currentIndexChanged.connect(self._run_search)
        filters.addWidget(self.customer_combo, 2)

        self.status_combo = QComboBox()
        self.status_combo.setStyleSheet(_SEARCH_QSS)
        self.status_combo.addItem("كل الحالات", None)
        self.status_combo.addItem(STATUS_LABELS_AR[STATUS_DRAFT], STATUS_DRAFT)
        self.status_combo.addItem(STATUS_LABELS_AR[STATUS_ISSUED], STATUS_ISSUED)
        self.status_combo.currentIndexChanged.connect(self._run_search)
        filters.addWidget(self.status_combo, 1)
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

    def _fill_company_combo(self) -> None:
        try:
            for company in self.service.list_cashier_companies(1000):
                self.company_combo.addItem(
                    company.get("name_ar") or str(company.get("id")), company.get("id")
                )
        except Exception:  # noqa: BLE001 - a load failure just leaves "كل الشركات"
            pass

    def _fill_customer_combo(self) -> None:
        try:
            for customer in self.service.list_cashier_customers(1000):
                self.customer_combo.addItem(
                    (customer.get("customer_name") or "")
                    + f"  ({customer.get('customer_id')})",
                    customer.get("customer_id"),
                )
        except Exception:  # noqa: BLE001
            pass

    def _run_search(self) -> None:
        try:
            self._rows = self.service.search_cashier_invoices(
                self.search.text(),
                self.company_combo.currentData(),
                self.customer_combo.currentData(),
                self.status_combo.currentData(),
                300,
            )
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
                    if key == "invoice_status":
                        text = STATUS_LABELS_AR.get(value, value or "")
                    elif key == "invoice_datetime" and value is not None:
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
            QMessageBox.information(self, "تنبيه", "اختر فاتورة أولًا.")
            return
        first = self.table.item(row, 0)
        invoice_id = first.data(Qt.UserRole) if first is not None else None
        if invoice_id is None:
            return
        self.selected_id = int(invoice_id)
        self.accept()


__all__ = ["CashierDraftDialog", "CashierInvoiceSearchDialog"]
