"""Reusable RTL CRUD screen shared by every data module.

This is layout + user-interaction only. Every read/write goes through
``ReviewDataService`` (database layer) and the per-screen metadata comes from
``TABLE_SPECS``. Concrete screens (customers, employees, ...) subclass this and
only supply their :class:`TableSpec`.
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from contextlib import contextmanager
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.security.session_context import SESSION
from app.services.review_data_service import ReviewDataService, TABLE_SPECS, FieldSpec, TableSpec
from app.ui.common.theme import (
    EXTRA_COLUMN_LABELS,
    GREEN,
    GREEN_DARK,
    LOOKUP_LAYOUT_KEYS,
    RIGHT_ALIGNED_FIELDS,
    TEXT,
    _RightAlignDelegate,
    _button_style,
)
from app.ui.dialogs.record_lookup_dialog import RecordLookupDialog


class BaseCrudScreen(QWidget):
    """Reusable RTL CRUD page with the same layout family as the reference customer UI."""

    # Placeholder text for the search box. Screens whose records are not searched
    # by name/number/phone (e.g. products) override this with their own hint.
    SEARCH_PLACEHOLDER = "ابحث بالاسم أو الرقم أو الهاتف"

    # Number of field columns in the form grid (2 fields per row by default).
    # Screens that want every field on its own line override this with 1.
    FORM_COLUMNS = 2

    def __init__(self, service: ReviewDataService, spec: TableSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.spec = spec
        self.current_id: Any | None = None
        self.inputs: dict[str, QLineEdit | QComboBox] = {}
        self.area_options: list[dict[str, Any]] = []
        self._suppress_customer = False
        self.mode = "view"
        # Permission gating for this screen's actions (crm.<key>.<action>).
        # SESSION is permissive when no user is logged in (legacy/tests).
        base = f"crm.{spec.key}"
        self._perm_create = SESSION.can(f"{base}.create")
        self._perm_edit = SESSION.can(f"{base}.edit")
        self._perm_save = SESSION.can(f"{base}.save")
        self._perm_delete = SESSION.can(f"{base}.delete")
        # "Delete All" wipes the whole screen: it keeps the normal delete
        # permission AND is further restricted to admin/superuser accounts.
        self._perm_delete_all = self._perm_delete and SESSION.is_admin
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet("QWidget { font-family: 'Segoe UI', 'Tahoma', 'Arial'; }")
        self._build_ui()
        if self.spec.key in LOOKUP_LAYOUT_KEYS:
            self.lookup_shortcut = QShortcut(QKeySequence("F1"), self)
            self.lookup_shortcut.activated.connect(self.open_lookup)
        self.set_mode("view")
        self.refresh_table()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._build_header())
        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_content(), 1)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background:{GREEN}; border-radius:6px; }}"
            "QLabel { background:transparent; color:#FFFFFF; }"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        icon = QLabel(self.spec.icon_text)
        icon.setFixedSize(54, 54)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            "background:rgba(255,255,255,0.16); border:1px solid rgba(255,255,255,0.28); "
            "border-radius:7px; font-size:24px; font-weight:900;"
        )
        title_box = QVBoxLayout()
        title = QLabel(self.spec.title)
        title.setStyleSheet("font-size:27px; font-weight:900;")
        title_box.addWidget(title)

        _username = (SESSION.user or {}).get("username") if SESSION.user else None
        self.header_user = QLabel(f"المستخدم: {_username or '—'}")
        self.header_date = QLabel("")
        for label in (self.header_user, self.header_date):
            label.setStyleSheet(
                "background:rgba(255,255,255,0.14); border:1px solid rgba(255,255,255,0.24); "
                "border-radius:6px; padding:9px 14px; font-weight:800;"
            )

        layout.addWidget(icon)
        layout.addLayout(title_box, 1)
        layout.addWidget(self.header_user)
        layout.addWidget(self.header_date)

        self.header_timer = QTimer(self)
        self.header_timer.timeout.connect(self._update_datetime)
        self.header_timer.start(1000)
        self._update_datetime()
        return header

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet("QFrame { background:#FFFFFF; border:1px solid #E5EAF0; border-radius:7px; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(7)

        self.new_button = QPushButton("جديد")
        self.edit_button = QPushButton("تعديل")
        self.save_button = QPushButton("حفظ")
        self.delete_button = QPushButton("حذف")
        self.cancel_button = QPushButton("إلغاء")
        self.refresh_button = QPushButton("تحديث")
        self.exit_button = QPushButton("خروج")
        self.mode_badge = QLabel("عرض")
        self.mode_badge.setAlignment(Qt.AlignCenter)
        self.mode_badge.setMinimumWidth(100)
        self.mode_badge.setStyleSheet("background:#ECFDF3;color:#087443;border:1px solid #B7E4C7;border-radius:7px;padding:8px 12px;font-weight:900;")

        buttons = [
            (self.new_button, QStyle.SP_FileIcon, "#1A7A3C", "#166534"),
            (self.edit_button, QStyle.SP_FileDialogDetailedView, "#64748B", "#475569"),
            (self.save_button, QStyle.SP_DialogSaveButton, "#2563EB", "#1D4ED8"),
            (self.delete_button, QStyle.SP_TrashIcon, "#DC2626", "#B91C1C"),
            (self.cancel_button, QStyle.SP_DialogCancelButton, "#6B7280", "#4B5563"),
            (self.refresh_button, QStyle.SP_BrowserReload, "#FFFFFF", "#F3F4F6"),
            (self.exit_button, QStyle.SP_ArrowBack, "#374151", "#1F2937"),
        ]
        for button, icon, bg, hover in buttons:
            button.setFixedHeight(38)
            button.setIcon(self.style().standardIcon(icon))
            if bg == "#FFFFFF":
                button.setStyleSheet(
                    "QPushButton { background:#FFFFFF;color:#374151;border:1px solid #D1D5DB;border-radius:6px;font-weight:800;padding:7px 12px; }"
                    f"QPushButton:hover {{ background:{hover}; }}"
                )
            else:
                button.setStyleSheet(_button_style(bg, hover))
            layout.addWidget(button)

        # "Delete All" is a destructive, admin-only action, so the button only
        # exists for admin/superuser sessions. It is placed next to the normal
        # delete button with a darker red to signal its wider blast radius.
        if self._perm_delete_all:
            self.delete_all_button = QPushButton("حذف الكل")
            self.delete_all_button.setFixedHeight(38)
            self.delete_all_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
            self.delete_all_button.setStyleSheet(_button_style("#991B1B", "#7F1D1D"))
            self.delete_all_button.clicked.connect(self.delete_all_records)
            layout.addWidget(self.delete_all_button)

        # Excel template export / import (only on the supported CRM screens).
        from app.services.excel_io_service import SUPPORTED_KEYS

        if self.spec.key in SUPPORTED_KEYS:
            self.export_excel_button = QPushButton("تصدير قالب إكسل")
            self.import_excel_button = QPushButton("استيراد من إكسل")
            for excel_button, bg, hover in (
                (self.export_excel_button, "#0E7490", "#155E75"),
                (self.import_excel_button, "#7C3AED", "#6D28D9"),
            ):
                excel_button.setFixedHeight(38)
                excel_button.setStyleSheet(_button_style(bg, hover))
                layout.addWidget(excel_button)
            self.export_excel_button.clicked.connect(self.export_excel_template)
            self.import_excel_button.clicked.connect(self.import_from_excel)

        # Screen-specific extra actions (e.g. "طباعة سند" on the vouchers screen).
        # Default is a no-op; subclasses override _install_extra_toolbar_buttons.
        self._install_extra_toolbar_buttons(layout)

        layout.addStretch(1)
        layout.addWidget(self.mode_badge)

        self.new_button.clicked.connect(self.new_record)
        self.edit_button.clicked.connect(lambda: self.set_mode("edit") if self.current_id is not None else None)
        self.save_button.clicked.connect(self.save_record)
        self.delete_button.clicked.connect(self.delete_record)
        self.cancel_button.clicked.connect(self.cancel_edit)
        self.refresh_button.clicked.connect(self.refresh_screen)
        self.exit_button.clicked.connect(self.window().close)
        return bar

    def _install_extra_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        """Hook for subclasses to append screen-specific toolbar buttons.

        Called from :meth:`_build_toolbar` just before the trailing stretch and
        the mode badge, so any added buttons sit alongside the CRUD actions.
        The default adds nothing.
        """

    def _build_content(self):
        if self.spec.key in LOOKUP_LAYOUT_KEYS:
            content = QHBoxLayout()
            content.setSpacing(12)
            content.addWidget(self._build_form_panel(), 1)
            content.addWidget(self._build_list_panel(), 0)
            return content

        content = QVBoxLayout()
        content.setSpacing(10)
        content.addWidget(self._build_form_panel(), 0)
        content.addWidget(self._build_list_panel(), 1)
        return content

    def _build_form_panel(self) -> QScrollArea:
        visible_fields = [field for field in self.spec.fields if not field.hidden_on_form]
        group_names = list(dict.fromkeys(f.group for f in visible_fields if f.group))

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        title = QLabel(self.spec.title.replace("إدارة", "بيانات"))
        title.setStyleSheet(f"font-size:21px; font-weight:900; color:{TEXT};")
        self.summary_label = QLabel("سجل جديد")
        self.summary_label.setStyleSheet("font-size:13px; font-weight:700; color:#64748B;")
        outer.addWidget(title)
        outer.addWidget(self.summary_label)

        if group_names:
            for group_name in group_names:
                fields = [f for f in visible_fields if f.group == group_name]
                outer.addWidget(self._build_field_group(group_name, fields))
        else:
            self.form_box = self._build_field_group("", visible_fields)
            outer.addWidget(self.form_box)
        outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(container)
        if self.spec.key not in LOOKUP_LAYOUT_KEYS:
            scroll.setMinimumHeight(205)
            scroll.setMaximumHeight(245)
        return scroll

    def _build_field_group(self, title: str, fields: list[FieldSpec]) -> QGroupBox:
        box = QGroupBox(title)
        box.setStyleSheet(
            "QGroupBox { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:7px; margin-top:14px; }"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:16px; "
            f"padding:0 16px; color:{GREEN}; font-weight:900; }}"
        )
        grid = QGridLayout(box)
        grid.setContentsMargins(18, 18, 18, 16)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setAlignment(Qt.AlignTop)
        per_row = max(1, self.FORM_COLUMNS)
        for index, field in enumerate(fields):
            label = QLabel(field.label)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setLayoutDirection(Qt.RightToLeft)
            label.setMinimumWidth(90)
            label.setStyleSheet(f"font-size:14px; font-weight:900; color:{TEXT};")
            editor = self._make_editor(field)
            self.inputs[field.name] = editor
            row = index // per_row
            pair = index % per_row
            grid.addWidget(label, row, pair * 2)
            grid.addWidget(self._field_editor_widget(field, editor), row, pair * 2 + 1)
        # Only the columns actually used (given per_row) get stretch: each editor
        # column expands, each label column stays tight. This keeps a single
        # editor filling the full panel width instead of leaving an empty stretch
        # column beside it.
        for pair in range(per_row):
            grid.setColumnStretch(pair * 2, 0)      # label column
            grid.setColumnStretch(pair * 2 + 1, 1)  # editor column
        return box

    def _field_editor_widget(self, field: FieldSpec, editor: QLineEdit | QComboBox) -> QWidget:
        if (
            self.spec.key == "daily_followups"
            and field.combo_source == "customers_name"
            and hasattr(self, "open_customer_lookup")
        ):
            wrapper = QWidget()
            layout = QHBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            button = QPushButton()
            button.setObjectName("customer_name_lookup_button")
            button.setFixedSize(38, 38)
            button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
            button.setToolTip("ط¨ط­ط« ط¹ظ† ط¹ظ…ظٹظ„")
            button.setStyleSheet(
                "QPushButton { background:#FFFFFF;color:#374151;border:1px solid #D1D5DB;"
                "border-radius:6px;font-weight:800;padding:6px; }"
                "QPushButton:hover { background:#F3F4F6; }"
            )
            button.clicked.connect(lambda _checked=False: self.open_customer_lookup())
            self.customer_name_lookup_button = button
            layout.addWidget(editor, 1)
            layout.addWidget(button, 0)
            return wrapper
        return editor

    def _make_editor(self, field: FieldSpec) -> QLineEdit | QComboBox:
        if self.spec.key == "customers" and field.name == "place_area_feddan":
            combo = self._make_combo(read_only_edit=True)
            self._place_combo = combo
            self._populate_area_combo(combo)
            return combo

        if field.combo_source:
            return self._make_source_combo(field)

        editor = QLineEdit()
        editor.setMinimumHeight(38)
        editor.setReadOnly(field.readonly)
        editor.setPlaceholderText(field.label)
        editor.setLayoutDirection(Qt.LeftToRight)
        if field.name in RIGHT_ALIGNED_FIELDS.get(self.spec.key, set()):
            editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        else:
            editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        editor.setStyleSheet(
            f"QLineEdit {{ background:#FFFFFF; border:1px solid {GREEN}; border-radius:7px; "
            f"padding:6px 10px; font-size:14px; font-weight:900; color:{TEXT}; }}"
            f"QLineEdit:focus {{ border:2px solid {GREEN_DARK}; }}"
            "QLineEdit:read-only { background:#F8FAFC; color:#64748B; }"
        )
        return editor

    def _make_combo(self, read_only_edit: bool = False) -> QComboBox:
        combo = QComboBox()
        combo.setMinimumHeight(38)
        combo.setLayoutDirection(Qt.LeftToRight)
        combo.setEditable(True)
        combo.lineEdit().setReadOnly(read_only_edit)
        combo.lineEdit().setLayoutDirection(Qt.LeftToRight)
        combo.lineEdit().setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        combo.lineEdit().setStyleSheet("background:transparent; border:none;")
        combo.setStyleSheet(
            f"QComboBox {{ background:#FFFFFF; border:1px solid {GREEN}; border-radius:7px; "
            f"padding:6px 10px; font-size:14px; font-weight:900; color:{TEXT}; }}"
            f"QComboBox:focus {{ border:2px solid {GREEN_DARK}; }}"
            "QComboBox:disabled { background:#F8FAFC; color:#64748B; }"
        )
        combo.setItemDelegate(_RightAlignDelegate(combo))
        return combo

    def _make_source_combo(self, field: FieldSpec) -> QComboBox:
        # Editable combos use contains-completion so users can search by any
        # part of the visible value. Customer phone/name also auto-fill the
        # linked customer after selection.
        searchable = field.combo_source in {
            "customers_phone",
            "customers_name",
            "employees",
            "case_statuses",
        }
        combo = self._make_combo(read_only_edit=not searchable)
        if searchable:
            combo.setInsertPolicy(QComboBox.NoInsert)
            if combo.completer() is not None:
                combo.completer().setCompletionMode(QCompleter.PopupCompletion)
                combo.completer().setFilterMode(Qt.MatchContains)

        if field.combo_source == "employees":
            self._fill_combo(combo, self._employee_options(), "employee_id", "employee_name")
            self.employee_combo = combo
            combo.lineEdit().editingFinished.connect(lambda: self._commit_source_combo_text(combo))
        elif field.combo_source == "case_statuses":
            self._fill_combo(combo, self._case_status_options(), "case_status_id", "case_status_name")
            self.case_status_combo = combo
            combo.lineEdit().editingFinished.connect(lambda: self._commit_source_combo_text(combo))
        elif field.combo_source == "customers_phone":
            combo.addItem("", None)
            self.customer_phone_combo = combo
            combo.activated.connect(lambda *_: self._commit_customer_from("customers_phone", by_text=False))
            combo.lineEdit().editingFinished.connect(
                lambda: self._commit_customer_from("customers_phone", by_text=True)
            )
        elif field.combo_source == "customers_name":
            combo.addItem("", None)
            self.customer_name_combo = combo
            combo.activated.connect(lambda *_: self._commit_customer_from("customers_name", by_text=False))
            combo.lineEdit().editingFinished.connect(
                lambda: self._commit_customer_from("customers_name", by_text=True)
            )
        return combo

    def _fill_combo(
        self,
        combo: QComboBox,
        rows: list[dict[str, Any]],
        data_key: str,
        label_key: str,
        skip_blank_labels: bool = False,
    ) -> None:
        # Disable view updates/signals while bulk-loading so adding hundreds of
        # rows (e.g. the full customer list) does not repaint per item.
        combo.setUpdatesEnabled(False)
        combo.blockSignals(True)
        try:
            combo.addItem("", None)
            for row in rows:
                data = row.get(data_key)
                label = row.get(label_key)
                if skip_blank_labels and label in (None, ""):
                    continue
                combo.addItem("" if label in (None, "") else str(label), data)
        finally:
            combo.blockSignals(False)
            combo.setUpdatesEnabled(True)

    def _employee_options(self) -> list[dict[str, Any]]:
        try:
            return self.service.list_employees_for_selection()
        except Exception:
            return []

    def _case_status_options(self) -> list[dict[str, Any]]:
        try:
            return self.service.list_case_statuses_for_selection()
        except Exception:
            return []

    def _customer_options(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        if force_refresh or not hasattr(self, "_customer_cache"):
            try:
                self._customer_cache = self.service.list_customers_for_selection()
            except Exception:
                self._customer_cache = []
        return self._customer_cache

    def _reload_customer_combos(self) -> None:
        if not hasattr(self, "customer_phone_combo") and not hasattr(self, "customer_name_combo"):
            return
        rows = self._customer_options(force_refresh=True)
        if hasattr(self, "customer_phone_combo"):
            current_phone_id = self.customer_phone_combo.currentData()
            self.customer_phone_combo.clear()
            self._fill_combo(
                self.customer_phone_combo,
                rows,
                "customer_id",
                "phone_number",
                skip_blank_labels=True,
            )
            self._set_editor_value(self.customer_phone_combo, current_phone_id)
        if hasattr(self, "customer_name_combo"):
            current_name_id = self.customer_name_combo.currentData()
            self.customer_name_combo.clear()
            self._fill_combo(
                self.customer_name_combo,
                rows,
                "customer_id",
                "customer_name",
                skip_blank_labels=True,
            )
            self._set_editor_value(self.customer_name_combo, current_name_id)

    def _reload_reference_combos(self) -> None:
        """Reload employee/status dropdowns from their source screens."""
        if hasattr(self, "employee_combo"):
            current_employee_id = self.employee_combo.currentData()
            current_employee_text = self.employee_combo.currentText()
            self.employee_combo.clear()
            self._fill_combo(
                self.employee_combo,
                self._employee_options(),
                "employee_id",
                "employee_name",
            )
            self._restore_combo_selection(self.employee_combo, current_employee_id, current_employee_text)
        if hasattr(self, "case_status_combo"):
            current_status_id = self.case_status_combo.currentData()
            current_status_text = self.case_status_combo.currentText()
            self.case_status_combo.clear()
            self._fill_combo(
                self.case_status_combo,
                self._case_status_options(),
                "case_status_id",
                "case_status_name",
            )
            self._restore_combo_selection(self.case_status_combo, current_status_id, current_status_text)

    def _restore_combo_selection(self, combo: QComboBox, value: Any, text: str) -> None:
        index = combo.findData(value)
        if index < 0 and text:
            index = combo.findText(text, Qt.MatchFixedString)
        combo.setCurrentIndex(max(0, index))

    def _commit_source_combo_text(self, combo: QComboBox) -> None:
        """Resolve a typed employee/status label to the existing combo item."""
        text = combo.currentText().strip()
        if not text:
            combo.setCurrentIndex(0)
            return
        index = combo.findText(text, Qt.MatchFixedString)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _populate_area_combo(self, combo: QComboBox) -> None:
        combo.addItem("", None)
        try:
            self.area_options = self.service.list_places_for_selection()
        except Exception:
            self.area_options = []
        for place in self.area_options:
            label = place.get("place_number")
            place_id = place.get("place_id")
            combo.addItem(str(label) if label not in (None, "") else str(place_id), place_id)

    def _build_list_panel(self) -> QGroupBox:
        box = QGroupBox("البحث والتسجيل")
        if self.spec.key in LOOKUP_LAYOUT_KEYS:
            box.setFixedWidth(650)
        else:
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        box.setStyleSheet(
            "QGroupBox { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:7px; margin-top:14px; }"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:16px; "
            f"padding:0 16px; color:{GREEN}; font-weight:900; }}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 12)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        self.search_text = QLineEdit()
        self.search_text.setPlaceholderText(self.SEARCH_PLACEHOLDER)
        self.search_text.setFixedHeight(40)
        if self.spec.key in LOOKUP_LAYOUT_KEYS:
            self.search_text.setLayoutDirection(Qt.RightToLeft)
            self.search_text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search_text.setStyleSheet(
            "QLineEdit { background:#FFFFFF; border:1px solid #CBD5E1; border-radius:7px; padding:8px 10px; }"
            f"QLineEdit:focus {{ border:1px solid {GREEN}; }}"
        )
        filter_button = QPushButton("فلتر")
        filter_button.setFixedHeight(40)
        filter_button.setMaximumWidth(80)
        filter_button.setStyleSheet(
            "QPushButton{background:#E6EEF4;color:#1F2937;border:1px solid #CBD5E1;border-radius:6px;font-weight:800;}"
            "QPushButton:hover{background:#DCE7F0;}"
        )
        search_row.addWidget(filter_button)
        search_row.addWidget(self.search_text, 1)
        layout.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.spec.list_columns))
        self.table.setHorizontalHeaderLabels([self._label_for_column(c) for c in self.spec.list_columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        list_header = self.table.horizontalHeader()
        list_header.setSectionResizeMode(QHeaderView.Interactive)
        list_header.setStretchLastSection(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; border:1px solid #E2E8F0; "
            "gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:10px 8px; }}"
            "QTableWidget::item:selected { background:#DDF3E6; color:#111827; }"
        )
        self.table.itemSelectionChanged.connect(self.load_selected)
        layout.addWidget(self.table, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#64748B; font-weight:700;")
        layout.addWidget(self.count_label)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.refresh_table)
        self.search_text.textChanged.connect(self.search_timer.start)
        filter_button.clicked.connect(self._refresh_table_now)
        return box

    def _label_for_column(self, column: str) -> str:
        for field in self.spec.fields:
            if field.name == column:
                return field.label
        return EXTRA_COLUMN_LABELS.get(column, column)

    def _update_datetime(self) -> None:
        now = datetime.datetime.now()
        self.header_date.setText(f"التاريخ: {now:%Y-%m-%d} | الوقت: {now:%H:%M:%S}")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        editing = mode in {"new", "edit"}
        for field in self.spec.fields:
            editor = self.inputs.get(field.name)
            if editor is None:
                continue
            if isinstance(editor, QComboBox):
                editor.setEnabled(editing and not field.readonly)
            else:
                editor.setReadOnly(field.readonly or not editing)
        self.save_button.setEnabled(editing and self._perm_save)
        self.cancel_button.setEnabled(editing)
        self.edit_button.setEnabled((not editing) and self.current_id is not None and self._perm_edit)
        self.delete_button.setEnabled((not editing) and self.current_id is not None and self._perm_delete)
        # "Delete All" targets the whole table, so it does not require a selected
        # row — only that we are not mid-edit and the user is an admin.
        if hasattr(self, "delete_all_button"):
            self.delete_all_button.setEnabled((not editing) and self._perm_delete_all)
        self.new_button.setEnabled((not editing) and self._perm_create)
        self.mode_badge.setText({"view": "عرض", "new": "جديد", "edit": "تعديل"}.get(mode, mode))

    def refresh_table(self) -> None:
        try:
            rows = self.service.list_records(self.spec, self.search_text.text())
        except Exception as exc:
            self._show_error("فشل تحميل البيانات", exc)
            return
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(rows))
            for row_index, record in enumerate(rows):
                for col_index, column in enumerate(self.spec.list_columns):
                    item = QTableWidgetItem("" if record.get(column) is None else str(record.get(column)))
                    item.setTextAlignment(Qt.AlignCenter)
                    if col_index == 0:
                        item.setData(Qt.UserRole, record.get(self.spec.primary_key))
                        item.setBackground(QColor("#ECFDF3"))
                    self.table.setItem(row_index, col_index, item)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        if not getattr(self, "_list_columns_sized", False) and rows:
            self.table.resizeColumnsToContents()
            for col in range(self.table.columnCount()):
                self.table.setColumnWidth(col, min(self.table.columnWidth(col) + 16, 280))
            self._list_columns_sized = True
        self.count_label.setText(f"عدد السجلات المعروضة: {len(rows)}")
        if rows and self.current_id is None:
            self.table.selectRow(0)

    def _refresh_table_now(self) -> None:
        if hasattr(self, "search_timer"):
            self.search_timer.stop()
        self.refresh_table()

    def refresh_screen(self) -> None:
        self._reload_reference_combos()
        self._reload_customer_combos()
        self.refresh_table()

    def load_selected(self) -> None:
        if self.mode in {"new", "edit"}:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        record_id = item.data(Qt.UserRole)
        try:
            record = self.service.get_record(self.spec, record_id)
        except Exception as exc:
            self._show_error("فشل تحميل السجل", exc)
            return
        if record:
            self.current_id = record_id
            self._fill_form(record)
            self.set_mode("view")

    @contextmanager
    def _profile(self, label: str):
        """Time a block and print it when CRM_PROFILE=1.

        Zero overhead in normal runs (guarded by an env check). Set the
        environment variable ``CRM_PROFILE=1`` before launching the app to see
        a per-step breakdown of ``new_record`` (and any other instrumented
        block) on stderr, e.g. ``[PROFILE] new_record._ensure_customer_combos_ready: 4.2 ms``.
        """
        if not os.environ.get("CRM_PROFILE"):
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - start) * 1000.0
            print(f"[PROFILE] {label}: {elapsed:.1f} ms", file=sys.stderr)

    def _ensure_customer_combos_ready(self) -> None:
        """Make the customer combos usable for a new record WITHOUT rebuilding them.

        The full customer list (~12k rows) is queried once and cached, and both
        the phone and name combos are populated when the screen is first built.
        The previous code force-reloaded that list from PostgreSQL and refilled
        *both* editable combos (each with a MatchContains completer) on every
        click of "New" — clearing and re-adding ~12k items per combo forces the
        completer's internal model to be rebuilt twice, which was the 3–5 s
        stall before the empty form appeared.

        Opening a new record does not need fresh data: the item lists have not
        changed, so we only reset the current *selection* to blank. A customer
        added elsewhere is still reachable immediately via the F5 lookup (which
        queries the DB live) and via the explicit Refresh button (which does a
        full rebuild through ``_reload_customer_combos``).
        """
        combos = [
            c
            for c in (
                getattr(self, "customer_phone_combo", None),
                getattr(self, "customer_name_combo", None),
            )
            if c is not None
        ]
        if not combos:
            return
        for combo in combos:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            if combo.isEditable():
                combo.setCurrentText("")
            combo.blockSignals(False)

    def new_record(self) -> None:
        with self._profile("new_record.total"):
            with self._profile("new_record._reload_reference_combos"):
                self._reload_reference_combos()
            with self._profile("new_record._ensure_customer_combos_ready"):
                self._ensure_customer_combos_ready()
            self.current_id = None
            with self._profile("new_record._clear_form"):
                self._clear_form()
            if self.spec.key in LOOKUP_LAYOUT_KEYS:
                try:
                    with self._profile("new_record.next_id"):
                        next_id = self.service.next_id(self.spec)
                    self._set_editor_value(self.inputs[self.spec.primary_key], next_id)
                except Exception:
                    pass
            if self.spec.key == "daily_followups":
                today = datetime.date.today().strftime("%Y-%m-%d")
                self._set_editor_value(self.inputs.get("follow_up_date"), today)
                self._set_editor_value(self.inputs.get("contact_count"), 1)
            self.summary_label.setText("سجل جديد")
            with self._profile("new_record.set_mode"):
                self.set_mode("new")

    def cancel_edit(self) -> None:
        self.set_mode("view")
        if self.current_id is None:
            self.load_selected()
        else:
            record = self.service.get_record(self.spec, self.current_id)
            if record:
                self._fill_form(record)

    def save_record(self) -> None:
        payload = {name: self._editor_value(editor) for name, editor in self.inputs.items()}
        try:
            saved_id = self.service.save_record(
                self.spec,
                payload,
                None if self.mode == "new" else self.current_id,
            )
            self.current_id = saved_id
            self.set_mode("view")
            self.refresh_table()
            self._select_row_by_id(saved_id)
            QMessageBox.information(self, "تم الحفظ", "تم حفظ البيانات بنجاح.")
        except Exception as exc:
            self._show_error("فشل حفظ البيانات", exc)

    def delete_record(self) -> None:
        if self.current_id is None:
            return
        answer = QMessageBox.question(
            self,
            "تأكيد الحذف",
            "هل تريد حذف السجل المحدد؟",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.service.delete_record(self.spec, self.current_id)
            self.current_id = None
            self._clear_form()
            self.refresh_table()
            self.set_mode("view")
        except Exception as exc:
            self._show_error("لا يمكن حذف السجل", exc)

    def delete_all_records(self) -> None:
        """Delete every record on this screen (admin only) after confirmation."""
        # Never bypass the existing security checks: this stays admin/superuser
        # only even if the button is reached some other way.
        if not self._perm_delete_all:
            QMessageBox.warning(
                self,
                "غير مسموح",
                "هذا الإجراء متاح لمستخدمي المدير (Admin) فقط.",
            )
            return
        answer = QMessageBox.warning(
            self,
            "تأكيد حذف الكل",
            "سيؤدي هذا الإجراء إلى حذف جميع السجلات في هذه الشاشة.\n"
            "This action will delete all records in the current screen.\n"
            "هل أنت متأكد؟",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Ok:
            # Cancel (or dialog closed): do nothing at all.
            return
        try:
            deleted = self.service.delete_all_records(self.spec)
        except Exception as exc:
            # Blocked by linked records or any DB error: clear message, no crash.
            self._show_error("تعذر حذف جميع السجلات", exc)
            return
        self.current_id = None
        self._clear_form()
        self.refresh_table()
        self.set_mode("view")
        QMessageBox.information(
            self,
            "تم الحذف",
            f"تم حذف جميع سجلات هذه الشاشة بنجاح. عدد السجلات المحذوفة: {deleted}",
        )

    def _fill_form(self, record: dict[str, Any]) -> None:
        for field in self.spec.fields:
            if field.hidden_on_form:
                continue
            value = record.get(field.name)
            editor = self.inputs.get(field.name)
            if editor is not None:
                self._set_editor_value(editor, value)
        if self.spec.key == "daily_followups" and record.get("customer_id"):
            # Keep the stored follow-up snapshot; only populate the customer block.
            self._load_customer(record["customer_id"], prefill_installments=False)
        label_value = record.get(self.spec.primary_key, "")
        self.summary_label.setText(f"السجل الحالي: {label_value}")

    def _clear_form(self) -> None:
        for editor in self.inputs.values():
            self._set_editor_value(editor, None)

    def _editor_value(self, editor: QLineEdit | QComboBox) -> Any:
        if isinstance(editor, QComboBox):
            return editor.currentData()
        return editor.text()

    def _set_editor_value(self, editor: QLineEdit | QComboBox | None, value: Any) -> None:
        if editor is None:
            return
        if isinstance(editor, QComboBox):
            if editor is getattr(self, "_place_combo", None):
                # Drop any legacy value added for a previously shown record so
                # the dropdown keeps only the real places (+ one legacy entry).
                base_count = 1 + len(self.area_options)
                while editor.count() > base_count:
                    editor.removeItem(editor.count() - 1)
            index = editor.findData(value)
            if index < 0 and value is not None:
                try:
                    index = editor.findData(int(value))
                except (TypeError, ValueError):
                    index = -1
            if index < 0 and value not in (None, "") and editor is getattr(self, "_place_combo", None):
                # Legacy area code not in the current places list: show as-is.
                editor.addItem(str(value), value)
                index = editor.count() - 1
            editor.setCurrentIndex(max(0, index))
            return
        editor.setText("" if value is None else str(value))

    @staticmethod
    def _set_combo_value_with_label(combo: QComboBox, value: Any, label: Any) -> None:
        index = combo.findData(value)
        if index < 0 and value is not None:
            try:
                index = combo.findData(int(value))
            except (TypeError, ValueError):
                index = -1
        if index < 0 and value not in (None, ""):
            combo.addItem("" if label in (None, "") else str(label), value)
            index = combo.count() - 1
        elif index >= 0 and label not in (None, "") and not combo.itemText(index):
            combo.setItemText(index, str(label))
        combo.setCurrentIndex(max(0, index))

    def open_lookup(self) -> None:
        if self.spec.key not in LOOKUP_LAYOUT_KEYS:
            return
        dialog = RecordLookupDialog(self.service, self.spec, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_id is not None:
            self.current_id = dialog.selected_id
            record = self.service.get_record(self.spec, self.current_id)
            if record:
                self._fill_form(record)
                self.set_mode("view")
                self._select_row_by_id(self.current_id)

    def _commit_customer_from(self, source: str, by_text: bool) -> None:
        """Resolve the customer chosen via the phone/name combo and fill the form."""
        if getattr(self, "_suppress_customer", False):
            return
        combo = self.customer_phone_combo if source == "customers_phone" else self.customer_name_combo
        if by_text:
            # Free-typed value: resolve against the customers table.
            text = combo.currentText().strip()
            if not text:
                return
            finder = (
                self.service.find_customer_by_phone
                if source == "customers_phone"
                else self.service.find_customer_by_name
            )
            customer = finder(text)
            customer_id = customer.get("customer_id") if customer else None
        else:
            # Picked from the dropdown: the selected item carries the id.
            customer_id = combo.currentData()
        if customer_id is not None:
            self._load_customer(customer_id, prefill_installments=self.mode in {"new", "edit"})

    def _load_customer(self, customer_id: Any, prefill_installments: bool) -> None:
        customer = self.service.get_record(TABLE_SPECS["customers"], customer_id)
        if customer:
            self._apply_customer(customer, prefill_installments)

    def _apply_customer(self, customer: dict[str, Any], prefill_installments: bool) -> None:
        self._suppress_customer = True
        try:
            customer_id = customer.get("customer_id")
            if hasattr(self, "customer_phone_combo"):
                self._set_combo_value_with_label(
                    self.customer_phone_combo,
                    customer_id,
                    customer.get("phone_number"),
                )
            if hasattr(self, "customer_name_combo"):
                self._set_combo_value_with_label(
                    self.customer_name_combo,
                    customer_id,
                    customer.get("customer_name"),
                )
            self._set_editor_value(self.inputs.get("customer_id"), customer_id)
            for name in ("place_area_feddan", "area_number", "building", "unit_number", "floor_number"):
                value = customer.get(name)
                if name == "area_number" and value in (None, ""):
                    value = customer.get("legacy_area_number_2")
                self._set_editor_value(self.inputs.get(name), value)
            if prefill_installments:
                for name in ("installment_duration_years", "remaining_installments", "installment_amount"):
                    self._set_editor_value(self.inputs.get(name), customer.get(name))
        finally:
            self._suppress_customer = False

    def _select_row_by_id(self, record_id: Any) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and str(item.data(Qt.UserRole)) == str(record_id):
                self.table.selectRow(row)
                return

    # -- Excel template export / import -------------------------------------

    def _excel_service(self):
        from app.services.excel_io_service import ExcelTemplateService

        return ExcelTemplateService(self.service)

    def _io_spec(self):
        from app.services.excel_io_service import io_spec_for

        return io_spec_for(self.spec.key)

    def export_excel_template(self) -> None:
        """Export an empty Excel template matching this screen's grid columns."""
        default_name = f"{self.spec.key}_template.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "تصدير قالب إكسل", default_name, "ملفات إكسل (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            self._excel_service().export_template(self._io_spec(), path)
        except Exception as exc:
            self._show_error("تعذر تصدير القالب", exc)
            return
        QMessageBox.information(self, "تم التصدير", f"تم إنشاء القالب:\n{path}")

    def import_from_excel(self) -> None:
        """Read an Excel file, validate it, and open the review window."""
        if not self._perm_create:
            QMessageBox.warning(
                self, "غير مسموح", "لا تملك صلاحية إضافة سجلات في هذه الشاشة."
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "استيراد من إكسل", "", "ملفات إكسل (*.xlsx)"
        )
        if not path:
            return
        service = self._excel_service()
        io_spec = self._io_spec()
        try:
            rows = service.read_workbook(io_spec, path)
        except Exception as exc:
            self._show_error("تعذر قراءة الملف", exc)
            return
        if not rows:
            QMessageBox.information(self, "لا توجد بيانات", "لا توجد صفوف في الملف.")
            return

        from app.ui.dialogs.import_review_dialog import ImportReviewDialog

        dialog = ImportReviewDialog(service, io_spec, rows, self)
        dialog.exec()
        if dialog.saved_count:
            self.current_id = None
            self._clear_form()
            self.refresh_screen()

    def _show_error(self, title: str, exc: Exception) -> None:
        QMessageBox.critical(self, title, str(exc))
