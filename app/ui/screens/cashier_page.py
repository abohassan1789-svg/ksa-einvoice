"""Experimental Cashier / POS screen (الكاشير) — Design A (two-pane).

An Arabic-first (RTL) point-of-sale screen on the two ``cashier_invoice*`` tables.
Layout: a scrollable product-card grid on the right, and the invoice (details
table + totals + header) on the left. It reuses the existing master data
(``products``, ``customers``, ``companies``) through :class:`CashierService`.

Scope (this phase): create/open/save drafts, add/edit/remove lines, recalculate
totals, and locally *issue* (complete) an invoice. It deliberately has **no**
thermal printing, print preview, PDF, QR, ZATCA XML/signing/reporting, accounting
journals, receipt vouchers, customer-balance updates or inventory movements, and
never posts to the ``sales_invoice*`` tables. All money maths lives in the
service (Decimal); the screen never computes or trusts totals on its own.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.cashier_invoice import STATUS_DRAFT, STATUS_ISSUED, STATUS_LABELS_AR
from app.security.session_context import SESSION
from app.services.cashier_service import CashierError, CashierService
from app.services.cashier_print_service import (
    CashierPrintError,
    CashierPrintService,
    ZatcaPrerequisitesMissing,
    check_zatca_prerequisites,
)
from app.ui.common.saudi_invoice_style import SI_GREEN, si_icon, si_pixmap, style_button
from app.ui.dialogs.cashier_dialogs import CashierDraftDialog, CashierInvoiceSearchDialog
from app.ui.dialogs.saudi_invoice_dialogs import EntityPickerDialog

# Column indices for the details table.
COL_NAME, COL_QTY, COL_PRICE, COL_BEFORE, COL_VAT_RATE, COL_VAT, COL_TOTAL, COL_DELETE = range(8)

CASHIER_QSS = """
QWidget#cashierPage { background: #F5F7FA; }
QFrame#card { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px; }
QFrame#headerCard { background: transparent; border: none; }
QFrame#headerLine { background: #E5E7EB; border: none; min-height: 1px; max-height: 1px; }

QLabel#titleLabel { color: #1F2D3D; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 22px; font-weight: 800; background: transparent; }
QLabel#subtitleLabel { color: #6B7280; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; background: transparent; }
QLabel#metaLabel { color: #6B7280; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; background: transparent; }
QLabel#sectionTitle { color: #00843D; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 15px; font-weight: 800; background: transparent; }
QLabel { color: #1F2937; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 13px; font-weight: 700; background: transparent; }
QLabel#fieldLabel { color: #111827; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; background: transparent; }

QLineEdit {
    min-height: 26px; max-height: 30px;
    border: 1px solid #D7DEE7; border-radius: 6px; background: #FFFFFF;
    padding: 1px 8px; color: #111827; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700;
}
QLineEdit:focus { border: 1px solid #00843D; }
QLineEdit:read-only { background: #F4F6F9; }

QTableWidget {
    border: 1px solid #E5E7EB; border-radius: 8px; background: #FFFFFF;
    gridline-color: #EEF1F4; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 14px; font-weight: 700;
}
QTableWidget::item { padding: 4px; color: #374151; }
QTableWidget::item:selected { background: #E7F6EE; color: #0B3B23; }
QHeaderView::section {
    background: #00843D; color: #FFFFFF; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial';
    font-size: 12px; font-weight: 800; border: none; padding: 6px 4px;
}

QScrollArea#productScroll { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px; }
QWidget#productScrollBody { background: #FFFFFF; }
QLineEdit#productSearch { min-height: 34px; max-height: 38px; font-size: 13px; }

QPushButton#productCard {
    background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px;
    padding: 8px 6px; text-align: center;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; color: #1F2D3D;
}
QPushButton#productCard:hover { border: 1px solid #00843D; background: #F3FbF6; }
QPushButton#productCard:disabled { color: #9CA3AF; background: #F8FAFC; border-color: #EEF1F4; }

QFrame#summaryRow { background: #F9FAFB; border: 1px solid #EEF1F4; border-radius: 8px; }
QFrame#netBox { background: #E8F5EC; border: 1px solid #BFE6CF; border-radius: 10px; }
QLabel#netLabel { color: #00843D; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 13px; font-weight: 800; background: transparent; }
QLineEdit#total_value {
    background: transparent; border: none; color: #00843D;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 20px; font-weight: 800; min-height: 40px; padding: 0 2px;
}
QLineEdit#subtotal_value, QLineEdit#vat_value {
    background: transparent; border: none; color: #111827;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 14px; font-weight: 800; min-height: 28px; padding: 0 4px;
}
QPushButton#rowDelete {
    background: #FFFFFF; color: #DC2626; border: 1px solid #F0999B; border-radius: 6px;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-weight: 800; font-size: 12px;
}
QPushButton#rowDelete:hover { background: #FEECEC; }
QLabel#statusChip {
    border-radius: 12px; padding: 4px 14px; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial';
    font-size: 12px; font-weight: 800;
}
"""

_PRODUCT_COLUMNS = 3
_PRODUCT_PAGE = 60


class CashierPage(QWidget):
    """The Cashier/POS screen. Modes: ``idle`` | ``draft`` | ``issued``."""

    def __init__(self, service: CashierService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service or CashierService(permission_check=SESSION.can)
        self._print_service = CashierPrintService()
        self._draft: dict[str, Any] | None = None      # {id, invoice_number, invoice_status}
        self._lines: list[dict[str, Any]] = []          # working-line dicts
        self._company: dict[str, Any] | None = None
        self._customer: dict[str, Any] | None = None
        self._mode = "idle"
        self._dirty = False
        self._suspend_cell_signal = False

        self.setObjectName("cashierPage")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(CASHIER_QSS)
        self.setMinimumSize(1280, 720)

        self._build_ui()
        self._load_products("")
        self._start_clock()
        self.enter_idle_mode()

    # ======================================================================
    # UI assembly
    # ======================================================================
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(8)
        self._build_header(outer)
        self._build_info_card(outer)
        self._build_body(outer)

    def _build_header(self, outer: QVBoxLayout) -> None:
        container = QFrame()
        container.setObjectName("headerCard")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 0, 4, 0)
        vbox.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(si_pixmap("fa5s.cash-register", color=SI_GREEN, size=26))
        title = QLabel("الكاشير / نقطة البيع")
        title.setObjectName("titleLabel")
        top.addWidget(icon)
        top.addWidget(title)
        self.status_chip = QLabel("")
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setVisible(False)
        top.addWidget(self.status_chip)
        top.addStretch(1)
        meta_box = QWidget()
        meta_v = QVBoxLayout(meta_box)
        meta_v.setContentsMargins(0, 0, 0, 0)
        meta_v.setSpacing(2)
        self.user_label = QLabel("")
        self.clock_label = QLabel("")
        for label in (self.user_label, self.clock_label):
            label.setObjectName("metaLabel")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter | Qt.AlignAbsolute)
            meta_v.addWidget(label)
        top.addWidget(meta_box)
        vbox.addLayout(top)

        self.new_button = self._toolbar_button("فاتورة جديدة", "fa5s.plus", "green")
        self.save_button = self._toolbar_button("حفظ مسودة", "fa5s.save", "green")
        self.open_button = self._toolbar_button("فتح مسودة", "fa5s.folder-open", "blue")
        self.search_button = self._toolbar_button("بحث (F1)", "fa5s.search", "blue")
        self.delete_line_button = self._toolbar_button("حذف السطر", "fa5s.trash", "red")
        self.delete_button = self._toolbar_button("حذف الفاتورة", "fa5s.trash", "red")
        self.delete_all_button = self._toolbar_button("حذف الكل", "fa5s.trash-alt", "red")
        self.cancel_button = self._toolbar_button("إلغاء التعديلات", "fa5s.undo", "white")
        self.issue_button = self._toolbar_button("إتمام الفاتورة", "fa5s.check-double", "green")
        self.unissue_button = self._toolbar_button("إلغاء الاعتماد", "fa5s.unlock", "red")
        self.preview_button = self._toolbar_button("معاينة الطباعة", "fa5s.eye", "blue")
        self.print_button = self._toolbar_button("طباعة الإيصال", "fa5s.print", "green")
        self.close_button = self._toolbar_button("إغلاق", "fa5s.sign-out-alt", "gray")

        # Two compact rows so the actions never crowd or overlap:
        # row 1 = document/editing actions, row 2 = issue/print actions.
        row1 = QHBoxLayout()
        row1.setSpacing(5)
        for button in (self.new_button, self.save_button, self.open_button,
                       self.search_button, self.delete_line_button, self.delete_button,
                       self.delete_all_button, self.cancel_button):
            row1.addWidget(button)
        row1.addStretch(1)
        vbox.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(5)
        for button in (self.issue_button, self.unissue_button, self.preview_button,
                       self.print_button, self.close_button):
            row2.addWidget(button)
        row2.addStretch(1)
        vbox.addLayout(row2)

        line = QFrame()
        line.setObjectName("headerLine")
        vbox.addWidget(line)
        outer.addWidget(container)
        self._wire_actions()

    def _toolbar_button(self, text: str, icon_name: str, kind: str) -> QPushButton:
        button = QPushButton(text)
        # Compact sizing so the (now many) actions never crowd or overlap.
        width = max(74, min(148, 8 * len(text) + 24))
        button.setMinimumWidth(width)
        button.setFixedHeight(30)
        button.setCursor(Qt.PointingHandCursor)
        button.setIconSize(QSize(14, 14))
        button.setIcon(si_icon(icon_name, color="#FFFFFF" if kind != "white" else "#374151"))
        style_button(button, kind)
        # Smaller font + tighter padding than the shared button style.
        button.setStyleSheet(button.styleSheet() + " QPushButton{font-size:11px;padding:2px 8px;}")
        return button

    def _build_info_card(self, outer: QVBoxLayout) -> None:
        card = QFrame()
        card.setObjectName("card")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 10, 16, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)

        # Customer (searchable, nullable in a draft).
        self.customer_value = QLineEdit()
        self.customer_value.setReadOnly(True)
        self.customer_value.setPlaceholderText("— عميل نقدي —")
        self.customer_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        grid.addWidget(self._lookup("اسم العميل", self.customer_value, self._search_customer, "customer"), 0, 0)

        # Invoice number (read-only, DB-generated).
        self.invoice_number_value = QLineEdit()
        self.invoice_number_value.setReadOnly(True)
        self.invoice_number_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        grid.addWidget(self._labeled("رقم الفاتورة", self.invoice_number_value), 0, 1)

        # Company (searchable, required before local issue).
        self.company_value = QLineEdit()
        self.company_value.setReadOnly(True)
        self.company_value.setPlaceholderText("— اختر الشركة —")
        self.company_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        grid.addWidget(self._lookup("اسم الشركة", self.company_value, self._search_company, "company"), 0, 2)

        # Date & time.
        self.datetime_value = QLineEdit()
        self.datetime_value.setReadOnly(True)
        self.datetime_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        grid.addWidget(self._labeled("التاريخ والوقت", self.datetime_value), 0, 3)

        for col in range(4):
            grid.setColumnStretch(col, 1)
            grid.setColumnMinimumWidth(col, 220)
        outer.addWidget(card)

    def _labeled(self, text: str, widget: QWidget) -> QWidget:
        box = QWidget()
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(3)
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        vbox.addWidget(label)
        vbox.addWidget(widget)
        return box

    def _lookup(self, text: str, field: QLineEdit, on_search, kind: str) -> QWidget:
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(field, 1)
        button = QPushButton()
        button.setIcon(si_icon("fa5s.search", color="#FFFFFF"))
        button.setFixedSize(30, 30)
        button.setCursor(Qt.PointingHandCursor)
        style_button(button, "green")
        button.clicked.connect(on_search)
        hbox.addWidget(button)
        if kind == "customer":
            self.customer_search_button = button
        else:
            self.company_search_button = button
        return self._labeled(text, row)

    def _build_body(self, outer: QVBoxLayout) -> None:
        body = QHBoxLayout()
        body.setSpacing(12)
        # RTL: the first widget added sits on the RIGHT -> product panel first.
        body.addWidget(self._build_product_panel(), 0)
        body.addWidget(self._build_invoice_panel(), 1)
        outer.addLayout(body, 1)

    def _build_product_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        panel.setFixedWidth(400)
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(8)

        title_row = QHBoxLayout()
        label = QLabel("الأصناف")
        label.setObjectName("sectionTitle")
        icon = QLabel()
        icon.setPixmap(si_pixmap("fa5s.box-open", color=SI_GREEN, size=16))
        title_row.addWidget(label)
        title_row.addWidget(icon)
        title_row.addStretch(1)
        vbox.addLayout(title_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.product_search = QLineEdit()
        self.product_search.setObjectName("productSearch")
        self.product_search.setPlaceholderText("ابحث بالاسم أو رقم الصنف (باركود لاحقًا)…")
        self.product_search.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._on_product_search)
        self.product_search.textChanged.connect(lambda *_a: self._search_timer.start())
        search_row.addWidget(self.product_search, 1)
        self.refresh_products_button = QPushButton()
        self.refresh_products_button.setIcon(si_icon("fa5s.sync", color="#FFFFFF"))
        self.refresh_products_button.setToolTip("تحديث قائمة الأصناف")
        self.refresh_products_button.setFixedSize(36, 36)
        self.refresh_products_button.setCursor(Qt.PointingHandCursor)
        style_button(self.refresh_products_button, "green")
        self.refresh_products_button.clicked.connect(self._on_product_search)
        search_row.addWidget(self.refresh_products_button)
        vbox.addLayout(search_row)

        self.product_scroll = QScrollArea()
        self.product_scroll.setObjectName("productScroll")
        self.product_scroll.setWidgetResizable(True)
        self.product_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.product_body = QWidget()
        self.product_body.setObjectName("productScrollBody")
        self.product_grid = QGridLayout(self.product_body)
        self.product_grid.setContentsMargins(8, 8, 8, 8)
        self.product_grid.setHorizontalSpacing(8)
        self.product_grid.setVerticalSpacing(8)
        self.product_scroll.setWidget(self.product_body)
        vbox.addWidget(self.product_scroll, 1)

        self.product_hint = QLabel("")
        self.product_hint.setObjectName("metaLabel")
        vbox.addWidget(self.product_hint)
        return panel

    def _build_invoice_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(8)

        title_row = QHBoxLayout()
        label = QLabel("تفاصيل الفاتورة")
        label.setObjectName("sectionTitle")
        icon = QLabel()
        icon.setPixmap(si_pixmap("fa5s.list-ul", color=SI_GREEN, size=16))
        title_row.addWidget(label)
        title_row.addWidget(icon)
        title_row.addStretch(1)
        vbox.addLayout(title_row)

        self.lines_table = QTableWidget()
        self.lines_table.setColumnCount(8)
        self.lines_table.setHorizontalHeaderLabels([
            "اسم الصنف", "الكمية", "السعر", "الإجمالي قبل الضريبة",
            "نسبة الضريبة", "قيمة الضريبة", "الإجمالي بعد الضريبة", "حذف",
        ])
        self.lines_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lines_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lines_table.setAlternatingRowColors(True)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(42)
        self.lines_table.horizontalHeader().setFixedHeight(46)
        header = self.lines_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(COL_DELETE, QHeaderView.Fixed)
        self.lines_table.setColumnWidth(COL_DELETE, 70)
        self.lines_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.lines_table.cellChanged.connect(self._on_cell_changed)
        vbox.addWidget(self.lines_table, 1)

        # Totals section.
        totals = QVBoxLayout()
        totals.setSpacing(6)
        self.subtotal_value = self._summary_row(totals, "الإجمالي قبل الضريبة", "subtotal_value")
        self.vat_value = self._summary_row(totals, "إجمالي ضريبة القيمة المضافة (15%)", "vat_value")
        net_box = QFrame()
        net_box.setObjectName("netBox")
        net_box.setFixedHeight(58)
        net_h = QHBoxLayout(net_box)
        net_h.setContentsMargins(16, 8, 16, 8)
        net_label = QLabel("الإجمالي شامل الضريبة")
        net_label.setObjectName("netLabel")
        self.total_value = QLineEdit("0.00 SAR")
        self.total_value.setObjectName("total_value")
        self.total_value.setReadOnly(True)
        self.total_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        net_h.addWidget(net_label)
        net_h.addWidget(self.total_value, 1)
        totals.addWidget(net_box)
        vbox.addLayout(totals)
        return panel

    def _summary_row(self, parent: QVBoxLayout, text: str, object_name: str) -> QLineEdit:
        frame = QFrame()
        frame.setObjectName("summaryRow")
        frame.setFixedHeight(44)
        hbox = QHBoxLayout(frame)
        hbox.setContentsMargins(14, 4, 14, 4)
        label = QLabel(text)
        value = QLineEdit("0.00")
        value.setObjectName(object_name)
        value.setReadOnly(True)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        hbox.addWidget(label)
        hbox.addWidget(value, 1)
        parent.addWidget(frame)
        return value

    # ======================================================================
    # Wiring / clock
    # ======================================================================
    def _wire_actions(self) -> None:
        self.new_button.clicked.connect(self.on_new)
        self.save_button.clicked.connect(self.on_save)
        self.open_button.clicked.connect(self.on_open)
        self.search_button.clicked.connect(self.on_search_invoices)
        self.delete_line_button.clicked.connect(self.on_delete_selected_line)
        self.delete_button.clicked.connect(self.on_delete)
        self.delete_all_button.clicked.connect(self.on_delete_all)
        self.issue_button.clicked.connect(self.on_issue)
        self.unissue_button.clicked.connect(self.on_unissue)
        self.preview_button.clicked.connect(self.on_preview_receipt)
        self.print_button.clicked.connect(self.on_print_receipt)
        self.cancel_button.clicked.connect(self.on_cancel)
        self.close_button.clicked.connect(self._on_close)
        # F1 opens the all-invoices search dialog.
        self._search_shortcut = QShortcut(QKeySequence(Qt.Key_F1), self)
        self._search_shortcut.activated.connect(self.on_search_invoices)

    def _start_clock(self) -> None:
        user = SESSION.user or {}
        name = user.get("full_name") or user.get("username") or "مستخدم النظام"
        self.user_label.setText(f"المستخدم: {name}")
        self._tick_clock()
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start()

    def _tick_clock(self) -> None:
        now = datetime.datetime.now()
        self.clock_label.setText(f"{now:%Y-%m-%d  %H:%M:%S}")
        # The invoice datetime field tracks "now" only while a draft is open;
        # an issued invoice shows its own stored issue datetime.
        if self._mode != "issued":
            self.datetime_value.setText(f"{now:%Y-%m-%d %H:%M}")

    def _current_user_id(self) -> int | None:
        user = SESSION.user or {}
        try:
            return int(user["id"]) if user.get("id") is not None else None
        except (KeyError, TypeError, ValueError):
            return None

    # ======================================================================
    # Products (right panel)
    # ======================================================================
    def _load_products(self, keyword: str) -> None:
        try:
            products = self.service.search_cashier_products(keyword, _PRODUCT_PAGE)
        except Exception as exc:  # noqa: BLE001 - surface, never crash
            products = []
            self.product_hint.setText(f"تعذّر تحميل الأصناف: {exc}")
        self._render_product_cards(products)

    def _on_product_search(self) -> None:
        self._load_products(self.product_search.text())

    def showEvent(self, event) -> None:  # noqa: ANN001 - Qt show event
        """Reload the product cards each time the screen is shown.

        The main window caches this screen, so a product added in the Products
        screen would otherwise not appear until restart. Reloading on show keeps
        the card grid current (filtered by whatever is in the search box).
        """
        super().showEvent(event)
        if hasattr(self, "product_search"):
            self._load_products(self.product_search.text())

    def _render_product_cards(self, products: list[dict[str, Any]]) -> None:
        # Clear the grid.
        while self.product_grid.count():
            item = self.product_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        enabled = self._mode == "draft"
        cards: list[QPushButton] = []
        for index, product in enumerate(products):
            card = self._make_product_card(product)
            card.setEnabled(enabled)
            row, col = divmod(index, _PRODUCT_COLUMNS)
            self.product_grid.addWidget(card, row, col)
            cards.append(card)
        # Push cards to the top.
        self.product_grid.setRowStretch((len(products) // _PRODUCT_COLUMNS) + 1, 1)
        # Keep only the cards just built: the ones this render replaced are still
        # children until the event loop runs their deleteLater(), so collecting
        # them from the tree would leave dangling pointers behind.
        self._product_cards = cards
        self.product_hint.setText(f"عدد الأصناف المعروضة: {len(products)}")

    def _make_product_card(self, product: dict[str, Any]) -> QPushButton:
        name = product.get("item_name") or ""
        price = product.get("price")
        price_text = f"{Decimal(str(price)):,.2f}" if price is not None else "0.00"
        code = product.get("item_code")
        card = QPushButton(f"{name}\n{price_text} SAR\n#{code}")
        card.setObjectName("productCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setMinimumHeight(72)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.clicked.connect(lambda _c=False, p=product: self._on_product_clicked(p))
        return card

    def _on_product_clicked(self, product: dict[str, Any]) -> None:
        if self._mode != "draft":
            QMessageBox.information(self, "تنبيه", "أنشئ فاتورة جديدة أو افتح مسودة أولًا.")
            return
        try:
            self.service.add_cashier_line(self._lines, product)
        except CashierError as exc:
            QMessageBox.warning(self, "تنبيه", exc.message)
            return
        self._refresh_table()
        self._refresh_totals()
        self._dirty = True

    # ======================================================================
    # Details table (left panel)
    # ======================================================================
    def _refresh_table(self) -> None:
        editable = self._mode == "draft"
        self._suspend_cell_signal = True
        try:
            self.lines_table.setRowCount(0)
            for line in self._lines:
                r = self.lines_table.rowCount()
                self.lines_table.insertRow(r)
                for c in range(8):
                    self.lines_table.setItem(r, c, QTableWidgetItem(""))
                name_item = self.lines_table.item(r, COL_NAME)
                name_item.setText(line["product_name"])
                name_item.setData(Qt.UserRole, line["product_id"])
                cells = {
                    COL_QTY: self._fmt_qty(line["quantity"]),
                    COL_PRICE: f"{line['unit_price']:,.2f}",
                    COL_BEFORE: f"{line['line_subtotal']:,.2f}",
                    COL_VAT_RATE: f"{line['vat_rate']:g}%",
                    COL_VAT: f"{line['vat_amount']:,.2f}",
                    COL_TOTAL: f"{line['line_total']:,.2f}",
                }
                for c, text in cells.items():
                    self.lines_table.item(r, c).setText(text)
                for c in range(7):
                    item = self.lines_table.item(r, c)
                    item.setTextAlignment(Qt.AlignCenter)
                    if c == COL_QTY and editable:
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    else:
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._install_delete_button(r, line["product_id"], editable)
        finally:
            self._suspend_cell_signal = False

    def _install_delete_button(self, row: int, product_id: int, enabled: bool) -> None:
        button = QPushButton("حذف")
        button.setObjectName("rowDelete")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(28)
        button.setEnabled(enabled)
        button.clicked.connect(lambda _c=False, pid=product_id: self._remove_line(pid))
        wrapper = QWidget()
        h = QHBoxLayout(wrapper)
        h.setContentsMargins(6, 3, 6, 3)
        h.addWidget(button)
        self.lines_table.setCellWidget(row, COL_DELETE, wrapper)

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._suspend_cell_signal or col != COL_QTY:
            return
        name_item = self.lines_table.item(row, COL_NAME)
        if name_item is None:
            return
        product_id = name_item.data(Qt.UserRole)
        text = self.lines_table.item(row, col).text()
        try:
            self.service.update_cashier_line_quantity(self._lines, int(product_id), text)
        except CashierError as exc:
            QMessageBox.warning(self, "قيمة غير صالحة", exc.message)
            self._refresh_table()  # revert to last good value
            return
        self._refresh_table()
        self._refresh_totals()
        self._dirty = True

    def _remove_line(self, product_id: int) -> None:
        if self._mode != "draft":
            return
        self._lines = self.service.remove_cashier_line(self._lines, int(product_id))
        self._refresh_table()
        self._refresh_totals()
        self._dirty = True

    def on_delete_selected_line(self) -> None:
        if self._mode != "draft":
            return
        row = self.lines_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "تنبيه", "اختر سطرًا لحذفه.")
            return
        name_item = self.lines_table.item(row, COL_NAME)
        if name_item is None:
            return
        self._remove_line(int(name_item.data(Qt.UserRole)))

    def _refresh_totals(self) -> None:
        totals = self.service.compute_totals(self._lines)
        self.subtotal_value.setText(f"{totals['subtotal']:,.2f}")
        self.vat_value.setText(f"{totals['vat_amount']:,.2f}")
        self.total_value.setText(f"{totals['grand_total']:,.2f} SAR")

    @staticmethod
    def _fmt_qty(value: Decimal) -> str:
        return format(value.normalize(), "f")

    # ======================================================================
    # Customer / company lookups
    # ======================================================================
    def _search_customer(self) -> None:
        if self._mode != "draft":
            return
        dialog = EntityPickerDialog(
            "بحث عن العميل",
            [("customer_id", "الكود"), ("customer_name", "الاسم"),
             ("phone_number", "الهاتف"), ("vat_number", "الرقم الضريبي")],
            self.service.search_cashier_customers, "customer_id", parent=self,
        )
        if dialog.exec() and dialog.selected:
            self._customer = dialog.selected
            self.customer_value.setText(
                (dialog.selected.get("customer_name") or "")
                + f"  ({dialog.selected.get('customer_id')})"
            )
            self._dirty = True

    def _search_company(self) -> None:
        if self._mode != "draft":
            return
        dialog = EntityPickerDialog(
            "بحث عن الشركة",
            [("id", "الكود"), ("name_ar", "الاسم"),
             ("vat_number", "الرقم الضريبي"), ("commercial_registration", "السجل التجاري")],
            self.service.search_cashier_companies, "id", parent=self,
        )
        if dialog.exec() and dialog.selected:
            self._company = dialog.selected
            self.company_value.setText(dialog.selected.get("name_ar") or str(dialog.selected.get("id")))
            self._dirty = True

    # ======================================================================
    # Form collection
    # ======================================================================
    def _collect_form(self) -> dict[str, Any]:
        return {
            "invoice_number": self._draft.get("invoice_number") if self._draft else None,
            "company_id": self._company.get("id") if self._company else None,
            "customer_id": self._customer.get("customer_id") if self._customer else None,
            "notes": None,
            "lines": [
                {"product_id": line["product_id"], "quantity": line["quantity"]}
                for line in self._lines
            ],
        }

    # ======================================================================
    # Modes
    # ======================================================================
    def _apply_mode(self) -> None:
        is_draft = self._mode == "draft"
        is_issued = self._mode == "issued"
        has_draft = self._draft is not None

        self.new_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.search_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self.save_button.setEnabled(is_draft)
        self.issue_button.setEnabled(is_draft)
        self.delete_line_button.setEnabled(is_draft)
        # "إلغاء الاعتماد" is available only when an ISSUED invoice is open.
        self.unissue_button.setEnabled(is_issued and has_draft)
        # Printing/preview act on the persisted invoice, so require a saved id.
        has_saved = has_draft and self._draft.get("id") is not None
        self.preview_button.setEnabled(has_saved)
        self.print_button.setEnabled(has_saved and is_issued)
        # Deleting acts on the stored row: an unsaved new invoice has no id yet,
        # and an issued one is immutable — only a saved draft can go.
        self.delete_button.setEnabled(is_draft and has_saved)
        self.delete_all_button.setEnabled(True)
        self.cancel_button.setEnabled(is_draft or (is_issued and has_draft))
        self.customer_search_button.setEnabled(is_draft)
        self.company_search_button.setEnabled(is_draft)
        self.product_search.setEnabled(is_draft)
        self.lines_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
            if is_draft else QAbstractItemView.NoEditTriggers
        )
        # Re-enable/disable product cards without a reload.
        for card in getattr(self, "_product_cards", []):
            card.setEnabled(is_draft)
        self._apply_status_chip()

    def _apply_status_chip(self) -> None:
        if self._draft is None:
            self.status_chip.setVisible(False)
            return
        status = self._draft.get("invoice_status", STATUS_DRAFT)
        self.status_chip.setText(STATUS_LABELS_AR.get(status, status))
        if status == STATUS_ISSUED:
            fg, bg = "#065F46", "#A7F3D0"
        else:
            fg, bg = "#374151", "#E5E7EB"
        self.status_chip.setStyleSheet(
            f"QLabel#statusChip {{ color:{fg}; background:{bg}; border-radius:12px; "
            "padding:4px 14px; font-family:'Cairo','Segoe UI','Tahoma','Arial'; font-size:12px; font-weight:800; }}"
        )
        self.status_chip.setVisible(True)

    def enter_idle_mode(self) -> None:
        self._mode = "idle"
        self._draft = None
        self._lines = []
        self._company = None
        self._customer = None
        self.invoice_number_value.clear()
        self.customer_value.clear()
        self.company_value.clear()
        self._refresh_table()
        self._refresh_totals()
        self._dirty = False
        self._apply_mode()

    def enter_draft_mode(self) -> None:
        self._mode = "draft"
        self._apply_mode()

    def enter_issued_mode(self) -> None:
        self._mode = "issued"
        self._apply_mode()

    # ======================================================================
    # Load / apply persisted invoices
    # ======================================================================
    def _lines_from_db(self, db_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        working: list[dict[str, Any]] = []
        for line in db_lines:
            working.append({
                "product_id": line["product_id"],
                "product_code": line["product_code_snapshot"],
                "product_name": line["product_name_snapshot"],
                "quantity": Decimal(str(line["quantity"])),
                "unit_price": Decimal(str(line["unit_price"])),
                "vat_rate": Decimal(str(line["vat_rate"])),
                "line_subtotal": Decimal(str(line["line_subtotal"])),
                "vat_amount": Decimal(str(line["vat_amount"])),
                "line_total": Decimal(str(line["line_total"])),
            })
        return working

    def _apply_saved(self, invoice: dict[str, Any]) -> None:
        """After save/issue: refresh id / number / status / lines; keep pickers."""
        header = invoice["header"]
        self._draft = {
            "id": header["id"],
            "invoice_number": header["invoice_number"],
            "invoice_status": header["invoice_status"],
        }
        self._lines = self._lines_from_db(invoice["lines"])
        self.invoice_number_value.setText(header["invoice_number"] or "")
        if header.get("invoice_datetime") is not None:
            self.datetime_value.setText(header["invoice_datetime"].strftime("%Y-%m-%d %H:%M"))
        self._refresh_table()
        self._refresh_totals()

    def _load_full(self, invoice: dict[str, Any]) -> None:
        """Open an existing invoice: repopulate everything from the database."""
        header = invoice["header"]
        self._draft = {
            "id": header["id"],
            "invoice_number": header["invoice_number"],
            "invoice_status": header["invoice_status"],
        }
        self._lines = self._lines_from_db(invoice["lines"])
        company = self.service.get_company(header["company_id"]) if header.get("company_id") else None
        self._company = company
        self.company_value.setText(company["name_ar"] if company else "")
        customer = self.service.get_customer(header["customer_id"]) if header.get("customer_id") else None
        self._customer = customer
        self.customer_value.setText(
            (customer["customer_name"] or "") + f"  ({customer['customer_id']})" if customer else ""
        )
        self.invoice_number_value.setText(header["invoice_number"] or "")
        if header.get("invoice_datetime") is not None:
            self.datetime_value.setText(header["invoice_datetime"].strftime("%Y-%m-%d %H:%M"))
        self._refresh_table()
        self._refresh_totals()
        self._dirty = False
        if header["invoice_status"] == STATUS_DRAFT:
            self.enter_draft_mode()
        else:
            self.enter_issued_mode()

    # ======================================================================
    # Actions
    # ======================================================================
    def _confirm_discard(self) -> bool:
        reply = QMessageBox.question(
            self, "تجاهل التعديلات؟",
            "هناك تعديلات غير محفوظة. هل تريد تجاهلها والمتابعة؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def on_new(self) -> None:
        if self._dirty and not self._confirm_discard():
            return
        try:
            draft = self.service.create_cashier_draft(user_id=self._current_user_id())
        except CashierError as exc:
            QMessageBox.warning(self, "تعذّر البدء", exc.message)
            return
        self._draft = draft
        self._lines = []
        self._company = None
        self._customer = None
        self.invoice_number_value.setText(draft["invoice_number"])
        self.customer_value.clear()
        self.company_value.clear()
        self._refresh_table()
        self._refresh_totals()
        # Pick up any newly-added products for the fresh invoice.
        self._load_products(self.product_search.text())
        self._dirty = False
        self.enter_draft_mode()

    def on_save(self) -> None:
        if self._draft is None:
            QMessageBox.information(self, "تنبيه", "أنشئ فاتورة جديدة أولًا.")
            return
        try:
            saved = self.service.save_cashier_draft(
                self._draft.get("id"), self._collect_form(), user_id=self._current_user_id()
            )
        except CashierError as exc:
            QMessageBox.warning(self, "تعذّر الحفظ", exc.message)
            return
        self._apply_saved(saved)
        self._dirty = False
        self.enter_draft_mode()
        QMessageBox.information(self, "تم", "تم حفظ المسودة بنجاح.")

    def on_open(self) -> None:
        if self._dirty and not self._confirm_discard():
            return
        dialog = CashierDraftDialog(self.service, self)
        if not (dialog.exec() and dialog.selected_id):
            return
        invoice = self.service.get_cashier_invoice(dialog.selected_id)
        if invoice is None:
            QMessageBox.warning(self, "غير موجودة", "تعذّر تحميل المسودة.")
            return
        self._load_full(invoice)

    def on_search_invoices(self) -> None:
        """F1: open the all-invoices search (number + company/customer dropdowns)."""
        if self._dirty and not self._confirm_discard():
            return
        dialog = CashierInvoiceSearchDialog(self.service, self)
        if not (dialog.exec() and dialog.selected_id):
            return
        invoice = self.service.get_cashier_invoice(dialog.selected_id)
        if invoice is None:
            QMessageBox.warning(self, "غير موجودة", "تعذّر تحميل الفاتورة.")
            return
        # A DRAFT opens editable; an ISSUED invoice opens read-only with the
        # "إلغاء الاعتماد" button available so it can be reopened for editing.
        self._load_full(invoice)

    def on_unissue(self) -> None:
        """Cancel the local approval of an ISSUED invoice (ISSUED -> DRAFT)."""
        if self._draft is None or self._draft.get("id") is None:
            return
        if self._draft.get("invoice_status") != STATUS_ISSUED:
            QMessageBox.information(self, "تنبيه", "هذه الفاتورة ليست مُصدرة.")
            return
        reply = QMessageBox.question(
            self, "إلغاء الاعتماد",
            "سيتم إرجاع الفاتورة إلى حالة مسودة ليمكن تعديلها. هل تريد المتابعة؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            reverted = self.service.unissue_cashier_invoice(
                self._draft["id"], user_id=self._current_user_id()
            )
        except CashierError as exc:
            QMessageBox.warning(self, "تعذّر إلغاء الاعتماد", exc.message)
            return
        self._load_full(reverted)
        QMessageBox.information(self, "تم", "تم إلغاء الاعتماد. الفاتورة الآن مسودة قابلة للتعديل.")

    # ------------------------------------------------------------------
    # Printing (thermal receipt)
    # ------------------------------------------------------------------
    def _open_receipt_preview(self, *, allow_draft: bool) -> None:
        """Build the receipt model for the saved invoice and open the preview."""
        if self._draft is None or self._draft.get("id") is None:
            QMessageBox.information(self, "تنبيه", "احفظ الفاتورة أولًا قبل الطباعة.")
            return
        invoice_id = int(self._draft["id"])
        try:
            invoice = self._print_service.load_printable_cashier_invoice(invoice_id)
            self._print_service.validate_invoice_for_print(invoice, allow_draft_preview=allow_draft)
            self._print_service.validate_invoice_totals(invoice)
            model = self._print_service.build_cashier_receipt_model(invoice, is_preview=allow_draft)
        except CashierPrintError as exc:
            QMessageBox.warning(self, "تعذّر التحضير للطباعة", exc.message)
            return
        qr_uri = None
        if model["qr"]["available"] and model["qr"]["base64"]:
            try:
                from app.ui.screens.cashier_receipt_print import render_qr_data_uri
                qr_uri = render_qr_data_uri(model["qr"]["base64"], paper_mm=model["paper_mm"])
            except Exception:  # noqa: BLE001 - fall back to the "no QR" notice
                qr_uri = None
        try:
            from app.ui.screens.cashier_receipt_print import CashierReceiptPreviewDialog
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "الطباعة", f"محرّك العرض غير متاح:\n{exc}")
            return
        dialog = CashierReceiptPreviewDialog(
            self._print_service, invoice_id, model, qr_uri, self,
            on_printed=self._after_printed,
        )
        dialog.exec()

    def on_preview_receipt(self) -> None:
        # Preview is allowed for a saved draft (watermarked) and for issued.
        self._open_receipt_preview(allow_draft=True)

    def on_print_receipt(self) -> None:
        """Official thermal print — ISSUED only, and only with a compliant QR."""
        if self._draft is None or self._draft.get("id") is None:
            QMessageBox.information(self, "تنبيه", "احفظ الفاتورة أولًا قبل الطباعة.")
            return
        invoice_id = int(self._draft["id"])
        try:
            invoice = self._print_service.load_printable_cashier_invoice(invoice_id)
            self._print_service.validate_invoice_for_print(invoice, allow_draft_preview=False)
            self._print_service.validate_invoice_totals(invoice)
        except CashierPrintError as exc:
            QMessageBox.warning(self, "تعذّر الطباعة", exc.message)
            return
        missing = check_zatca_prerequisites(invoice["header"])
        if missing:
            # Do not print an official receipt with a fake/missing Phase-2 QR.
            QMessageBox.warning(
                self, "طباعة المرحلة الثانية غير ممكنة", ZatcaPrerequisitesMissing(missing).message
            )
            return
        self._open_receipt_preview(allow_draft=False)

    def _after_printed(self, updated_invoice: dict[str, Any]) -> None:
        """Refresh local state after a successful print (reprint label, etc.)."""
        header = updated_invoice["header"]
        if self._draft is not None and self._draft.get("id") == header["id"]:
            self._draft["invoice_status"] = header["invoice_status"]

    def on_issue(self) -> None:
        if self._draft is None:
            QMessageBox.information(self, "تنبيه", "أنشئ فاتورة أولًا.")
            return
        if not self._lines:
            QMessageBox.warning(self, "تنبيه", "لا يمكن إتمام فاتورة بدون أصناف.")
            return
        if self._company is None:
            QMessageBox.warning(self, "تنبيه", "يجب اختيار الشركة قبل إتمام الفاتورة.")
            return
        reply = QMessageBox.question(
            self, "إتمام الفاتورة",
            "سيتم إتمام الفاتورة محليًا ولن يمكن تعديلها بعد ذلك. هل تريد المتابعة؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        uid = self._current_user_id()
        try:
            if self._draft.get("id") is None:
                saved = self.service.save_cashier_draft(None, self._collect_form(), user_id=uid)
                self._apply_saved(saved)
            issued = self.service.issue_cashier_invoice_locally(
                self._draft.get("id"), self._collect_form(), user_id=uid
            )
        except CashierError as exc:
            QMessageBox.warning(self, "تعذّر الإتمام", exc.message)
            return
        self._apply_saved(issued)
        self._dirty = False
        self.enter_issued_mode()
        QMessageBox.information(
            self, "تم",
            f"تم إتمام الفاتورة {issued['header']['invoice_number']} بنجاح.",
        )

    def on_delete(self) -> None:
        """Delete the open draft from the database and clear the screen."""
        if self._draft is None or self._draft.get("id") is None:
            QMessageBox.information(self, "تنبيه", "لا توجد مسودة محفوظة لحذفها.")
            return
        number = self._draft.get("invoice_number") or ""
        if QMessageBox.question(
            self, "تأكيد الحذف",
            f"هل تريد حذف المسودة {number}؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            deleted = self.service.delete_cashier_draft(
                self._draft["id"], user_id=self._current_user_id()
            )
        except CashierError as exc:
            QMessageBox.warning(self, "تعذّر الحذف", exc.message)
            return
        if not deleted:
            # The guarded DELETE matched nothing: another window issued or
            # removed it in the meantime, so the screen is out of date.
            QMessageBox.warning(self, "تعذّر الحذف", "لم يتم حذف المسودة: ربما تغيّرت من نافذة أخرى.")
            self.enter_idle_mode()
            return
        QMessageBox.information(self, "تم", "تم حذف المسودة.")
        self.enter_idle_mode()

    def on_delete_all(self) -> None:
        """Delete every cashier invoice — issued ones included, irreversibly."""
        try:
            counts = self.service.count_cashier_invoices()
        except Exception as exc:  # noqa: BLE001 - surface, never crash
            QMessageBox.warning(self, "تعذّر الحذف", f"تعذّر قراءة عدد الفواتير: {exc}")
            return
        total = sum(counts.values())
        if total == 0:
            QMessageBox.information(self, "تنبيه", "لا توجد فواتير كاشير لحذفها.")
            return
        drafts = counts.get(STATUS_DRAFT, 0)
        issued = counts.get(STATUS_ISSUED, 0)
        if QMessageBox.question(
            self, "تأكيد حذف الكل",
            f"سيتم حذف جميع فواتير الكاشير نهائيًا: {total} فاتورة "
            f"({drafts} مسودة، {issued} مُصدرة).\n"
            "سيتم حذف الفواتير المُصدرة أيضًا، ولا يمكن التراجع عن هذا الإجراء.\n"
            "هل تريد المتابعة؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        # Issued invoices are completed sales: make the user say yes twice before
        # they are gone for good.
        if issued and QMessageBox.warning(
            self, "تأكيد أخير",
            f"تحذير: سيتم حذف {issued} فاتورة مُصدرة (مكتملة) نهائيًا. هل أنت متأكد؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            result = self.service.delete_all_cashier_invoices(
                include_issued=True, user_id=self._current_user_id()
            )
        except CashierError as exc:
            QMessageBox.warning(self, "تعذّر الحذف", exc.message)
            return
        QMessageBox.information(
            self, "تم",
            f"تم حذف {result['deleted']} فاتورة (منها {result['issued_deleted']} مُصدرة).",
        )
        # Whatever was on screen is almost certainly one of the deleted rows.
        self.enter_idle_mode()

    def on_cancel(self) -> None:
        if self._draft is not None and self._draft.get("id") is not None:
            invoice = self.service.get_cashier_invoice(self._draft["id"])
            if invoice is not None:
                self._load_full(invoice)
                return
        self.enter_idle_mode()

    def _on_close(self) -> None:
        if self._dirty and not self._confirm_discard():
            return
        window = self.window()
        if window is not None:
            window.close()


__all__ = ["CashierPage"]
