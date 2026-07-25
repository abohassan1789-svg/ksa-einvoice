"""Attachments screen (شاشة المرفقات) — modern, reusable CRM file manager.

UI only. All persistence and business rules live in :class:`AttachmentsService`
(service layer) and :class:`AttachmentsRepository` (database layer). The real
file bytes are stored in the database (BYTEA); previewing/opening/exporting all
read the binary back from the database, never from the original source path.

The screen is generic: it links any CRM record through ``entity_type`` +
``entity_id``. Passing ``entity_type``/``entity_id`` to the constructor (e.g.
opened from a customer) pre-fills new uploads and filters the list to that
record.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.attachment import (
    ALLOWED_EXTENSIONS,
    ATTACHMENT_CATEGORIES,
    ENTITY_TYPES,
    IMAGE_EXTENSIONS,
)
from app.security.session_context import SESSION
from app.services.attachments_service import AttachmentsService, AttachmentValidationError
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK, TEXT

BG = "#F1F5F9"
CARD_BG = "#FFFFFF"
TEXT_MUTED = "#64748B"

# (label, extensions) for the file-type filter dropdown.
FILE_TYPE_FILTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("الكل", ()),
    ("PDF", ("pdf",)),
    ("صور", ("jpg", "jpeg", "png")),
    ("مستندات", ("doc", "docx", "txt")),
    ("Excel", ("xls", "xlsx")),
    ("نصوص", ("txt",)),
)

# Summary cards: (key, title, badge, accent colour).
SUMMARY_CARDS = (
    ("total", "إجمالي المرفقات", "📎", GREEN),
    ("total_size_human", "الحجم الإجمالي", "💾", "#2563EB"),
    ("pdf_count", "ملفات PDF", "📄", "#DC2626"),
    ("image_count", "ملفات الصور", "🖼", "#F59E0B"),
    ("favorite_count", "المفضلة", "★", "#7C3AED"),
)

TABLE_COLUMNS = (
    ("id", "الكود"),
    ("attachment_code", "كود المرفق"),
    ("entity_type", "النوع"),
    ("entity_id", "السجل المرتبط"),
    ("title", "العنوان"),
    ("category", "التصنيف"),
    ("original_file_name", "اسم الملف"),
    ("file_extension", "الامتداد"),
    ("file_size", "الحجم"),
    ("is_favorite", "مفضلة"),
    ("created_at", "تاريخ الإضافة"),
)


def _primary_button(text: str, bg: str, hover: str, fg: str = "#FFFFFF") -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setFixedHeight(38)
    button.setStyleSheet(
        f"QPushButton {{ background:{bg}; color:{fg}; border:none; border-radius:8px; "
        "font-weight:800; padding:7px 14px; }}"
        f"QPushButton:hover {{ background:{hover}; }}"
        "QPushButton:disabled { background:#CBD5E1; color:#F8FAFC; }"
    )
    return button


class _DropZone(QFrame):
    """Modern drag-and-drop area that also accepts a click to choose a file."""

    def __init__(self, on_file, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_file = on_file
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(92)
        self._apply_style(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.label = QLabel("اسحب الملف هنا أو اضغط لاختيار ملف")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:800; border:none; background:transparent;")
        layout.addWidget(self.label)

    def _apply_style(self, active: bool) -> None:
        border = GREEN if active else "#CBD5E1"
        bg = "#ECFDF3" if active else "#F8FAFC"
        self.setStyleSheet(
            f"_DropZone {{ background:{bg}; border:2px dashed {border}; border-radius:10px; }}"
        )

    def set_file_name(self, name: str | None) -> None:
        if name:
            self.label.setText(f"الملف المختار:\n{name}")
            self.label.setStyleSheet(f"color:{GREEN_DARK}; font-size:13px; font-weight:900; border:none; background:transparent;")
        else:
            self.label.setText("اسحب الملف هنا أو اضغط لاختيار ملف")
            self.label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:800; border:none; background:transparent;")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        if event.button() == Qt.LeftButton:
            self._on_file(None)  # None => open the file dialog
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._apply_style(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        self._apply_style(False)

    def dropEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        self._apply_style(False)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._on_file(path)
                event.acceptProposedAction()


class AttachmentsPage(QWidget):
    """Premium dashboard-style attachments manager."""

    def __init__(
        self,
        service: AttachmentsService | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or AttachmentsService()
        self._context_entity_type = entity_type
        self._context_entity_id = entity_id

        self.current_id: int | None = None
        self.mode = "view"  # view | new | edit
        self.pending_file: dict[str, Any] | None = None  # chosen-but-unsaved file
        self.inputs: dict[str, Any] = {}
        self._card_labels: dict[str, QLabel] = {}

        base = "crm.attachments"
        self._perm_create = SESSION.can(f"{base}.create")
        self._perm_edit = SESSION.can(f"{base}.edit")
        self._perm_save = SESSION.can(f"{base}.save")
        self._perm_delete = SESSION.can(f"{base}.delete")
        self._perm_export = SESSION.can(f"{base}.export")

        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(f"AttachmentsPage {{ background:{BG}; }}")
        self._build_ui()
        try:
            self.service.ensure_schema()
        except Exception:  # never block the screen if the DB is momentarily down
            pass
        self.refresh()
        self.set_mode("view")

    # --- layout -------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"background:{BG}; border:none;")
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_cards())
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_status_bar())
        layout.addLayout(self._build_body(), 1)
        scroll.setWidget(content)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background:{GREEN}; border-radius:12px; }} QLabel {{ background:transparent; color:#FFFFFF; }}"
        )
        box = QHBoxLayout(header)
        box.setContentsMargins(20, 16, 20, 16)
        icon = QLabel("📎")
        icon.setFixedSize(54, 54)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            "background:rgba(255,255,255,0.16); border:1px solid rgba(255,255,255,0.28); "
            "border-radius:10px; font-size:26px;"
        )
        text_box = QVBoxLayout()
        title = QLabel("المرفقات")
        title.setStyleSheet("font-size:26px; font-weight:900;")
        subtitle = QLabel("إدارة ملفات ومرفقات العملاء والمتابعة والعقود")
        subtitle.setStyleSheet("font-size:13px; font-weight:700; color:#E8FBEF;")
        text_box.addWidget(title)
        text_box.addWidget(subtitle)
        box.addWidget(icon)
        box.addLayout(text_box, 1)

        if self._context_entity_type:
            tag = QLabel(f"مرتبط بـ: {self._context_entity_type} #{self._context_entity_id or '—'}")
            tag.setStyleSheet(
                "background:rgba(255,255,255,0.16); border:1px solid rgba(255,255,255,0.28); "
                "border-radius:8px; padding:8px 14px; font-weight:800;"
            )
            box.addWidget(tag)
        return header

    def _build_cards(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for index, (key, title, badge, accent) in enumerate(SUMMARY_CARDS):
            grid.addWidget(self._make_card(key, title, badge, accent), 0, index)
            grid.setColumnStretch(index, 1)
        return section

    def _make_card(self, key: str, title: str, badge: str, accent: str) -> QFrame:
        card = QFrame()
        card.setMinimumHeight(92)
        card.setStyleSheet(
            f"background:{CARD_BG}; border:1px solid {BORDER}; border-right:4px solid {accent}; border-radius:10px;"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:800; border:none;")
        badge_label = QLabel(badge)
        badge_label.setAlignment(Qt.AlignCenter)
        badge_label.setFixedSize(28, 28)
        badge_label.setStyleSheet(f"color:{accent}; background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px;")
        top.addWidget(title_label)
        top.addStretch(1)
        top.addWidget(badge_label)
        value = QLabel("0")
        value.setAlignment(Qt.AlignRight)
        value.setStyleSheet(f"color:{TEXT}; font-size:24px; font-weight:900; border:none;")
        box.addLayout(top)
        box.addWidget(value)
        self._card_labels[key] = value
        return card

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(7)

        self.new_button = _primary_button("جديد", "#1A7A3C", "#166534")
        self.save_button = _primary_button("حفظ", "#2563EB", "#1D4ED8")
        self.delete_button = _primary_button("حذف", "#DC2626", "#B91C1C")
        self.clear_button = _primary_button("مسح", "#6B7280", "#4B5563")
        self.choose_button = _primary_button("اختيار ملف", GREEN, GREEN_DARK)
        self.open_button = _primary_button("معاينة / فتح", "#0891B2", "#0E7490")
        self.export_button = _primary_button("تنزيل / تصدير", "#7C3AED", "#6D28D9")
        self.favorite_button = _primary_button("تمييز كمفضلة", "#F59E0B", "#D97706")

        for button in (
            self.new_button, self.save_button, self.delete_button, self.clear_button,
            self.choose_button, self.open_button, self.export_button, self.favorite_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self.mode_badge = QLabel("عرض")
        self.mode_badge.setAlignment(Qt.AlignCenter)
        self.mode_badge.setMinimumWidth(90)
        self.mode_badge.setStyleSheet(
            "background:#ECFDF3; color:#087443; border:1px solid #B7E4C7; border-radius:8px; padding:8px 12px; font-weight:900;"
        )
        row.addWidget(self.mode_badge)

        self.new_button.clicked.connect(self.new_attachment)
        self.save_button.clicked.connect(self.save_attachment)
        self.delete_button.clicked.connect(self.delete_attachment)
        self.clear_button.clicked.connect(self.clear_form)
        self.choose_button.clicked.connect(lambda: self.choose_file(None))
        self.open_button.clicked.connect(self.open_selected)
        self.export_button.clicked.connect(self.export_selected)
        self.favorite_button.clicked.connect(self.toggle_favorite)
        return bar

    def _build_status_bar(self) -> QLabel:
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            "background:transparent; color:#64748B; font-size:13px; font-weight:800; padding:0 4px;"
        )
        return self.status_label

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_left_panel(), 0)
        body.addWidget(self._build_right_panel(), 1)
        return body

    # --- left: upload form + file info + preview ---------------------------
    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(430)
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        box.addWidget(self._build_form_card())
        box.addWidget(self._build_file_info_card())
        box.addWidget(self._build_preview_card(), 1)
        return panel

    def _card_box(self, title: str) -> tuple[QGroupBox, QVBoxLayout]:
        box = QGroupBox(title)
        box.setStyleSheet(
            f"QGroupBox {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; margin-top:14px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:16px; "
            f"padding:0 14px; color:{GREEN}; font-weight:900; }}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)
        return box, layout

    def _field_style(self) -> str:
        return (
            f"QLineEdit, QComboBox, QPlainTextEdit {{ background:#FFFFFF; border:1px solid {GREEN}; "
            f"border-radius:8px; padding:6px 10px; font-size:13px; font-weight:700; color:{TEXT}; }}"
            f"QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border:2px solid {GREEN_DARK}; }}"
            "QLineEdit:read-only { background:#F8FAFC; color:#64748B; }"
            "QComboBox:disabled, QLineEdit:disabled { background:#F8FAFC; color:#94A3B8; }"
        )

    def _build_form_card(self) -> QGroupBox:
        box, layout = self._card_box("بيانات المرفق")
        self.drop_zone = _DropZone(self.choose_file)
        layout.addWidget(self.drop_zone)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)

        self.inputs["attachment_code"] = QLineEdit()
        self.inputs["attachment_code"].setPlaceholderText("يُولَّد تلقائيًا إذا تُرك فارغًا")
        self.inputs["title"] = QLineEdit()
        self.inputs["title"].setPlaceholderText("عنوان المرفق")

        self.inputs["entity_type"] = QComboBox()
        for code, label in ENTITY_TYPES:
            self.inputs["entity_type"].addItem(label, code)
        self.inputs["entity_id"] = QLineEdit()
        self.inputs["entity_id"].setPlaceholderText("كود السجل المرتبط (اختياري)")

        self.inputs["category"] = QComboBox()
        self.inputs["category"].addItem("بدون", None)
        for code, label in ATTACHMENT_CATEGORIES:
            self.inputs["category"].addItem(label, code)

        self.inputs["tags"] = QLineEdit()
        self.inputs["tags"].setPlaceholderText("وسوم مفصولة بفواصل")

        rows = [
            ("كود المرفق", self.inputs["attachment_code"]),
            ("العنوان", self.inputs["title"]),
            ("نوع السجل", self.inputs["entity_type"]),
            ("كود السجل", self.inputs["entity_id"]),
            ("التصنيف", self.inputs["category"]),
            ("الوسوم", self.inputs["tags"]),
        ]
        for index, (label_text, widget) in enumerate(rows):
            widget.setMinimumHeight(36)
            widget.setStyleSheet(self._field_style())
            label = QLabel(label_text)
            label.setStyleSheet(f"font-size:13px; font-weight:900; color:{TEXT};")
            grid.addWidget(label, index, 0)
            grid.addWidget(widget, index, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.inputs["notes"] = QPlainTextEdit()
        self.inputs["notes"].setPlaceholderText("ملاحظات")
        self.inputs["notes"].setFixedHeight(60)
        self.inputs["notes"].setStyleSheet(self._field_style())
        layout.addWidget(self.inputs["notes"])

        self.inputs["is_favorite"] = QCheckBox("إضافة إلى المفضلة")
        self.inputs["is_favorite"].setStyleSheet(f"font-weight:800; color:{TEXT};")
        layout.addWidget(self.inputs["is_favorite"])
        return box

    def _build_file_info_card(self) -> QGroupBox:
        box, layout = self._card_box("معلومات الملف")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._info_labels: dict[str, QLabel] = {}
        rows = [
            ("original_file_name", "اسم الملف الأصلي"),
            ("file_extension", "الامتداد"),
            ("mime_type", "نوع MIME"),
            ("file_size", "الحجم"),
            ("file_hash", "البصمة SHA256"),
        ]
        for index, (key, label_text) in enumerate(rows):
            label = QLabel(label_text)
            label.setStyleSheet(f"font-size:12px; font-weight:900; color:{TEXT_MUTED};")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setStyleSheet(f"font-size:12px; font-weight:800; color:{TEXT};")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(label, index, 0)
            grid.addWidget(value, index, 1)
            self._info_labels[key] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return box

    def _build_preview_card(self) -> QGroupBox:
        box, layout = self._card_box("المعاينة")
        self.preview_label = QLabel("لا يوجد ملف للمعاينة")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumHeight(150)
        self.preview_label.setStyleSheet(
            f"background:#F8FAFC; border:1px solid {BORDER}; border-radius:8px; "
            f"color:{TEXT_MUTED}; font-size:13px; font-weight:800; padding:10px;"
        )
        layout.addWidget(self.preview_label, 1)
        return box

    # --- right: filters + table --------------------------------------------
    def _build_right_panel(self) -> QGroupBox:
        box, layout = self._card_box("قائمة المرفقات")
        layout.addWidget(self._build_filters())

        self.table = QTableWidget()
        self.table.setColumnCount(len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _key, label in TABLE_COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self.load_selected)
        self.table.doubleClicked.connect(lambda *_: self.begin_edit())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; border:1px solid #E2E8F0; "
            "gridline-color:#E5E7EB; font-size:12px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:9px 6px; }}"
            "QTableWidget::item:selected { background:#DDF3E6; color:#111827; }"
        )
        layout.addWidget(self.table, 1)

        # Empty-state overlay label (shown when there are no rows).
        self.empty_label = QLabel("لا توجد مرفقات حتى الآن\nابدأ بإضافة أول مرفق من زر اختيار ملف")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:14px; font-weight:800; padding:16px;")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:800;")
        layout.addWidget(self.count_label)
        return box

    def _build_filters(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("QFrame { background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px; }")
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.filter_search = QLineEdit()
        self.filter_search.setPlaceholderText("بحث: كود، عنوان، اسم ملف، ملاحظات، وسوم")
        self.filter_search.setFixedHeight(34)
        self.filter_search.textChanged.connect(self.refresh_table)

        self.filter_entity = QComboBox()
        self.filter_entity.addItem("كل الأنواع", None)
        for code, label in ENTITY_TYPES:
            self.filter_entity.addItem(label, code)
        self.filter_entity.currentIndexChanged.connect(self.refresh_table)

        self.filter_category = QComboBox()
        self.filter_category.addItem("كل التصنيفات", None)
        for code, label in ATTACHMENT_CATEGORIES:
            self.filter_category.addItem(label, code)
        self.filter_category.currentIndexChanged.connect(self.refresh_table)

        self.filter_type = QComboBox()
        for label, exts in FILE_TYPE_FILTERS:
            self.filter_type.addItem(label, exts)
        self.filter_type.currentIndexChanged.connect(self.refresh_table)

        self.filter_favorite = QCheckBox("المفضلة فقط")
        self.filter_favorite.stateChanged.connect(self.refresh_table)

        self.filter_date_from = self._date_edit()
        self.filter_date_to = self._date_edit()

        clear_button = QPushButton("مسح الفلاتر")
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.setFixedHeight(34)
        clear_button.setStyleSheet(
            "QPushButton { background:#E2E8F0; color:#0F172A; border:none; border-radius:8px; "
            "padding:6px 12px; font-weight:900; } QPushButton:hover { background:#CBD5E1; }"
        )
        clear_button.clicked.connect(self.clear_filters)

        for widget in (self.filter_entity, self.filter_category, self.filter_type):
            widget.setFixedHeight(34)
            widget.setStyleSheet(self._field_style())

        grid.addWidget(self.filter_search, 0, 0, 1, 4)
        grid.addWidget(self._labeled("النوع", self.filter_entity), 1, 0)
        grid.addWidget(self._labeled("التصنيف", self.filter_category), 1, 1)
        grid.addWidget(self._labeled("نوع الملف", self.filter_type), 1, 2)
        grid.addWidget(self._labeled("من تاريخ", self.filter_date_from), 1, 3)
        grid.addWidget(self._labeled("إلى تاريخ", self.filter_date_to), 2, 0)
        grid.addWidget(self.filter_favorite, 2, 1)
        grid.addWidget(clear_button, 2, 3)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        return panel

    def _labeled(self, text: str, widget: QWidget) -> QWidget:
        wrapper = QWidget()
        box = QVBoxLayout(wrapper)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        label = QLabel(text)
        label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px; font-weight:800;")
        box.addWidget(label)
        box.addWidget(widget)
        return wrapper

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setMinimumDate(QDate(2000, 1, 1))
        edit.setSpecialValueText("—")
        edit.setDate(edit.minimumDate())
        edit.setFixedHeight(34)
        edit.setStyleSheet("background:#FFFFFF; border:1px solid #CBD5E1; border-radius:8px; padding:5px;")
        edit.dateChanged.connect(self.refresh_table)
        return edit

    # --- data flow ----------------------------------------------------------
    def refresh(self) -> None:
        self.refresh_cards()
        self.refresh_table()

    def refresh_cards(self) -> None:
        try:
            stats = self.service.get_attachment_statistics()
        except Exception:
            stats = {}
        for key, label in self._card_labels.items():
            value = stats.get(key, 0)
            label.setText(str(value) if value not in (None, "") else "0")

    def _current_filters(self) -> dict[str, Any]:
        filters: dict[str, Any] = {"keyword": self.filter_search.text()}
        if self._context_entity_type:
            filters["entity_type"] = self._context_entity_type
            if self._context_entity_id is not None:
                filters["entity_id"] = self._context_entity_id
        elif self.filter_entity.currentData():
            filters["entity_type"] = self.filter_entity.currentData()
        if self.filter_category.currentData():
            filters["category"] = self.filter_category.currentData()
        exts = self.filter_type.currentData()
        if exts:
            filters["extensions"] = list(exts)
        if self.filter_favorite.isChecked():
            filters["favorite_only"] = True
        if self.filter_date_from.date() != self.filter_date_from.minimumDate():
            filters["date_from"] = self.filter_date_from.date().toString("yyyy-MM-dd")
        if self.filter_date_to.date() != self.filter_date_to.minimumDate():
            filters["date_to"] = self.filter_date_to.date().toString("yyyy-MM-dd")
        return filters

    def refresh_table(self) -> None:
        try:
            rows = self.service.list_attachments(self._current_filters())
        except Exception as exc:
            self._error("تعذر تحميل المرفقات", exc)
            return
        self.table.setRowCount(0)
        for row_index, record in enumerate(rows):
            self.table.insertRow(row_index)
            for col_index, (key, _label) in enumerate(TABLE_COLUMNS):
                item = QTableWidgetItem(self._cell_text(key, record))
                item.setTextAlignment(Qt.AlignCenter)
                if col_index == 0:
                    item.setData(Qt.UserRole, record.get("id"))
                if key == "is_favorite" and record.get("is_favorite"):
                    item.setForeground(Qt.GlobalColor.darkYellow)
                self.table.setItem(row_index, col_index, item)
        self.empty_label.setVisible(not rows)
        self.table.setVisible(bool(rows))
        self.count_label.setText(f"عدد المرفقات: {len(rows)}")

    def _cell_text(self, key: str, record: dict[str, Any]) -> str:
        value = record.get(key)
        if key == "file_size":
            return self.service.format_file_size(value)
        if key == "is_favorite":
            return "★" if value else ""
        if key == "created_at" and value is not None:
            return str(value)[:19]
        return "" if value is None else str(value)

    def load_selected(self) -> None:
        if self.mode in {"new", "edit"}:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        attachment_id = item.data(Qt.UserRole)
        try:
            record = self.service.get_attachment_by_id(attachment_id)
        except Exception as exc:
            self._error("تعذر تحميل المرفق", exc)
            return
        if record:
            self.current_id = int(attachment_id)
            self.pending_file = None
            self._fill_form(record)
            self.set_mode("view")
            self._update_preview(record, file_bytes=None)

    # --- file selection -----------------------------------------------------
    def choose_file(self, path: str | None) -> None:
        if self.mode not in {"new", "edit"}:
            self._info("ابدأ بإضافة مرفق جديد أو تعديل مرفق قبل اختيار ملف.")
            return
        if path is None:
            patterns = " ".join(f"*.{ext}" for ext in ALLOWED_EXTENSIONS)
            path, _ = QFileDialog.getOpenFileName(
                self, "اختيار ملف", "", f"الملفات المسموح بها ({patterns})"
            )
            if not path:
                return
        try:
            meta = self.service.read_file_metadata(path)
            self.service.validate_file(meta["file_extension"], meta["file_size"])
        except AttachmentValidationError as exc:
            self._warn(str(exc))
            return
        except Exception as exc:
            self._error("تعذر قراءة الملف", exc)
            return

        duplicates = self.service.detect_duplicate_file(meta["file_hash"])
        if duplicates:
            names = "، ".join(d.get("attachment_code", "") for d in duplicates[:3])
            answer = QMessageBox.question(
                self, "ملف مكرر",
                f"يوجد ملف مطابق بالفعل ({names}). هل تريد المتابعة وحفظه على أي حال؟",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self.pending_file = meta
        if not self.inputs["title"].text().strip():
            self.inputs["title"].setText(os.path.splitext(meta["original_file_name"])[0])
        self.drop_zone.set_file_name(meta["original_file_name"])
        self._fill_file_info(meta)
        self._update_preview(meta, file_bytes=meta["file_data"])
        self._info(f"تم اختيار الملف: {meta['original_file_name']}")

    # --- actions ------------------------------------------------------------
    def new_attachment(self) -> None:
        if not self._perm_create:
            self._warn("ليس لديك صلاحية الإضافة.")
            return
        self.current_id = None
        self.pending_file = None
        self.clear_form(reset_status=False)
        self.inputs["attachment_code"].setText(self.service.get_next_attachment_code())
        if self._context_entity_type:
            self._select_combo_data(self.inputs["entity_type"], self._context_entity_type)
            if self._context_entity_id is not None:
                self.inputs["entity_id"].setText(str(self._context_entity_id))
        self.set_mode("new")
        self._info("سجل مرفق جديد — اختر ملفًا ثم اضغط حفظ.")

    def save_attachment(self) -> None:
        if not self._perm_save:
            self._warn("ليس لديك صلاحية الحفظ.")
            return
        data = self._collect_form()
        try:
            if self.mode == "new":
                if not self.pending_file:
                    self._warn("يجب اختيار ملف قبل الحفظ.")
                    return
                data.update(self._file_fields(self.pending_file))
                result = self.service.create_attachment(data, self.pending_file["file_data"])
                self.current_id = result["id"]
                self._info("تم حفظ المرفق بنجاح.")
            else:
                file_bytes = None
                if self.pending_file:
                    data.update(self._file_fields(self.pending_file))
                    file_bytes = self.pending_file["file_data"]
                self.service.update_attachment(self.current_id, data, file_bytes)
                self._info("تم تحديث المرفق بنجاح.")
            self.pending_file = None
            self.set_mode("view")
            self.refresh()
            self._select_row_by_id(self.current_id)
        except AttachmentValidationError as exc:
            self._warn(str(exc))
        except Exception as exc:
            self._error("تعذر حفظ المرفق", exc)

    def delete_attachment(self) -> None:
        if self.current_id is None:
            return
        if not self._perm_delete:
            self._warn("ليس لديك صلاحية الحذف.")
            return
        answer = QMessageBox.question(
            self, "تأكيد الحذف", "هل تريد حذف المرفق المحدد؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.service.delete_attachment(self.current_id)
            self.current_id = None
            self.clear_form(reset_status=False)
            self.set_mode("view")
            self.refresh()
            self._info("تم حذف المرفق بنجاح.")
        except Exception as exc:
            self._error("تعذر حذف المرفق", exc)

    def toggle_favorite(self) -> None:
        if self.current_id is None:
            self._warn("اختر مرفقًا أولًا.")
            return
        record = self.service.get_attachment_by_id(self.current_id)
        if not record:
            return
        new_value = not bool(record.get("is_favorite"))
        try:
            self.service.set_favorite(self.current_id, new_value)
            self.inputs["is_favorite"].setChecked(new_value)
            self.refresh()
            self._select_row_by_id(self.current_id)
            self._info("تم تحديث حالة المفضلة.")
        except Exception as exc:
            self._error("تعذر تحديث المفضلة", exc)

    def open_selected(self) -> None:
        payload = self._require_payload()
        if payload is None:
            return
        try:
            suffix = f".{payload.get('file_extension')}" if payload.get("file_extension") else ""
            fd, temp_path = tempfile.mkstemp(prefix="crm_att_", suffix=suffix)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload["file_data"])
            QDesktopServices.openUrl(QUrl.fromLocalFile(temp_path))
            self._info("تم فتح الملف في التطبيق الافتراضي.")
        except Exception as exc:
            self._error("تعذر فتح الملف", exc)

    def export_selected(self) -> None:
        if not self._perm_export:
            self._warn("ليس لديك صلاحية التصدير.")
            return
        if self.current_id is None:
            self._warn("اختر مرفقًا أولًا.")
            return
        record = self.service.get_attachment_by_id(self.current_id)
        if not record:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "تصدير الملف", record.get("original_file_name", "attachment")
        )
        if not target:
            return
        try:
            self.service.export_attachment(self.current_id, target)
            self._info("تم تصدير الملف بنجاح.")
        except Exception as exc:
            self._error("تعذر تصدير الملف", exc)

    # --- context menu -------------------------------------------------------
    def _show_context_menu(self, pos) -> None:  # noqa: ANN001 - Qt point
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        self.load_selected()
        menu = QMenu(self)
        menu.addAction("فتح", self.open_selected)
        menu.addAction("تنزيل", self.export_selected)
        menu.addAction("تمييز كمفضلة", self.toggle_favorite)
        menu.addSeparator()
        menu.addAction("حذف", self.delete_attachment)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --- preview ------------------------------------------------------------
    def _update_preview(self, record: dict[str, Any], file_bytes: bytes | None) -> None:
        extension = str(record.get("file_extension") or "").lower()
        # Load the image binary lazily for a single selected row when needed.
        if file_bytes is None and extension in IMAGE_EXTENSIONS and self.current_id is not None:
            try:
                payload = self.service.get_file_payload(self.current_id)
                file_bytes = payload.get("file_data") if payload else None
            except Exception:
                file_bytes = None
        if extension in IMAGE_EXTENSIONS and file_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(file_bytes):
                self.preview_label.setPixmap(
                    pixmap.scaled(360, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                return
        icon = {"pdf": "📄  ملف PDF", "doc": "📝  مستند Word", "docx": "📝  مستند Word",
                "xls": "📊  ملف Excel", "xlsx": "📊  ملف Excel", "txt": "📃  ملف نصي"}.get(
            extension, "📎  ملف"
        )
        name = record.get("original_file_name") or ""
        size = self.service.format_file_size(record.get("file_size"))
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(f"{icon}\n\n{name}\n{size}")

    # --- form helpers -------------------------------------------------------
    def _collect_form(self) -> dict[str, Any]:
        return {
            "attachment_code": self.inputs["attachment_code"].text(),
            "title": self.inputs["title"].text(),
            "entity_type": self.inputs["entity_type"].currentData(),
            "entity_id": self.inputs["entity_id"].text(),
            "category": self.inputs["category"].currentData(),
            "tags": self.inputs["tags"].text(),
            "notes": self.inputs["notes"].toPlainText(),
            "is_favorite": self.inputs["is_favorite"].isChecked(),
            "updated_by": self._current_user_id(),
            "created_by": self._current_user_id(),
        }

    @staticmethod
    def _file_fields(meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "original_file_name": meta["original_file_name"],
            "file_extension": meta["file_extension"],
            "mime_type": meta["mime_type"],
            "file_size": meta["file_size"],
            "file_hash": meta["file_hash"],
        }

    def _fill_form(self, record: dict[str, Any]) -> None:
        self.inputs["attachment_code"].setText(str(record.get("attachment_code") or ""))
        self.inputs["title"].setText(str(record.get("title") or ""))
        self._select_combo_data(self.inputs["entity_type"], record.get("entity_type"))
        self.inputs["entity_id"].setText("" if record.get("entity_id") is None else str(record.get("entity_id")))
        self._select_combo_data(self.inputs["category"], record.get("category"))
        self.inputs["tags"].setText(str(record.get("tags") or ""))
        self.inputs["notes"].setPlainText(str(record.get("notes") or ""))
        self.inputs["is_favorite"].setChecked(bool(record.get("is_favorite")))
        self.drop_zone.set_file_name(record.get("original_file_name"))
        self._fill_file_info(record)

    def _fill_file_info(self, record: dict[str, Any]) -> None:
        self._info_labels["original_file_name"].setText(str(record.get("original_file_name") or "—"))
        self._info_labels["file_extension"].setText(str(record.get("file_extension") or "—"))
        self._info_labels["mime_type"].setText(str(record.get("mime_type") or "—"))
        self._info_labels["file_size"].setText(self.service.format_file_size(record.get("file_size")))
        self._info_labels["file_hash"].setText(str(record.get("file_hash") or "—"))

    def clear_form(self, reset_status: bool = True) -> None:
        self.current_id = None
        self.pending_file = None
        self.inputs["attachment_code"].clear()
        self.inputs["title"].clear()
        self.inputs["entity_type"].setCurrentIndex(self.inputs["entity_type"].count() - 1)
        self.inputs["entity_id"].clear()
        self.inputs["category"].setCurrentIndex(0)
        self.inputs["tags"].clear()
        self.inputs["notes"].clear()
        self.inputs["is_favorite"].setChecked(False)
        self.drop_zone.set_file_name(None)
        for label in self._info_labels.values():
            label.setText("—")
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("لا يوجد ملف للمعاينة")
        if reset_status:
            self.set_mode("view")
            self.status_label.setText("")

    def clear_filters(self) -> None:
        self.filter_search.clear()
        self.filter_entity.setCurrentIndex(0)
        self.filter_category.setCurrentIndex(0)
        self.filter_type.setCurrentIndex(0)
        self.filter_favorite.setChecked(False)
        self.filter_date_from.setDate(self.filter_date_from.minimumDate())
        self.filter_date_to.setDate(self.filter_date_to.minimumDate())
        self.refresh_table()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        editing = mode in {"new", "edit"}
        for key in ("attachment_code", "title", "entity_id", "tags"):
            self.inputs[key].setReadOnly(not editing)
        for key in ("entity_type", "category"):
            self.inputs[key].setEnabled(editing)
        self.inputs["notes"].setReadOnly(not editing)
        self.inputs["is_favorite"].setEnabled(editing)

        has_selection = self.current_id is not None
        self.save_button.setEnabled(editing and self._perm_save)
        self.clear_button.setEnabled(True)
        self.choose_button.setEnabled(editing)
        self.delete_button.setEnabled(has_selection and not editing and self._perm_delete)
        self.open_button.setEnabled(has_selection and not editing)
        self.export_button.setEnabled(has_selection and not editing and self._perm_export)
        self.favorite_button.setEnabled(has_selection and not editing)
        self.new_button.setEnabled(not editing and self._perm_create)
        self.mode_badge.setText({"view": "عرض", "new": "جديد", "edit": "تعديل"}.get(mode, mode))

    def begin_edit(self) -> None:
        if self.current_id is not None and self._perm_edit:
            self.set_mode("edit")
            self._info("وضع التعديل — يمكنك تغيير البيانات أو استبدال الملف.")

    # --- small utilities ----------------------------------------------------
    @staticmethod
    def _current_user_id() -> int | None:
        user = SESSION.user or {}
        try:
            return int(user.get("id")) if user.get("id") is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _select_row_by_id(self, attachment_id: int | None) -> None:
        if attachment_id is None:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and str(item.data(Qt.UserRole)) == str(attachment_id):
                self.table.selectRow(row)
                return

    def _require_payload(self) -> dict[str, Any] | None:
        if self.current_id is None:
            self._warn("اختر مرفقًا أولًا.")
            return None
        try:
            payload = self.service.get_file_payload(self.current_id)
        except Exception as exc:
            self._error("تعذر تحميل محتوى الملف", exc)
            return None
        if not payload or payload.get("file_data") is None:
            self._warn("لا يوجد محتوى ملف مخزن لهذا المرفق.")
            return None
        return payload

    def _info(self, message: str) -> None:
        self.status_label.setStyleSheet("background:transparent; color:#087443; font-size:13px; font-weight:800; padding:0 4px;")
        self.status_label.setText(message)

    def _warn(self, message: str) -> None:
        self.status_label.setStyleSheet("background:transparent; color:#B45309; font-size:13px; font-weight:800; padding:0 4px;")
        self.status_label.setText(message)
        QMessageBox.warning(self, "تنبيه", message)

    def _error(self, title: str, exc: Exception) -> None:
        self.status_label.setStyleSheet("background:transparent; color:#B91C1C; font-size:13px; font-weight:800; padding:0 4px;")
        self.status_label.setText(f"{title}: {exc}")
        QMessageBox.critical(self, title, str(exc))
