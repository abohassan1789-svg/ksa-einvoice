"""Saudi Phase-2 sales-invoice screen (فاتورة المبيعات السعودية).

An Arabic-first (RTL) screen for creating and managing electronic invoices on the
five ``sales_invoice*`` tables. It reuses the InvPhase2 master data (companies,
customers, products) through :class:`SaudiSalesInvoiceService` and deliberately
avoids all legacy sales/accounting/inventory/currency/shipment behaviour.

The visual design follows the approved reference (``F:\\ErpLogstic``): a header
with a live clock, a two-row toolbar + status badge, a borderless invoice-header
grid, a collapsible Phase-2 technical section, a details table with a pale-green
subtotal footer, a totals panel, a quick-item-entry card and a notes card.

Nothing here generates UUID / ICV / PIH / invoice hash / XML / QR / signatures or
calls ZATCA; approval is intentionally disabled until a dedicated Phase-2
approval service exists.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QDate, QDateTime, QEvent, Qt, QTime, QTimer, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.sales_invoice import (
    DEFAULT_UNIT_CODE,
    DEFAULT_VAT_RATE,
    MAX_PRODUCT_CODE_LEN,
    MAX_PRODUCT_NAME_LEN,
    PAYMENT_CASH,
    PAYMENT_CREDIT,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_LABELS_AR,
    VOUCHER_NUMBER_OFFSET,
    voucher_serial_from_invoice_number,
)
from app.security.session_context import SESSION
from app.services import saudi_zatca_generator as zatca_gen
from app.services.saudi_sales_invoice_service import (
    SaudiSalesInvoiceError,
    SaudiSalesInvoiceService,
)
from app.ui.common.saudi_invoice_style import (
    SI_GREEN,
    button_qss,
    si_icon,
    si_pixmap,
    style_button,
)
from app.ui.dialogs.saudi_invoice_dialogs import (
    EntityPickerDialog,
    SaudiInvoiceSearchDialog,
)

# Column indices for the details table.
COL_CODE, COL_NAME, COL_QTY, COL_PRICE, COL_BEFORE, COL_VAT_RATE, COL_VAT, COL_TOTAL = range(8)

# First row of every master-data combo; carries None as its data.
COMBO_PLACEHOLDER = "— اختر —"

# Phase-2 technical fields shown read-only: (zatca column, Arabic label).
ZATCA_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("integration_status", "حالة التكامل"),
    ("uuid", "UUID"),
    ("invoice_counter_value", "ICV"),
    ("previous_invoice_hash", "Previous Invoice Hash"),
    ("invoice_hash", "Invoice Hash"),
    ("cryptographic_stamp", "الختم التشفيري"),
    ("public_key_snapshot", "المفتاح العام"),
    ("digital_signature", "التوقيع الرقمي"),
    ("xml_file_name", "اسم ملف XML"),
    ("xml_file_path", "مسار XML"),
    ("zatca_request_id", "ZATCA Request ID"),
    ("xml_generated_at", "تاريخ إنشاء XML"),
    ("submitted_at", "تاريخ الإرسال"),
    ("accepted_at", "تاريخ القبول"),
)

APPROVE_DISABLED_TOOLTIP = (
    "سيتم تفعيل الاعتماد بعد تنفيذ خدمة الاعتماد ومتطلبات المرحلة الثانية"
)

# VOUCHER_NUMBER_OFFSET / voucher_serial_from_invoice_number now live in
# app.models.sales_invoice so the printed سند and the customer statement apply
# the identical rule; they are imported above and re-exported here for callers.

SALES_INVOICE_QSS = """
QWidget#saudiSalesInvoicePage { background: #F5F7FA; }
QScrollArea#saudiScroll { background: #F5F7FA; border: none; }
QWidget#saudiScrollBody { background: #F5F7FA; }

QFrame#card { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px; }
QFrame#headerCard { background: transparent; border: none; }
QFrame#headerLine { background: #E5E7EB; border: none; min-height: 1px; max-height: 1px; }

QLabel#titleLabel { color: #1F2D3D; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 22px; font-weight: 800; background: transparent; }
QLabel#subtitleLabel { color: #6B7280; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; background: transparent; }
QLabel#metaLabel { color: #6B7280; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; background: transparent; }
QLabel#sectionTitle { color: #00843D; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 15px; font-weight: 800; background: transparent; }
QLabel { color: #1F2937; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 13px; font-weight: 700; background: transparent; }
QLabel#fieldLabel { min-height: 16px; max-height: 16px; color: #111827; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700; background: transparent; }
QLabel#statusCaption { color: #374151; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 13px; font-weight: 800; background: transparent; }

QLineEdit, QComboBox, QDateTimeEdit {
    min-height: 26px; max-height: 28px;
    border: 1px solid #D7DEE7; border-radius: 6px; background: #FFFFFF;
    padding: 1px 8px; color: #111827; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 700;
}
QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus { border: 1px solid #00843D; }
QLineEdit:read-only { background: #F4F6F9; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    border: 1px solid #D7DEE7; background: #FFFFFF;
    selection-background-color: #00843D; selection-color: #FFFFFF; outline: none;
}
QDateTimeEdit::drop-down { border: none; width: 22px; }

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

QFrame#tableFooter { background: #EAF6EF; border: 1px solid #D7EBDF; border-radius: 8px; }
QFrame#summaryRow { background: #F9FAFB; border: 1px solid #EEF1F4; border-radius: 8px; }
QFrame#netBox { background: #E8F5EC; border: 1px solid #BFE6CF; border-radius: 10px; }
QLabel#netLabel { color: #00843D; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 12px; font-weight: 800; background: transparent; }

QLineEdit#subtotal_value, QLineEdit#vat_value, QLineEdit#items_total_value {
    background: transparent; border: none; color: #111827;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 14px; font-weight: 800; min-height: 28px; padding: 0 4px;
}
QLineEdit#total_value {
    background: transparent; border: none; color: #00843D;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 14px; font-weight: 800; min-height: 40px; padding: 0 2px;
}

QTextEdit {
    border: 1px solid #D7DEE7; border-radius: 10px; background: #FFFFFF;
    color: #111827; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 13px; font-weight: 700; padding: 6px;
}
QTextEdit:focus { border: 1px solid #00843D; }

QPushButton#phase2Toggle {
    text-align: right; background: #F9FAFB; color: #00843D; border: 1px solid #E5E7EB;
    border-radius: 8px; padding: 7px 12px; font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size: 13px; font-weight: 800;
}
QPushButton#phase2Toggle:hover { background: #F1F5F9; }
QPushButton#lookupButton {
    background: #00843D; color: #FFFFFF; border: none; border-radius: 6px;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-weight: 800;
}
QPushButton#lookupButton:hover { background: #046A31; }
QPushButton#searchButton {
    background: #00843D; color: #FFFFFF; border: none; border-radius: 8px;
    font-family: 'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-weight: 800;
}
QPushButton#searchButton:hover { background: #046A31; }
"""


# Density. A maximised window on a 768px-tall screen leaves the page ~680px once
# the title bar and taskbar take theirs, so that is where compact has to start —
# 720 would leave the most common small laptop on the roomy settings it cannot
# afford. Above it nothing changes: big screens keep the comfortable look.
COMPACT_BELOW_HEIGHT = 760
ROW_HEIGHT, COMPACT_ROW_HEIGHT = 44, 32
TABLE_HEADER, COMPACT_TABLE_HEADER = 50, 40
# Padding inside every card. Measured: tightening these is worth a whole extra
# invoice line on a 768px screen — the other candidates (footer height, summary
# rows) turned out to be worth nothing, so they keep their roomy look.
CARD_MARGINS, COMPACT_CARD_MARGINS = (18, 12, 18, 14), (12, 6, 12, 8)
CARD_SPACING, COMPACT_CARD_SPACING = 8, 4

# Icon (15) + icon-to-text gap (6) + padding (7 each side) + border (1 each side),
# i.e. everything in a toolbar button that is not the label itself.
_BTN_CHROME = 15 + 6 + 2 * 7 + 2
_TOOLBAR_FONT: QFont | None = None


def _toolbar_metrics() -> QFontMetrics:
    """Metrics for the exact font the toolbar stylesheet paints with.

    ``button.font()`` is *not* it: a stylesheet font-family/size never reaches
    QWidget::font(), so measuring that would size every button against the
    default application font instead of Cairo 12px. The family list mirrors
    button_qss so the fallback resolves the same way when Cairo is missing.
    """
    global _TOOLBAR_FONT
    if _TOOLBAR_FONT is None:
        font = QFont()
        font.setFamilies(["Cairo", "Segoe UI", "Tahoma", "Arial"])
        font.setPixelSize(12)
        font.setWeight(QFont.Bold)
        _TOOLBAR_FONT = font
    return QFontMetrics(_TOOLBAR_FONT)


class SaudiSalesInvoicePage(QWidget):
    """The Saudi sales-invoice screen. Modes: ``view`` | ``new`` | ``edit``."""

    def __init__(
        self,
        service: SaudiSalesInvoiceService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or SaudiSalesInvoiceService(permission_check=SESSION.can)
        self._mode = "view"
        self._current: dict[str, Any] | None = None  # loaded invoice dict
        self._current_status = STATUS_DRAFT
        self._dirty = False
        # True while the invoice on screen is one we *just saved* (as opposed to
        # one picked from the search dialog). The invoice stays visible either
        # way, but the buttons differ — see _set_mode.
        self._post_save = False
        self._suspend_cell_signal = False
        # item_code of the product currently picked in the quick-entry combo, so
        # a typed-over selection can drop its leftover code.
        self._picked_code = ""
        self._zatca_fields: dict[str, QLineEdit] = {}
        self._phase2_expanded = False
        # None (not False) so the first _apply_density always applies a state:
        # the screen may well open on a short display.
        self._compact: bool | None = None
        self._cards: list[QVBoxLayout] = []  # every card's layout, for density

        self.setObjectName("saudiSalesInvoicePage")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(SALES_INVOICE_QSS)
        # Measured, not guessed: the content's own minimumSizeHint is 1212px wide,
        # so the old 1360 floor was 148px of pure exclusion — it pushed the screen
        # off smaller laptops for nothing. The height floor is what the chrome
        # needs with a 3-row table; below that the scroll area takes over.
        self.setMinimumSize(1212, 560)

        self._build_ui()
        self._load_master_combos()
        self._start_clock()
        # Open idle and fully locked: no field accepts input until the user
        # presses "New" (to create) or "Edit" (after selecting an invoice).
        self.enter_ready_mode()

    # ======================================================================
    # Small builders / helpers
    # ======================================================================
    def _make_card(self, title: str | None = None, icon_name: str | None = None):
        card = QFrame()
        card.setObjectName("card")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(15, 23, 42, 22))
        shadow.setOffset(0, 2)
        card.setGraphicsEffect(shadow)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*CARD_MARGINS)
        layout.setSpacing(CARD_SPACING)
        self._cards.append(layout)
        if title:
            header = QHBoxLayout()
            header.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            header.addWidget(label)
            if icon_name:
                icon_label = QLabel()
                icon_label.setPixmap(si_pixmap(icon_name, color=SI_GREEN, size=18))
                header.addWidget(icon_label)
            header.addStretch(1)
            layout.addLayout(header)
        return card, layout

    def _labeled(self, label_text: str, widget: QWidget) -> QWidget:
        box = QWidget()
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(3)
        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        vbox.addWidget(label)
        vbox.addWidget(widget)
        return box

    def _lookup_field(self, label_text: str, combo: QComboBox, on_search) -> QWidget:
        """A combo + small green search button, wrapped under a field label."""
        combo.setLayoutDirection(Qt.RightToLeft)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(combo, 1)
        button = QPushButton()
        button.setObjectName("lookupButton")
        button.setIcon(si_icon("fa5s.search", color="#FFFFFF"))
        button.setFixedSize(28, 28)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(on_search)
        hbox.addWidget(button)
        self._lookup_buttons.append(button)
        return self._labeled(label_text, row)

    def _make_toolbar_button(self, text, object_name, icon_name, kind) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setFixedHeight(34)
        button.setCursor(Qt.PointingHandCursor)
        button.setIconSize(QSize(15, 15))
        button.setIcon(si_icon(icon_name, color="#FFFFFF" if kind != "white" else "#374151"))
        # Tighter padding than the shared button_qss, which other screens keep as-is.
        button.setStyleSheet(button_qss(kind) + "QPushButton { padding:3px 7px; }")
        # Width fits the label instead of a flat 96px floor: that floor gave "جديد"
        # (56px of text) the same width as "حذف الكل" and pushed the row past the
        # screen, clipping the last buttons. Measured against the same font the
        # stylesheet paints with, so no label is ever cut.
        width = _toolbar_metrics().horizontalAdvance(str(text)) + _BTN_CHROME
        button.setMinimumWidth(max(58, width))
        return button

    # ======================================================================
    # UI assembly
    # ======================================================================
    def _build_ui(self) -> None:
        self._lookup_buttons: list[QPushButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setObjectName("saudiScroll")
        scroll.setWidgetResizable(True)
        # AsNeeded, not AlwaysOff: below the content's 1212px floor the old policy
        # clipped the left edge silently with no way to reach it. The bar only
        # ever appears on a screen narrower than that.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        body = QWidget()
        body.setObjectName("saudiScrollBody")
        scroll.setWidget(body)

        self.main_layout = QVBoxLayout(body)
        self.main_layout.setContentsMargins(12, 6, 12, 8)
        self.main_layout.setSpacing(6)

        self._build_header()
        self._build_invoice_card()
        self._build_phase2_section()
        self._build_details_row()
        self._build_quick_entry_card()
        self._build_notes_field()  # hidden — notes card removed at user's request
        # No trailing stretch: every spare pixel belongs to the items table (the
        # details row carries the only stretch factor). A stretch here competed
        # with it and burned 171px of empty space at 1080p — four invoice lines.

    def _build_header(self) -> None:
        container = QFrame()
        container.setObjectName("headerCard")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 0, 4, 0)
        vbox.setSpacing(6)

        # -- info strip: title (right) ... meta (left) --
        top = QHBoxLayout()
        top.setSpacing(12)

        title_box = QWidget()
        title_v = QVBoxLayout(title_box)
        title_v.setContentsMargins(0, 0, 0, 0)
        title_v.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(si_pixmap("fa5s.file-invoice", color=SI_GREEN, size=26))
        self.title_label = QLabel("فاتورة المبيعات السعودية")
        self.title_label.setObjectName("titleLabel")
        title_row.addWidget(icon)
        title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        self.subtitle_label = QLabel("إنشاء وإدارة الفواتير الإلكترونية – المرحلة الثانية")
        self.subtitle_label.setObjectName("subtitleLabel")
        title_v.addLayout(title_row)
        title_v.addWidget(self.subtitle_label)
        top.addWidget(title_box)
        top.addStretch(1)

        meta_box = QWidget()
        # A grid, not a VBox, so the three meta lines can be re-laid as one row
        # when the screen is short: stacked they are the tallest thing in the
        # header (~54px) and set its floor, so the subtitle alone never shrinks it.
        self.meta_grid = QGridLayout(meta_box)
        self.meta_grid.setContentsMargins(0, 0, 0, 0)
        self.meta_grid.setSpacing(2)
        meta_v = self.meta_grid
        self.user_label = QLabel("")
        self.date_label = QLabel("")
        self.time_label = QLabel("")
        for label in (self.user_label, self.date_label, self.time_label):
            label.setObjectName("metaLabel")
            label.setMinimumWidth(150)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter | Qt.AlignAbsolute)
        self._lay_out_meta(stacked=True)
        top.addWidget(meta_box)
        vbox.addLayout(top)

        # -- toolbar strip: all actions on ONE row (RTL: first added sits right) --
        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        # Icon-only twin of F1: same dialog, reachable by mouse. Stays enabled in
        # every mode (like the shortcut), so _set_mode never touches it.
        self.search_button = QPushButton()
        self.search_button.setObjectName("searchButton")
        self.search_button.setIcon(si_icon("fa5s.search", color="#FFFFFF"))
        self.search_button.setIconSize(QSize(16, 16))
        self.search_button.setFixedSize(38, 38)
        self.search_button.setCursor(Qt.PointingHandCursor)
        self.search_button.setToolTip("بحث عن فاتورة (F1)")
        self.new_button = self._make_toolbar_button("جديد", "new_button", "fa5s.plus", "green")
        self.duplicate_button = self._make_toolbar_button(
            "تكرار الفاتورة", "duplicate_button", "fa5s.copy", "green"
        )
        self.save_button = self._make_toolbar_button("حفظ", "save_button", "fa5s.save", "green")
        self.edit_button = self._make_toolbar_button("تعديل", "edit_button", "fa5s.pen", "blue")
        self.update_button = self._make_toolbar_button("تحديث", "update_button", "fa5s.sync", "blue")
        self.approve_button = self._make_toolbar_button("اعتماد الفاتورة", "approve_button", "fa5s.check-double", "blue")
        self.delete_button = self._make_toolbar_button("حذف", "delete_button", "fa5s.trash", "red")
        self.delete_all_button = self._make_toolbar_button("حذف الكل", "delete_all_button", "fa5s.trash-alt", "red")
        self.cancel_button = self._make_toolbar_button("إلغاء", "cancel_button", "fa5s.times", "white")
        self.back_button = self._make_toolbar_button("خروج", "back_button", "fa5s.sign-out-alt", "gray")
        # Preview + Export-PDF + receipt-voucher print sit on the SAME action row
        # (at the left end under RTL).
        self.preview_button = self._make_toolbar_button("معاينة", "preview_button", "fa5s.eye", "gray")
        self.export_pdf_button = self._make_toolbar_button("تصدير PDF", "export_pdf_button", "fa5s.file-pdf", "red")
        self.print_voucher_button = self._make_toolbar_button("طباعة سند", "print_voucher_button", "fa5s.receipt", "gray")
        for button in (self.search_button,
                       self.new_button, self.duplicate_button,
                       self.save_button, self.edit_button, self.update_button,
                       self.approve_button, self.delete_button, self.delete_all_button,
                       self.cancel_button, self.back_button,
                       self.preview_button, self.export_pdf_button, self.print_voucher_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        vbox.addLayout(toolbar)

        # Document status is shown in the header grid ("حالة المستند"); the
        # separate top badge is hidden at the user's request. The label is kept
        # alive (never shown) so status styling code has a target.
        self.status_badge = QLabel(STATUS_LABELS_AR[STATUS_DRAFT])
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setVisible(False)

        line = QFrame()
        line.setObjectName("headerLine")
        vbox.addWidget(line)

        self.main_layout.addWidget(container)
        self._wire_actions()

    def _build_invoice_card(self) -> None:
        card = QFrame()
        card.setObjectName("headerCard")
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(4, 2, 4, 2)
        vbox.setSpacing(6)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        label = QLabel("بيانات الفاتورة")
        label.setObjectName("sectionTitle")
        icon = QLabel()
        icon.setPixmap(si_pixmap("fa5s.calendar-alt", color=SI_GREEN, size=16))
        title_row.addWidget(label)
        title_row.addWidget(icon)
        title_row.addStretch(1)
        vbox.addLayout(title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)

        # Row 0 (RTL: column 0 is rightmost).
        self.invoice_number_input = QLineEdit()
        self.invoice_number_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        self.invoice_number_input.textEdited.connect(self._mark_dirty)

        self.issue_datetime_input = QDateTimeEdit()
        self.issue_datetime_input.setCalendarPopup(True)
        self.issue_datetime_input.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.issue_datetime_input.setDateTime(QDateTime.currentDateTime())

        self.seller_combo = QComboBox()
        self.seller_combo.currentIndexChanged.connect(self._on_seller_changed)

        self.seller_vat_input = QLineEdit()
        self.seller_vat_input.setReadOnly(True)
        self.seller_vat_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)

        grid.addWidget(self._labeled("رقم الفاتورة", self.invoice_number_input), 0, 0)
        grid.addWidget(self._labeled("التاريخ والوقت", self.issue_datetime_input), 0, 1)
        grid.addWidget(self._lookup_field("اسم البائع", self.seller_combo, self._search_seller), 0, 2)
        grid.addWidget(self._labeled("الرقم الضريبي للبائع", self.seller_vat_input), 0, 3)

        # Row 1.
        self.customer_combo = QComboBox()
        self.customer_combo.currentIndexChanged.connect(self._on_customer_changed)

        self.payment_combo = QComboBox()
        self.payment_combo.setLayoutDirection(Qt.RightToLeft)
        self.payment_combo.addItem("نقدي", PAYMENT_CASH)
        self.payment_combo.addItem("آجل", PAYMENT_CREDIT)
        self.payment_combo.currentIndexChanged.connect(self._mark_dirty)

        self.customer_vat_input = QLineEdit()
        self.customer_vat_input.setReadOnly(True)
        self.customer_vat_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)

        # Document status is hidden from the grid at the user's request; the
        # widget is kept alive (never shown) so _apply_status_badge still has a
        # target and the mode logic keeps working.
        self.doc_status_input = QLineEdit(STATUS_LABELS_AR[STATUS_DRAFT])
        self.doc_status_input.setReadOnly(True)
        self.doc_status_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        self.doc_status_input.setVisible(False)

        # Seller commercial registration (read-only, auto-filled from the
        # selected company) fills the slot the status field used to occupy.
        self.seller_cr_input = QLineEdit()
        self.seller_cr_input.setReadOnly(True)
        self.seller_cr_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)

        # RTL columns (0 = rightmost): customer name, then its VAT beside it;
        # seller CR in the middle, and payment type in the far-left slot.
        grid.addWidget(self._lookup_field("اسم العميل", self.customer_combo, self._search_customer), 1, 0)
        grid.addWidget(self._labeled("الرقم الضريبي للعميل", self.customer_vat_input), 1, 1)
        grid.addWidget(self._labeled("السجل التجاري للبائع", self.seller_cr_input), 1, 2)
        grid.addWidget(self._labeled("نوع الدفع", self.payment_combo), 1, 3)

        for col in range(4):
            grid.setColumnStretch(col, 1)
            grid.setColumnMinimumWidth(col, 210)
        vbox.addLayout(grid)
        self.main_layout.addWidget(card)

    def _build_phase2_section(self) -> None:
        self.phase2_toggle = QPushButton("▸   بيانات المرحلة الثانية")
        self.phase2_toggle.setObjectName("phase2Toggle")
        self.phase2_toggle.setCursor(Qt.PointingHandCursor)
        self.phase2_toggle.clicked.connect(self._toggle_phase2)
        self.main_layout.addWidget(self.phase2_toggle)

        self.phase2_body, layout = self._make_card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for index, (column, label_text) in enumerate(ZATCA_FIELD_LABELS):
            field = QLineEdit()
            field.setReadOnly(True)
            field.setPlaceholderText("—")
            field.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
            self._zatca_fields[column] = field
            row, col = divmod(index, 3)
            grid.addWidget(self._labeled(label_text, field), row, col)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)
        self.phase2_body.setVisible(False)
        self.main_layout.addWidget(self.phase2_body)

    def _build_details_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(16)

        details_card, details_layout = self._make_card("تفاصيل الفاتورة", "fa5s.list-ul")
        self.lines_table = QTableWidget()
        self.lines_table.setColumnCount(8)
        self.lines_table.setHorizontalHeaderLabels([
            "رقم الصنف", "اسم الصنف", "الكمية", "السعر",
            "إجمالي قبل الضريبة", "نسبة الضريبة", "قيمة الضريبة", "إجمالي شامل الضريبة",
        ])
        self.lines_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lines_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lines_table.setAlternatingRowColors(True)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.lines_table.horizontalHeader().setFixedHeight(TABLE_HEADER)
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.lines_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lines_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # A floor of 3 rows, not 5: the table is the one widget that grows into
        # whatever is left, so this number only ever binds on a screen too short
        # to give it more. At 235 the whole page stopped fitting a 768px screen
        # and scrolled — which hid the very lines it was protecting.
        self.lines_table.setMinimumHeight(TABLE_HEADER + 3 * ROW_HEIGHT)
        self.lines_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.lines_table.cellChanged.connect(self._on_cell_changed)
        details_layout.addWidget(self.lines_table, 1)

        footer = QFrame()
        footer.setObjectName("tableFooter")
        footer.setFixedHeight(38)
        footer_h = QHBoxLayout(footer)
        footer_h.setContentsMargins(14, 4, 14, 4)
        footer_label = QLabel("إجمالي المنتجات قبل الضريبة")
        footer_label.setStyleSheet("color:#0B3B23; font-weight:800; background:transparent;")
        self.items_total_value = QLineEdit("0.00")
        self.items_total_value.setObjectName("items_total_value")
        self.items_total_value.setReadOnly(True)
        self.items_total_value.setMaximumWidth(180)
        self.items_total_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        footer_h.addWidget(footer_label)
        footer_h.addStretch(1)
        footer_h.addWidget(self.items_total_value)
        details_layout.addWidget(footer)

        # Totals panel (fixed 340px, on the right under RTL).
        totals_card, totals_layout = self._make_card("الإجماليات", "fa5s.chart-pie")
        totals_card.setFixedWidth(340)
        totals_card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.subtotal_value = self._summary_row(totals_layout, "إجمالي الفاتورة قبل الضريبة", "subtotal_value")
        self.vat_value = self._summary_row(totals_layout, "قيمة الضريبة", "vat_value")

        net_box = QFrame()
        net_box.setObjectName("netBox")
        net_box.setFixedHeight(56)
        net_h = QHBoxLayout(net_box)
        net_h.setContentsMargins(14, 8, 14, 8)
        net_label = QLabel("الإجمالي النهائي شامل الضريبة")
        net_label.setObjectName("netLabel")
        self.total_value = QLineEdit("0.00")
        self.total_value.setObjectName("total_value")
        self.total_value.setReadOnly(True)
        self.total_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        net_h.addWidget(net_label)
        net_h.addWidget(self.total_value, 1)
        totals_layout.addWidget(net_box)
        totals_layout.addStretch(1)
        # RTL: add the totals card FIRST so it sits on the right; the details
        # card is added second and stretches to fill the remaining left area.
        row.addWidget(totals_card)
        row.addWidget(details_card, 1)

        self.main_layout.addLayout(row, 3)

    def _summary_row(self, parent_layout: QVBoxLayout, label_text: str, object_name: str) -> QLineEdit:
        frame = QFrame()
        frame.setObjectName("summaryRow")
        frame.setFixedHeight(52)
        hbox = QHBoxLayout(frame)
        hbox.setContentsMargins(12, 4, 12, 4)
        label = QLabel(label_text)
        value = QLineEdit("0.00")
        value.setObjectName(object_name)
        value.setReadOnly(True)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        hbox.addWidget(label)
        hbox.addWidget(value, 1)
        parent_layout.addWidget(frame)
        return value

    def _build_quick_entry_card(self) -> None:
        card, layout = self._make_card("إضافة صنف", "fa5s.tag")
        card.setMaximumHeight(112)
        self.quick_entry_card = card
        row = QHBoxLayout()
        row.setSpacing(10)

        self.item_combo = QComboBox()
        # Editable so an item that is not in the master data can simply be typed:
        # the text becomes the line's name. NoInsert keeps such typing out of the
        # dropdown — an unregistered item is never added to the products list.
        self.item_combo.setEditable(True)
        self.item_combo.setInsertPolicy(QComboBox.NoInsert)
        self.item_combo.lineEdit().setPlaceholderText("اختر صنفًا أو اكتب اسم صنف غير مسجّل…")
        self.item_combo.currentIndexChanged.connect(self._on_item_combo_changed)
        self.item_combo.editTextChanged.connect(self._on_item_text_edited)
        item_field = self._lookup_field("الصنف", self.item_combo, self._search_item)
        item_field.setMinimumWidth(320)

        self.item_code_input = QLineEdit()
        self.item_code_input.setMinimumWidth(120)
        self.item_code_input.setPlaceholderText("لصنف غير مسجّل")
        self.item_code_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)

        self.qty_input = QLineEdit("1")
        self.qty_input.setMinimumWidth(110)
        self.qty_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
        self.price_input = QLineEdit("0")
        self.price_input.setMinimumWidth(120)
        self.price_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)

        self.add_line_button = QPushButton("إضافة إلى الفاتورة")
        self.add_line_button.setIcon(si_icon("fa5s.plus", color="#FFFFFF"))
        self.add_line_button.setIconSize(QSize(18, 18))
        self.add_line_button.setFixedHeight(36)
        self.add_line_button.setMinimumWidth(170)
        self.add_line_button.setCursor(Qt.PointingHandCursor)
        style_button(self.add_line_button, "green")
        self.add_line_button.clicked.connect(self._on_add_line)

        self.new_line_button = QPushButton("سطر جديد")
        self.new_line_button.setIcon(si_icon("fa5s.pen", color="#374151"))
        self.new_line_button.setIconSize(QSize(16, 16))
        self.new_line_button.setFixedHeight(36)
        self.new_line_button.setMinimumWidth(120)
        self.new_line_button.setCursor(Qt.PointingHandCursor)
        self.new_line_button.setToolTip("إضافة سطر فارغ تكتب فيه صنفًا غير مسجّل مباشرةً")
        style_button(self.new_line_button, "white")
        self.new_line_button.clicked.connect(self._on_new_free_line)

        self.remove_line_button = QPushButton("حذف الصنف")
        self.remove_line_button.setIcon(si_icon("fa5s.trash", color="#DC2626"))
        self.remove_line_button.setFixedHeight(36)
        self.remove_line_button.setMinimumWidth(130)
        self.remove_line_button.setCursor(Qt.PointingHandCursor)
        style_button(self.remove_line_button, "white")
        self.remove_line_button.clicked.connect(self._on_remove_line)

        row.addWidget(item_field, 1)
        row.addWidget(self._labeled("رقم الصنف", self.item_code_input))
        row.addWidget(self._labeled("الكمية", self.qty_input))
        row.addWidget(self._labeled("السعر", self.price_input))
        row.addWidget(self.add_line_button)
        row.addWidget(self.new_line_button)
        row.addWidget(self.remove_line_button)
        layout.addLayout(row)
        self.main_layout.addWidget(card)

    def _build_notes_field(self) -> None:
        # The notes card is hidden at the user's request, but notes are still a
        # real invoice column — keep the widget alive (never shown) so save/load
        # continue to round-trip the value.
        self.notes_input = QTextEdit()
        self.notes_input.setLayoutDirection(Qt.RightToLeft)
        self.notes_input.setVisible(False)
        self.notes_input.textChanged.connect(self._mark_dirty)

    # ======================================================================
    # Wiring / clock
    # ======================================================================
    def _wire_actions(self) -> None:
        self.search_button.clicked.connect(self.open_search)
        self.new_button.clicked.connect(self.on_new)
        self.duplicate_button.clicked.connect(self.on_duplicate)
        self.save_button.clicked.connect(self.on_save)
        self.edit_button.clicked.connect(self.on_edit)
        self.update_button.clicked.connect(self.on_update)
        self.approve_button.clicked.connect(self.on_approve)
        self.delete_button.clicked.connect(self.on_delete)
        self.delete_all_button.clicked.connect(self.on_delete_all)
        self.cancel_button.clicked.connect(self.on_cancel)
        self.back_button.clicked.connect(self.on_back)
        self.preview_button.clicked.connect(self.on_preview)
        self.export_pdf_button.clicked.connect(self.on_export_pdf)
        self.print_voucher_button.clicked.connect(self.on_print_voucher)

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
        self.date_label.setText(f"التاريخ: {now:%Y-%m-%d}")
        self.time_label.setText(f"الوقت: {now:%H:%M:%S}")

    # ======================================================================
    # Master combos
    # ======================================================================
    @staticmethod
    def _seller_label(row: dict) -> str:
        return row.get("name_ar") or str(row.get("id"))

    @staticmethod
    def _customer_label(row: dict) -> str:
        return (row.get("customer_name") or "") + f"  ({row.get('customer_id')})"

    @staticmethod
    def _product_label(row: dict) -> str:
        return f"{row.get('item_code')} - {row.get('item_name')}"

    def _load_master_combos(self) -> None:
        self._fill_combo(self.seller_combo, self._safe(self.service.list_companies), "id",
                         self._seller_label)
        self._fill_combo(self.customer_combo, self._safe(self.service.list_customers), "customer_id",
                         self._customer_label)
        self._fill_combo(self.item_combo, self._safe(self.service.list_products), "id",
                         self._product_label)

    def refresh_master_data(self) -> None:
        """Re-read companies / customers / products into the three combos.

        The main window keeps this screen alive in ``open_windows``, so master
        data registered *after* it opened never reached the dropdowns: they were
        filled once in ``__init__`` and never again. Only the search dialogs
        showed a new record, because they query the database live.

        Whatever is picked survives the refill — a half-written invoice must not
        lose its seller / customer / item. The header's VAT and CR boxes are
        deliberately left alone: they belong to the invoice being written, and
        rewriting them here would both fight the user's own edits and mark a
        clean invoice dirty.
        """
        self._refill_preserving(self.seller_combo, self._safe(self.service.list_companies),
                                "id", self._seller_label)
        self._refill_preserving(self.customer_combo, self._safe(self.service.list_customers),
                                "customer_id", self._customer_label)
        self._refresh_item_combo()

    def _refill_preserving(self, combo: QComboBox, rows, id_key, label_fn) -> None:
        previous = combo.currentData()
        self._fill_combo(combo, rows, id_key, label_fn)
        if isinstance(previous, dict):
            self._ensure_combo_record(combo, previous, label_fn(previous), id_key)

    def _refresh_item_combo(self) -> None:
        """Refill the item combo without disturbing the quick-entry card.

        The combo is editable, so its *text* — not its selection — is what a free
        (unregistered) item is made of, and clearing it would delete what the user
        typed. ``currentData`` also keeps reporting the last picked product under
        text that was typed over it (see :meth:`_selected_product`), so the pick is
        only restored when it genuinely still is the pick.
        """
        picked = self._selected_product()
        typed = self.item_combo.currentText()
        self._fill_combo(self.item_combo, self._safe(self.service.list_products), "id",
                         self._product_label)
        if picked is not None:
            self._ensure_combo_record(self.item_combo, picked, self._product_label(picked), "id")
        elif typed and typed != COMBO_PLACEHOLDER:
            self.item_combo.blockSignals(True)
            self.item_combo.setCurrentIndex(-1)
            self.item_combo.setEditText(typed)
            self.item_combo.blockSignals(False)

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return []

    def _fill_combo(self, combo: QComboBox, rows, id_key, label_fn) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(COMBO_PLACEHOLDER, None)
        for row in rows:
            combo.addItem(label_fn(row), row)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _ensure_combo_record(
        self, combo: QComboBox, record: dict, label: str, id_key: str
    ) -> None:
        """Select the combo row whose ``id_key`` matches ``record``'s.

        Matching is by identity (``id`` / ``customer_id``), never by full-dict
        equality — so loading an invoice re-selects the existing master-data row
        from the table instead of appending a snapshot copy (which produced the
        duplicated dropdown entries). The record is only appended when it is
        genuinely absent (e.g. it falls outside the first pre-loaded page).
        """
        target_id = record.get(id_key)
        combo.blockSignals(True)
        found = -1
        if target_id is not None:
            for i in range(combo.count()):
                data = combo.itemData(i)
                if isinstance(data, dict) and data.get(id_key) == target_id:
                    found = i
                    break
        if found < 0:
            combo.addItem(label, record)
            found = combo.count() - 1
        combo.setCurrentIndex(found)
        combo.blockSignals(False)

    # ======================================================================
    # Lookups
    # ======================================================================
    def _search_seller(self) -> None:
        dialog = EntityPickerDialog(
            "بحث عن الشركة البائعة",
            [("id", "الكود"), ("name_ar", "الاسم"), ("name_en", "English"), ("vat_number", "الرقم الضريبي")],
            self.service.search_companies, "id", parent=self,
        )
        if dialog.exec() and dialog.selected:
            rec = dialog.selected
            self._ensure_combo_record(self.seller_combo, rec, rec.get("name_ar") or str(rec.get("id")), "id")
            self._on_seller_changed()

    def _search_customer(self) -> None:
        dialog = EntityPickerDialog(
            "بحث عن العميل",
            [("customer_id", "الكود"), ("customer_name", "الاسم"), ("phone_number", "الهاتف"), ("vat_number", "الرقم الضريبي")],
            self.service.search_customers, "customer_id", parent=self,
        )
        if dialog.exec() and dialog.selected:
            rec = dialog.selected
            self._ensure_combo_record(self.customer_combo, rec,
                                      (rec.get("customer_name") or "") + f"  ({rec.get('customer_id')})",
                                      "customer_id")
            self._on_customer_changed()

    def _search_item(self) -> None:
        dialog = EntityPickerDialog(
            "بحث عن الصنف",
            [("item_code", "رقم الصنف"), ("item_name", "الاسم"), ("price", "السعر")],
            self.service.search_products, "id", parent=self, multi_select=True,
        )
        if not (dialog.exec() and dialog.selected_rows):
            return
        # Every picked product goes straight onto the invoice with quantity 1 at
        # its list price; the user then edits qty/price in the lines table.
        for rec in dialog.selected_rows:
            price = Decimal(str(rec["price"])) if rec.get("price") is not None else Decimal("0")
            self._add_or_merge_line(
                product_id=int(rec["id"]),
                code=str(rec.get("item_code")),
                name=str(rec.get("item_name") or ""),
                qty=Decimal("1"),
                price=price,
                vat_rate=DEFAULT_VAT_RATE,
                unit_code=DEFAULT_UNIT_CODE,
            )
        last = dialog.selected_rows[-1]
        self._ensure_combo_record(self.item_combo, last, f"{last.get('item_code')} - {last.get('item_name')}", "id")
        self.price_input.setText(
            f"{Decimal(str(last['price']))}" if last.get("price") is not None else "0"
        )
        self.qty_input.setText("1")
        self._mark_dirty()

    def _on_seller_changed(self, *_a) -> None:
        rec = self.seller_combo.currentData()
        is_rec = isinstance(rec, dict)
        self.seller_vat_input.setText(str(rec.get("vat_number")) if is_rec else "")
        self.seller_cr_input.setText(str(rec.get("commercial_registration") or "") if is_rec else "")
        self._mark_dirty()

    def _on_customer_changed(self, *_a) -> None:
        rec = self.customer_combo.currentData()
        if isinstance(rec, dict):
            self.customer_vat_input.setText(str(rec.get("vat_number") or ""))
        else:
            self.customer_vat_input.setText("")
        self._mark_dirty()

    # ======================================================================
    # Table lines
    # ======================================================================
    def _selected_product(self) -> dict | None:
        """The product actually chosen from the dropdown, else None.

        An editable QComboBox keeps reporting the previously picked item after
        the user types over its text, so the selection only counts while the text
        still is that item's text. Without this, typing a free item on top of a
        picked product would silently bill the product instead.
        """
        rec = self.item_combo.currentData()
        if not isinstance(rec, dict):
            return None
        index = self.item_combo.currentIndex()
        if index < 0 or self.item_combo.itemText(index).strip() != self.item_combo.currentText().strip():
            return None
        return rec

    def _on_item_combo_changed(self, *_a) -> None:
        """Mirror the picked product's code into the code box (blank if none)."""
        rec = self.item_combo.currentData()
        self._picked_code = str(rec.get("item_code")) if isinstance(rec, dict) else ""
        self.item_code_input.setText(self._picked_code)

    def _on_item_text_edited(self, *_a) -> None:
        # Once the text stops being the picked product's, its code must not stay
        # behind and get attached to whatever free item is being typed.
        if (self._selected_product() is None and self._picked_code
                and self.item_code_input.text() == self._picked_code):
            self.item_code_input.clear()
            self._picked_code = ""

    def _on_new_free_line(self) -> None:
        """Append an empty row and start typing the item straight into it."""
        if self._mode not in ("new", "edit"):
            return
        self._add_or_merge_line(
            product_id=None, code="", name="", qty=Decimal("1"), price=Decimal("0"),
            vat_rate=DEFAULT_VAT_RATE, unit_code=DEFAULT_UNIT_CODE,
        )
        row = self.lines_table.rowCount() - 1
        self.lines_table.setCurrentCell(row, COL_CODE)
        self.lines_table.editItem(self.lines_table.item(row, COL_CODE))
        self._mark_dirty()

    def _on_add_line(self) -> None:
        rec = self._selected_product()
        if rec is None:
            self._add_free_line_from_card()
            return
        try:
            qty = self._parse(self.qty_input.text(), "الكمية")
            price = self._parse(self.price_input.text(), "السعر")
        except ValueError as exc:
            QMessageBox.warning(self, "قيمة غير صالحة", str(exc))
            return
        if qty <= 0:
            QMessageBox.warning(self, "تنبيه", "الكمية يجب أن تكون أكبر من صفر.")
            return
        if price < 0:
            QMessageBox.warning(self, "تنبيه", "السعر لا يمكن أن يكون سالبًا.")
            return
        self._add_or_merge_line(
            product_id=int(rec["id"]),
            code=str(rec.get("item_code")),
            name=str(rec.get("item_name") or ""),
            qty=qty,
            price=price,
            vat_rate=DEFAULT_VAT_RATE,
            unit_code=DEFAULT_UNIT_CODE,
        )
        self.qty_input.setText("1")
        self._mark_dirty()

    def _add_free_line_from_card(self) -> None:
        """Add the typed, unregistered item from the quick-entry card.

        Only the code and the name are kept — nothing is written to the products
        master, so the item stays private to this invoice. The code is optional
        (see the service's _free_item_identity); the name is not.
        """
        name = self.item_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "تنبيه", "اختر الصنف أولًا أو اكتب اسم صنف غير مسجّل.")
            return
        code = self.item_code_input.text().strip()
        try:
            qty = self._parse(self.qty_input.text(), "الكمية")
            price = self._parse(self.price_input.text(), "السعر")
        except ValueError as exc:
            QMessageBox.warning(self, "قيمة غير صالحة", str(exc))
            return
        if qty <= 0:
            QMessageBox.warning(self, "تنبيه", "الكمية يجب أن تكون أكبر من صفر.")
            return
        if price < 0:
            QMessageBox.warning(self, "تنبيه", "السعر لا يمكن أن يكون سالبًا.")
            return
        if len(code) > MAX_PRODUCT_CODE_LEN or len(name) > MAX_PRODUCT_NAME_LEN:
            QMessageBox.warning(
                self, "قيمة غير صالحة",
                f"رقم الصنف حتى {MAX_PRODUCT_CODE_LEN} حرفًا والاسم حتى {MAX_PRODUCT_NAME_LEN} حرفًا.",
            )
            return
        self._add_or_merge_line(
            product_id=None, code=code, name=name, qty=qty, price=price,
            vat_rate=DEFAULT_VAT_RATE, unit_code=DEFAULT_UNIT_CODE,
        )
        self.item_combo.setCurrentIndex(0)
        self.item_code_input.clear()
        self.qty_input.setText("1")
        self.price_input.setText("0")
        self._mark_dirty()

    def _add_or_merge_line(self, product_id, code, name, qty, price, vat_rate, unit_code) -> None:
        # Merge only when product + unit price + VAT rate are identical. An
        # unregistered line (product_id None) never merges: two free items are
        # only ever equal by accident, so each keeps its own row.
        for r in range(self.lines_table.rowCount()):
            meta = self.lines_table.item(r, COL_CODE).data(Qt.UserRole)
            if (meta and product_id is not None and meta["product_id"] == product_id
                    and meta["price"] == price and meta["vat_rate"] == vat_rate):
                meta["qty"] = meta["qty"] + qty
                self._write_row(r, meta)
                self._refresh_totals()
                return
        meta = {
            "product_id": product_id, "product_code": code, "product_name": name,
            "unit_code": unit_code, "qty": qty, "price": price, "vat_rate": vat_rate,
            "line_id": None,
        }
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)
        for c in range(8):
            self.lines_table.setItem(r, c, QTableWidgetItem(""))
        self._write_row(r, meta)
        self._refresh_totals()

    def _write_row(self, r: int, meta: dict) -> None:
        amounts = self.service.compute_line_amounts(meta["qty"], meta["price"], meta["vat_rate"])
        meta["before"] = amounts["line_amount_before_vat"]
        meta["vat"] = amounts["vat_amount"]
        meta["total"] = amounts["line_total_including_vat"]
        editable = self._mode in ("new", "edit")
        # The code/name of a registered product are its own: they stay read-only
        # and only an unregistered line lets you type them.
        is_free = meta.get("product_id") is None
        self._suspend_cell_signal = True
        try:
            cells = {
                COL_CODE: meta["product_code"],
                COL_NAME: meta["product_name"],
                COL_QTY: self._fmt_qty(meta["qty"]),
                COL_PRICE: self._fmt_qty(meta["price"]),
                COL_BEFORE: f"{meta['before']:,.2f}",
                COL_VAT_RATE: f"{meta['vat_rate']:g}%",
                COL_VAT: f"{meta['vat']:,.2f}",
                COL_TOTAL: f"{meta['total']:,.2f}",
            }
            editable_cols = {COL_QTY, COL_PRICE} | ({COL_CODE, COL_NAME} if is_free else set())
            for c, text in cells.items():
                item = self.lines_table.item(r, c)
                item.setText(text)
                item.setTextAlignment(Qt.AlignCenter)
                if c in editable_cols and editable:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                else:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.lines_table.item(r, COL_CODE).setData(Qt.UserRole, meta)
        finally:
            self._suspend_cell_signal = False

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._suspend_cell_signal or col not in (COL_QTY, COL_PRICE, COL_CODE, COL_NAME):
            return
        # The cells can already be gone when the signal arrives (row removal, or
        # the table being torn down), so never assume they are still there.
        code_item = self.lines_table.item(row, COL_CODE)
        value_item = self.lines_table.item(row, col)
        if code_item is None or value_item is None:
            return
        meta = code_item.data(Qt.UserRole)
        if not meta:
            return
        text = value_item.text()
        if col in (COL_CODE, COL_NAME):
            self._edit_free_item_text(row, col, meta, text)
            return
        try:
            value = self._parse(text, "القيمة")
            if col == COL_QTY and value <= 0:
                raise ValueError("الكمية يجب أن تكون أكبر من صفر.")
            if col == COL_PRICE and value < 0:
                raise ValueError("السعر لا يمكن أن يكون سالبًا.")
        except ValueError as exc:
            QMessageBox.warning(self, "قيمة غير صالحة", str(exc))
            self._write_row(row, meta)  # revert to previous good value
            return
        meta["qty" if col == COL_QTY else "price"] = value
        self._write_row(row, meta)
        self._refresh_totals()
        self._mark_dirty()

    def _edit_free_item_text(self, row: int, col: int, meta: dict, text: str) -> None:
        """Apply a typed code/name to an unregistered line.

        A registered line can't get here (its cells aren't editable), but guard
        anyway so a product's own identity can never be rewritten on its invoice.
        """
        if meta.get("product_id") is not None:
            self._write_row(row, meta)
            return
        value = (text or "").strip()
        limit = MAX_PRODUCT_CODE_LEN if col == COL_CODE else MAX_PRODUCT_NAME_LEN
        if len(value) > limit:
            QMessageBox.warning(
                self, "قيمة غير صالحة",
                f"{'رقم الصنف' if col == COL_CODE else 'اسم الصنف'} يجب ألا يزيد عن {limit} حرفًا.",
            )
            self._write_row(row, meta)  # revert to the previous good value
            return
        meta["product_code" if col == COL_CODE else "product_name"] = value
        self._write_row(row, meta)
        self._mark_dirty()

    def _on_remove_line(self) -> None:
        row = self.lines_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "تنبيه", "اختر سطرًا لحذفه.")
            return
        self.lines_table.removeRow(row)
        self._refresh_totals()
        self._mark_dirty()

    def _refresh_totals(self) -> None:
        lines = []
        for r in range(self.lines_table.rowCount()):
            meta = self.lines_table.item(r, COL_CODE).data(Qt.UserRole)
            if meta:
                lines.append({"line_amount_before_vat": meta["before"], "vat_amount": meta["vat"]})
        totals = self.service.compute_totals(lines)
        self.items_total_value.setText(f"{totals['subtotal_before_vat']:,.2f}")
        self.subtotal_value.setText(f"{totals['subtotal_before_vat']:,.2f}")
        self.vat_value.setText(f"{totals['vat_total']:,.2f}")
        self.total_value.setText(f"{totals['total_including_vat']:,.2f}")

    def _collect_lines(self) -> list[dict[str, Any]]:
        lines = []
        for r in range(self.lines_table.rowCount()):
            meta = self.lines_table.item(r, COL_CODE).data(Qt.UserRole)
            if meta:
                lines.append({
                    "product_id": meta["product_id"],
                    # Carried for unregistered lines (product_id None), where the
                    # typed code/name are all the invoice will ever store.
                    "product_code": meta.get("product_code"),
                    "product_name": meta.get("product_name"),
                    "quantity": meta["qty"],
                    "unit_price": meta["price"],
                    "vat_rate": meta["vat_rate"],
                    "unit_code": meta.get("unit_code"),
                })
        return lines

    # ======================================================================
    # Parsing / formatting helpers
    # ======================================================================
    @staticmethod
    def _parse(text: str, field: str) -> Decimal:
        raw = (text or "").strip().replace(",", "")
        if raw == "":
            raise ValueError(f"{field} مطلوب.")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"{field}: قيمة رقمية غير صالحة.") from exc
        if not value.is_finite():
            raise ValueError(f"{field}: قيمة رقمية غير صالحة.")
        return value

    @staticmethod
    def _fmt_qty(value: Decimal) -> str:
        normalized = value.normalize()
        text = format(normalized, "f")
        return text

    def _mark_dirty(self, *_a) -> None:
        self._dirty = True

    # ======================================================================
    # Modes
    # ======================================================================
    def _set_form_editable(self, editable: bool) -> None:
        self.invoice_number_input.setReadOnly(not editable)
        self.issue_datetime_input.setEnabled(editable)
        self.seller_combo.setEnabled(editable)
        self.customer_combo.setEnabled(editable)
        self.payment_combo.setEnabled(editable)
        self.notes_input.setReadOnly(not editable)
        for button in self._lookup_buttons:
            button.setEnabled(editable)
        self.item_combo.setEnabled(editable)
        self.item_code_input.setReadOnly(not editable)
        self.qty_input.setReadOnly(not editable)
        self.price_input.setReadOnly(not editable)
        self.add_line_button.setEnabled(editable)
        self.new_line_button.setEnabled(editable)
        self.remove_line_button.setEnabled(editable)
        self.lines_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
            if editable else QAbstractItemView.NoEditTriggers
        )

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        is_new = mode == "new"
        is_edit = mode == "edit"
        is_view = mode == "view"
        editable = is_new or is_edit
        approved = self._current_status == STATUS_APPROVED

        self._set_form_editable(editable)

        has_current = self._current is not None
        has_draft = has_current and self._current_status == STATUS_DRAFT
        # Three resting states, which differ only in what the toolbar offers:
        #   idle          - nothing loaded            -> only New
        #   just saved    - the saved invoice on view -> only New (start the next one)
        #   picked/search - an invoice was selected   -> Edit + Save, no New
        # So saving always hands the toolbar back to its default shape, whether
        # the invoice was newly created or re-saved after a search.
        at_rest = is_view and (not has_current or self._post_save)
        self.new_button.setEnabled(at_rest)
        # "تكرار الفاتورة" copies whatever view mode is resting on: the invoice
        # just saved, one searched up, or (idle) the newest one. It is off while
        # typing a new invoice or editing one, so it can never overwrite entry in
        # progress — same gate as "حذف الكل".
        self.duplicate_button.setEnabled(is_view)
        # "Save" is live while creating/editing, and while a *searched* invoice is
        # on screen so it can be edited and re-saved from the same button. Saving
        # closes it again.
        self.save_button.setEnabled(editable or (is_view and has_current and not self._post_save))
        self.edit_button.setEnabled(is_view and has_draft and not self._post_save)
        self.update_button.setEnabled(is_edit)
        self.delete_button.setEnabled(is_view and has_draft)
        self.delete_all_button.setEnabled(is_view)
        # "Cancel" is always live: it discards the entry being typed, reverts an
        # edit, or clears the screen back to idle.
        self.cancel_button.setEnabled(True)
        self.back_button.setEnabled(True)

        # Approval stays disabled until a real Phase-2 approval service exists.
        approve_ok = self.service.is_approval_available() and is_view and has_draft
        self.approve_button.setEnabled(approve_ok)
        if not self.service.is_approval_available():
            self.approve_button.setToolTip(APPROVE_DISABLED_TOOLTIP)

        # Re-apply editable flags to existing rows (qty/price editability).
        self._suspend_cell_signal = True
        try:
            for r in range(self.lines_table.rowCount()):
                meta = self.lines_table.item(r, COL_CODE).data(Qt.UserRole)
                if meta:
                    self._write_row(r, meta)
        finally:
            self._suspend_cell_signal = False

    def _apply_status_badge(self) -> None:
        status = self._current_status
        self.doc_status_input.setText(STATUS_LABELS_AR.get(status, status))
        self.status_badge.setText(STATUS_LABELS_AR.get(status, status))
        if status == STATUS_APPROVED:
            fg, bg = "#065F46", "#A7F3D0"
        else:
            fg, bg = "#374151", "#E5E7EB"
        self.status_badge.setStyleSheet(
            "QLabel#statusBadge { color:%s; background:%s; border-radius:12px; "
            "padding:6px 16px; font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:12px; font-weight:800; min-width:110px; }"
            % (fg, bg)
        )

    def _clear_form(self) -> None:
        self.invoice_number_input.clear()
        self.issue_datetime_input.setDateTime(QDateTime.currentDateTime())
        self.seller_combo.setCurrentIndex(0)
        self.customer_combo.setCurrentIndex(0)
        self.payment_combo.setCurrentIndex(0)
        self.seller_vat_input.clear()
        self.seller_cr_input.clear()
        self.customer_vat_input.clear()
        self.notes_input.clear()
        self._suspend_cell_signal = True
        self.lines_table.setRowCount(0)
        self._suspend_cell_signal = False
        for field in self._zatca_fields.values():
            field.clear()
        self._refresh_totals()
        self._dirty = False

    def enter_ready_mode(self) -> None:
        """Idle, fully-locked state: nothing loaded, only "New" is actionable.

        Every field stays read-only here; the user must press "New" (to create a
        draft) or select then "Edit" an invoice before any input is accepted.
        Pressing "Save" returns to this state so "New" re-opens for the next one.
        """
        self._current = None
        self._current_status = STATUS_DRAFT
        self._post_save = False
        self._clear_form()
        self._apply_status_badge()
        self._set_mode("view")
        self._dirty = False

    def enter_new_mode(self) -> None:
        self._current = None
        self._current_status = STATUS_DRAFT
        self._post_save = False
        self._clear_form()
        self._apply_status_badge()
        self._set_mode("new")
        self._dirty = False

    def enter_view_mode(self) -> None:
        self._set_mode("view")

    def enter_edit_mode(self) -> None:
        if self._current is None or self._current_status != STATUS_DRAFT:
            return
        self._post_save = False
        self._set_mode("edit")

    # ======================================================================
    # Load an existing invoice
    # ======================================================================
    def load_invoice(self, invoice_id: int, *, post_save: bool = False) -> None:
        loaded = self._safe(lambda: self.service.load(invoice_id))
        if not loaded:
            QMessageBox.warning(self, "غير موجودة", "تعذّر تحميل الفاتورة.")
            return
        self._post_save = post_save
        self._current = loaded
        header = loaded["header"]
        self._current_status = header["document_status"]

        self.invoice_number_input.setText(header["invoice_number"] or "")
        issued = header["issue_datetime"]
        if issued is not None:
            self.issue_datetime_input.setDateTime(
                QDateTime(QDate(issued.year, issued.month, issued.day),
                          QTime(issued.hour, issued.minute, issued.second))
            )
        seller = {"id": header["seller_company_id"], "name_ar": header["seller_name_ar_snapshot"],
                  "name_en": header["seller_name_en_snapshot"], "vat_number": header["seller_vat_number_snapshot"]}
        self._ensure_combo_record(self.seller_combo, seller, header["seller_name_ar_snapshot"], "id")
        self.seller_vat_input.setText(header["seller_vat_number_snapshot"] or "")
        # CR is not part of the invoice snapshot; pull it from the company record.
        seller_master = self._safe(lambda: self.service.get_company(header["seller_company_id"])) or {}
        self.seller_cr_input.setText(str(seller_master.get("commercial_registration") or ""))
        customer = {"customer_id": header["customer_id"], "customer_name": header["customer_name_snapshot"],
                    "vat_number": header["customer_vat_number_snapshot"], "address": header["customer_address_snapshot"]}
        self._ensure_combo_record(self.customer_combo, customer,
                                  (header["customer_name_snapshot"] or "") + f"  ({header['customer_id']})",
                                  "customer_id")
        self.customer_vat_input.setText(header["customer_vat_number_snapshot"] or "")
        payment_index = self.payment_combo.findData(header["payment_type"])
        if payment_index >= 0:
            self.payment_combo.setCurrentIndex(payment_index)
        self.notes_input.setPlainText(header["notes"] or "")

        # Lines.
        self._suspend_cell_signal = True
        self.lines_table.setRowCount(0)
        self._suspend_cell_signal = False
        for line in loaded["lines"]:
            meta = {
                "product_id": line["product_id"],
                "product_code": line["product_code_snapshot"],
                "product_name": line["product_name_snapshot"],
                "unit_code": line["unit_code"],
                "qty": Decimal(str(line["quantity"])),
                "price": Decimal(str(line["unit_price"])),
                "vat_rate": Decimal(str(line["vat_rate"])),
                "line_id": line["id"],
            }
            r = self.lines_table.rowCount()
            self.lines_table.insertRow(r)
            for c in range(8):
                self.lines_table.setItem(r, c, QTableWidgetItem(""))
            self._write_row(r, meta)
        self._refresh_totals()

        # The Phase-2 fields are filled, but the section is never opened on the
        # user's behalf: it expands only when they click the header themselves.
        self._populate_phase2(loaded.get("zatca"))
        self._apply_status_badge()
        # Approved invoices are locked; drafts open in read-only view until Edit.
        self.enter_view_mode()
        self._dirty = False

    def _populate_phase2(self, zatca: dict | None) -> None:
        for column, field in self._zatca_fields.items():
            value = "" if not zatca else zatca.get(column)
            if isinstance(value, datetime.datetime):
                value = value.strftime("%Y-%m-%d %H:%M")
            field.setText("" if value is None else str(value))

    def _toggle_phase2(self) -> None:
        self._phase2_expanded = not self._phase2_expanded
        arrow = "▾" if self._phase2_expanded else "▸"
        self.phase2_toggle.setText(f"{arrow}   بيانات المرحلة الثانية")
        self.phase2_body.setVisible(self._phase2_expanded)

    # ======================================================================
    # Actions
    # ======================================================================
    def _current_user_id(self) -> int | None:
        user = SESSION.user or {}
        try:
            return int(user["id"]) if user.get("id") is not None else None
        except (KeyError, TypeError, ValueError):
            return None

    def _collect_form(self) -> dict[str, Any]:
        seller = self.seller_combo.currentData()
        customer = self.customer_combo.currentData()
        issued = self.issue_datetime_input.dateTime().toPython()
        if isinstance(issued, datetime.datetime) and issued.tzinfo is None:
            issued = issued.astimezone()  # attach local tz -> tz-aware storage
        return {
            "invoice_number": self.invoice_number_input.text(),
            "issue_datetime": issued,
            "seller_company_id": seller.get("id") if isinstance(seller, dict) else None,
            "customer_id": customer.get("customer_id") if isinstance(customer, dict) else None,
            "payment_type": self.payment_combo.currentData(),
            "notes": self.notes_input.toPlainText(),
            "lines": self._collect_lines(),
        }

    def on_new(self) -> None:
        if self._dirty and not self._confirm_discard():
            return
        self.enter_new_mode()

    def _invoice_to_copy(self) -> Any:
        """The invoice "تكرار" copies: the one on screen, else the newest saved.

        After a save the invoice stays on screen (see _stay_on_saved), so that is
        normally the one just entered. Falling back to the newest row keeps the
        button working from the idle screen too — including right after opening
        the app, where nothing is loaded yet.
        """
        header = self._current.get("header") if isinstance(self._current, dict) else None
        if isinstance(header, dict) and header.get("id") is not None:
            return header["id"]
        rows = self._safe(lambda: self.service.search_invoices("", None, 1))
        first = rows[0] if rows else None
        return first.get("id") if isinstance(first, dict) else None

    def on_duplicate(self) -> None:
        """Open a new draft pre-filled from the previous invoice.

        Speeds up entry when the next invoice is the same customer, seller and
        items, and only quantities/prices change. Everything is copied *except*
        the three things that must never be inherited:
          * the invoice number — unique per seller, so it is typed by hand;
          * the timestamp — a new invoice is issued now, not when the source was;
          * the Phase-2 data (UUID / ICV / PIH / hash / QR) — generated at save.
            Copying it would give two invoices one identity.
        """
        if self._dirty and not self._confirm_discard():
            return
        source_id = self._invoice_to_copy()
        if source_id is None:
            QMessageBox.information(
                self, "تكرار الفاتورة", "لا توجد فاتورة سابقة لتكرارها."
            )
            return
        self.load_invoice(source_id)
        if self._current is None:
            return  # load_invoice already reported why
        self._detach_as_new_draft()

    def _detach_as_new_draft(self) -> None:
        """Turn the loaded invoice on screen into an unsaved copy of itself."""
        self._current = None  # nothing loaded any more -> Save creates, not updates
        self._current_status = STATUS_DRAFT  # a copy of an approved invoice is a draft
        self._post_save = False
        self.invoice_number_input.clear()
        self.issue_datetime_input.setDateTime(QDateTime.currentDateTime())
        self._populate_phase2(None)
        # The rows still carry the source's line ids; they belong to that invoice,
        # not this copy. _collect_lines never sends them, but leaving them behind
        # would hand the next reader a lie.
        self._suspend_cell_signal = True
        try:
            for r in range(self.lines_table.rowCount()):
                item = self.lines_table.item(r, COL_CODE)
                meta = item.data(Qt.UserRole) if item else None
                if meta:
                    meta["line_id"] = None
                    item.setData(Qt.UserRole, meta)
        finally:
            self._suspend_cell_signal = False
        self._apply_status_badge()
        self._set_mode("new")
        # The screen now holds a populated, unsaved invoice: leaving it loses
        # real work, so it counts as dirty even though the user typed none of it.
        self._dirty = True
        self.invoice_number_input.setFocus()

    def on_save(self) -> None:
        # "Save" doubles as create (nothing loaded) and update (an invoice is
        # selected), so a picked invoice can be edited and re-saved from the same
        # button. Either way the invoice **stays on screen** afterwards — see
        # _stay_on_saved.
        if self._current is None:
            self._save_new()
        else:
            self._save_existing()

    def _stay_on_saved(self, invoice_id: Any) -> None:
        """Keep the just-saved invoice on screen instead of clearing the form.

        Saving used to drop back to the idle state, which threw away the invoice
        the user had just typed: previewing or printing it meant searching it back
        up first. So the invoice stays loaded, read-only, ready for معاينة /
        طباعة, and "New" starts the next one when the user asks for it.

        It is re-read from the database rather than left as typed, so the screen
        shows exactly what was persisted — including the Phase-2 data generated on
        save (UUID / ICV / QR), which is what the print templates put in the QR.
        Re-reading also refreshes ``row_version``, without which a second
        Edit → Update on the same invoice would fail the optimistic-concurrency
        check.
        """
        if invoice_id is None:
            # No id came back (shouldn't happen) — fall back to the old idle state
            # rather than leave a stale form claiming to be saved.
            self.enter_ready_mode()
            return
        self.load_invoice(invoice_id, post_save=True)

    @staticmethod
    def _saved_invoice_id(saved: Any) -> Any:
        header = (saved or {}).get("header") if isinstance(saved, dict) else None
        return header.get("id") if isinstance(header, dict) else None

    def _save_new(self) -> None:
        try:
            saved = self.service.create_draft(self._collect_form(), user_id=self._current_user_id())
        except SaudiSalesInvoiceError as exc:
            QMessageBox.warning(self, "تعذّر الحفظ", exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        QMessageBox.information(self, "تم", "تم حفظ الفاتورة كمسودة.")
        self._stay_on_saved(self._saved_invoice_id(saved))

    def _save_existing(self) -> None:
        row_version = self._current["header"]["row_version"]
        invoice_id = self._current["header"]["id"]
        try:
            self.service.update_draft(invoice_id, row_version, self._collect_form(),
                                      user_id=self._current_user_id())
        except SaudiSalesInvoiceError as exc:
            QMessageBox.warning(self, "تعذّر التحديث", exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        QMessageBox.information(self, "تم", "تم تحديث الفاتورة.")
        self._stay_on_saved(invoice_id)

    def on_edit(self) -> None:
        self.enter_edit_mode()

    def on_update(self) -> None:
        if self._current is None:
            return
        row_version = self._current["header"]["row_version"]
        invoice_id = self._current["header"]["id"]
        try:
            self.service.update_draft(invoice_id, row_version, self._collect_form(),
                                      user_id=self._current_user_id())
        except SaudiSalesInvoiceError as exc:
            QMessageBox.warning(self, "تعذّر التحديث", exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        QMessageBox.information(self, "تم", "تم تحديث الفاتورة.")
        self._stay_on_saved(invoice_id)

    def on_approve(self) -> None:
        QMessageBox.information(self, "الاعتماد", APPROVE_DISABLED_TOOLTIP)

    def on_delete(self) -> None:
        if self._current is None:
            return
        if QMessageBox.question(self, "تأكيد الحذف", "هل تريد حذف هذه المسودة؟") != QMessageBox.Yes:
            return
        try:
            self.service.delete_draft(self._current["header"]["id"], user_id=self._current_user_id())
        except SaudiSalesInvoiceError as exc:
            QMessageBox.warning(self, "تعذّر الحذف", exc.message)
            return
        QMessageBox.information(self, "تم", "تم حذف المسودة.")
        self.enter_ready_mode()

    def on_delete_all(self) -> None:
        confirm = QMessageBox.question(
            self, "تأكيد حذف الكل",
            "سيتم حذف جميع المسودات القابلة للحذف فقط (لن تُحذف الفواتير المعتمدة أو التي تحتوي بيانات "
            "المرحلة الثانية). هل تريد المتابعة؟",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            result = self.service.delete_all_drafts(user_id=self._current_user_id())
        except SaudiSalesInvoiceError as exc:
            QMessageBox.warning(self, "تعذّر الحذف", exc.message)
            return
        QMessageBox.information(
            self, "تم",
            f"تم حذف {result['deleted']} مسودة.\nتم تجاوز {result['protected']} فاتورة محمية.",
        )
        self.enter_ready_mode()

    def on_cancel(self) -> None:
        """Discard whatever the screen is holding.

        Mid-entry (new/edit) it throws away the unsaved changes: an invoice that
        was loaded comes back as stored, a brand-new one leaves nothing behind.
        From a resting state there is nothing to revert, so it clears the screen
        back to idle — which is also how you put down an invoice you searched up.
        """
        if self._dirty and not self._confirm_discard():
            return
        if self._mode == "edit" and self._current is not None:
            self.load_invoice(self._current["header"]["id"])
        else:
            self.enter_ready_mode()

    def on_back(self) -> None:
        # The dirty-discard guard and the reset-to-idle both live in closeEvent,
        # so this (and the window's native "X") share one path.
        window = self.window()
        if window is not None:
            window.close()

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt close event
        # The main window caches this screen and merely hides it on close, so
        # reset to the idle/default state here — that way reopening the screen
        # always starts clean (nothing loaded, only "New" actionable) rather
        # than showing the previously loaded invoice.
        if self._dirty and not self._confirm_discard():
            event.ignore()
            return
        super().closeEvent(event)
        self.enter_ready_mode()

    # ======================================================================
    # Preview / export PDF
    # ======================================================================
    def _collect_preview_data(self) -> dict[str, Any]:
        """Gather the current on-screen invoice into the print template's shape.

        Works for both saved and unsaved invoices, so a draft can be previewed
        before saving. Party details that are *not* typed on this screen — the
        commercial registration and the address — are pulled automatically from
        the selected company / customer master record (never entered by hand).
        """
        seller_rec = self.seller_combo.currentData()
        seller_rec = seller_rec if isinstance(seller_rec, dict) else {}
        customer_rec = self.customer_combo.currentData()
        customer_rec = customer_rec if isinstance(customer_rec, dict) else {}

        # Auto-fetch the full master record so CR + address are always current,
        # even for a loaded invoice whose combo only carries name/vat snapshots.
        seller_full = self._safe(lambda: self.service.get_company(seller_rec.get("id"))) or {}
        customer_full = self._safe(lambda: self.service.get_customer(customer_rec.get("customer_id"))) or {}
        seller = {**seller_rec, **seller_full}
        customer = {**customer_rec, **customer_full}

        lines: list[dict[str, Any]] = []
        subtotal = Decimal("0")
        vat_total = Decimal("0")
        grand_total = Decimal("0")
        for r in range(self.lines_table.rowCount()):
            meta = self.lines_table.item(r, COL_CODE).data(Qt.UserRole)
            if not meta:
                continue
            before = meta.get("before", Decimal("0"))
            vat_amount = meta.get("vat", Decimal("0"))
            total = meta.get("total", Decimal("0"))
            subtotal += before
            vat_total += vat_amount
            grand_total += total
            lines.append({
                "code": meta.get("product_code"),
                "name": meta.get("product_name"),
                "qty": meta.get("qty"),
                "unit": meta.get("unit_code"),
                "price": meta.get("price"),
                "before": before,
                "vat_amount": vat_amount,
                "vat_rate": meta.get("vat_rate"),
                "total": total,
            })

        issued = self.issue_datetime_input.dateTime().toPython()

        data = {
            "invoice_number": self.invoice_number_input.text().strip(),
            "issue_datetime": issued,
            "payment_label": self.payment_combo.currentText(),
            "seller": {
                "name": seller.get("name_ar") or "",
                "name_en": seller.get("name_en") or "",
                "address": seller.get("address_ar") or seller.get("address") or "",
                "address_en": seller.get("address_en") or seller.get("address") or "",
                "vat": seller.get("vat_number") or self.seller_vat_input.text().strip(),
                "cr": seller.get("commercial_registration") or seller.get("cr") or "",
                "logo": seller.get("logo"),
                "logo_mime": seller.get("logo_mime"),
            },
            "customer": {
                # The master-record number, printed by النموذج السابع.
                "code": customer.get("customer_id") or "",
                "name": customer.get("customer_name") or "",
                "address": customer.get("address") or "",
                "vat": customer.get("vat_number") or self.customer_vat_input.text().strip(),
                "cr": customer.get("cr") or customer.get("commercial_registration") or "",
            },
            "lines": lines,
            "totals": {"subtotal": subtotal, "vat": vat_total, "total": grand_total},
        }
        data["qr_payload"] = self._resolve_qr_payload(data)
        return data

    def _resolve_qr_payload(self, data: dict[str, Any]) -> str | None:
        """Prefer the saved invoice's Phase-2 QR; otherwise build one (with the
        invoice-hash tag) from the current form so drafts scan as compatible too."""
        zatca = self._current.get("zatca") if self._current else None
        if isinstance(zatca, dict) and zatca.get("qr_code_base64"):
            return zatca["qr_code_base64"]
        payload = self._safe(lambda: self.service.build_preview_qr(data))
        if payload:
            return payload
        # Last-resort fallback: a tags 1–5 QR (still renders, just not Phase-2).
        issued = data.get("issue_datetime")
        timestamp = issued if isinstance(issued, datetime.datetime) else datetime.datetime.now()
        try:
            return zatca_gen.build_qr(
                seller_name=data["seller"]["name"],
                vat_number=data["seller"]["vat"],
                timestamp=timestamp,
                total_with_vat=data["totals"]["total"],
                vat_total=data["totals"]["vat"],
            )
        except Exception:  # noqa: BLE001 - QR is decorative here; never block preview
            return None

    def _choose_invoice_builder(self, action_label: str) -> "Callable[[dict[str, Any]], str] | None":
        """Ask which invoice layout to use; return its HTML builder or ``None``."""
        from app.ui.screens.saudi_invoice_print import (
            build_invoice_html,
            choose_invoice_template,
        )
        from app.ui.screens.saudi_invoice_print_v2 import build_invoice_html_v2
        from app.ui.screens.saudi_invoice_print_v3 import build_invoice_html_v3
        from app.ui.screens.saudi_invoice_print_v4 import build_invoice_html_v4
        from app.ui.screens.saudi_invoice_print_v5 import build_invoice_html_v5
        from app.ui.screens.saudi_invoice_print_v6 import build_invoice_html_v6
        from app.ui.screens.saudi_invoice_print_v7 import build_invoice_html_v7
        from app.ui.screens.saudi_invoice_print_v8 import build_invoice_html_v8

        choice = choose_invoice_template(self, action_label)
        if choice is None:
            return None
        return {
            1: build_invoice_html,
            2: build_invoice_html_v2,
            3: build_invoice_html_v3,
            4: build_invoice_html_v4,
            5: build_invoice_html_v5,
            6: build_invoice_html_v6,
            7: build_invoice_html_v7,
            8: build_invoice_html_v8,
        }.get(choice, build_invoice_html)

    def on_preview(self) -> None:
        from app.ui.screens.saudi_invoice_print import SaudiInvoicePreviewDialog

        builder = self._choose_invoice_builder("المعاينة")
        if builder is None:
            return
        data = self._collect_preview_data()
        dialog = SaudiInvoicePreviewDialog(data, parent=self, html_builder=builder)
        dialog.exec()

    def on_export_pdf(self) -> None:
        from app.ui.screens.saudi_invoice_print import export_invoice_to_pdf

        builder = self._choose_invoice_builder("تصدير PDF")
        if builder is None:
            return
        data = self._collect_preview_data()
        export_invoice_to_pdf(self, data, html_builder=builder)

    # ======================================================================
    # Receipt-voucher print (سند قبض)
    # ======================================================================
    def _collect_voucher_data(self) -> dict[str, Any]:
        """Reshape the current invoice into the receipt-voucher template's shape.

        Reuses :meth:`_collect_preview_data` so the seller / customer master
        lookups and the grand total stay defined in exactly one place. The
        voucher acknowledges receipt, from the customer, of the invoice's
        VAT-inclusive total.
        """
        inv = self._collect_preview_data()
        invoice_number = inv.get("invoice_number") or ""
        # The voucher's own serial is the invoice number + a fixed offset (never
        # the same number). The «وذلك قيمة» purpose line, though, must keep the
        # REAL invoice number — it names which invoice this receipt is for.
        voucher_number = voucher_serial_from_invoice_number(invoice_number)
        return {
            "number": voucher_number,
            "issue_date": inv.get("issue_datetime"),
            "amount": inv.get("totals", {}).get("total", Decimal("0")),
            "received_from": inv.get("customer", {}).get("name") or "",
            "purpose": (
                f"قيمة فاتورة مبيعات رقم {invoice_number}"
                if invoice_number
                else "قيمة فاتورة مبيعات"
            ),
            # «نقدي» / «آجل» — the invoice's own payment label; النموذج الرابع
            # prints it, the other three ignore it.
            "payment_type": inv.get("payment_label") or "",
            "seller": inv.get("seller", {}),
        }

    def on_print_voucher(self) -> None:
        from app.ui.screens.saudi_receipt_voucher_print import (
            SaudiReceiptVoucherPreviewDialog,
            choose_voucher_builder,
        )

        builder = choose_voucher_builder(self)
        if builder is None:
            return
        data = self._collect_voucher_data()
        dialog = SaudiReceiptVoucherPreviewDialog(data, parent=self, html_builder=builder)
        dialog.exec()

    def open_search(self) -> None:
        dialog = SaudiInvoiceSearchDialog(self.service, parent=self)
        if dialog.exec() and dialog.selected_id is not None:
            self.load_invoice(dialog.selected_id)

    def _confirm_discard(self) -> bool:
        return QMessageBox.question(
            self, "تجاهل التغييرات", "هناك تغييرات غير محفوظة. هل تريد تجاهلها؟"
        ) == QMessageBox.Yes

    # ======================================================================
    # Fitting the screen it is actually shown on
    # ======================================================================
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_density()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Regaining focus is the moment the dropdowns go stale: the user has just
        # come back from the customers / products / companies screen, where they
        # registered the very record they now expect to find here.
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self.refresh_master_data()

    def _lay_out_meta(self, *, stacked: bool) -> None:
        """User / date / time: three rows when there is room, one when there isn't."""
        for label in (self.user_label, self.date_label, self.time_label):
            self.meta_grid.removeWidget(label)
        for i, label in enumerate((self.user_label, self.date_label, self.time_label)):
            self.meta_grid.addWidget(label, i, 0) if stacked else \
                self.meta_grid.addWidget(label, 0, i)

    def _apply_density(self) -> None:
        """Trade chrome for invoice lines when the screen is short.

        The items table is the only thing here that grows, so on a short screen
        every fixed pixel above it costs a line the user came to read. Measured on
        a maximised 1366x768 window (the page gets 680px once the title bar and
        taskbar take theirs): the chrome ate ~390px and left room for **three**
        lines. Compact mode gives back ~90px and shrinks the rows, which turns
        those three into seven — on the same screen, with nothing removed.

        The switch is driven by the page's real height, so it follows the window
        rather than the monitor: restore a window on a 4K screen and it compacts
        too, which is right — what matters is the room the table actually has.
        """
        compact = self.height() < COMPACT_BELOW_HEIGHT
        if compact == self._compact:
            return  # nothing to do; resizeEvent fires on every pixel of a drag
        self._compact = compact

        row_height = COMPACT_ROW_HEIGHT if compact else ROW_HEIGHT
        header = self.lines_table.horizontalHeader()
        header.setFixedHeight(COMPACT_TABLE_HEADER if compact else TABLE_HEADER)
        # This also re-heights the rows already on screen: none of them is ever
        # resized by hand (the vertical header is hidden), so they all follow the
        # default section size. Verified by mutation — a loop doing it manually
        # changed nothing.
        self.lines_table.verticalHeader().setDefaultSectionSize(row_height)
        self.lines_table.setMinimumHeight(header.height() + 3 * row_height)

        # The subtitle is decoration; a line of the invoice is not. The meta lines
        # go side by side — stacked they, not the subtitle, floor the header.
        self.subtitle_label.setVisible(not compact)
        self._lay_out_meta(stacked=not compact)
        self.quick_entry_card.setMaximumHeight(92 if compact else 112)
        for layout in self._cards:
            layout.setContentsMargins(*(COMPACT_CARD_MARGINS if compact else CARD_MARGINS))
            layout.setSpacing(COMPACT_CARD_SPACING if compact else CARD_SPACING)
        self.main_layout.setSpacing(3 if compact else 6)
        self.main_layout.setContentsMargins(12, 3, 12, 4) if compact else \
            self.main_layout.setContentsMargins(12, 6, 12, 8)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key_F1:
            self.open_search()
            return
        super().keyPressEvent(event)


__all__ = ["SaudiSalesInvoicePage"]
