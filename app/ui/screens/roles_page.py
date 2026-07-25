"""Roles / Permission Groups screen (مجموعات الصلاحيات).

UI only: a roles list/editor plus a permissions matrix (rows = screens/reports,
columns = actions). Logic/queries go through ``PermissionService`` and
``PermissionsSyncService``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.security.session_context import SESSION
from app.services.permission_registry import ACTION_NAMES_AR, REPORT_ACTIONS, SCREEN_ACTIONS
from app.services.permission_service import PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common.theme import GREEN, _button_style

T = "security.roles"


class RolesPage(QWidget):
    def __init__(self, permission_service: PermissionService,
                 sync_service: PermissionsSyncService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.permissions = permission_service
        self.sync_service = sync_service or PermissionsSyncService(permission_service.repository)
        self.current_role_id: int | None = None
        self.mode = "view"
        self.scope = "all"
        self.active_columns: list[str] = []
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("مجموعات الصلاحيات")
        self._build_ui()
        self.refresh_roles()
        self.rebuild_matrix()
        self.set_mode("view")
        # Auto-select the first role so its saved permissions show immediately
        # (otherwise the matrix looks empty until a role is clicked).
        self._auto_select_first_role()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_roles_panel(), 0)
        root.addWidget(self._build_matrix_panel(), 1)

    # --- roles panel --------------------------------------------------------
    def _build_roles_panel(self) -> QGroupBox:
        box = QGroupBox("المجموعات")
        box.setFixedWidth(330)
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(8)

        self.role_search = QLineEdit()
        self.role_search.setPlaceholderText("بحث في المجموعات")
        self.role_search.setFixedHeight(34)
        self.role_search.textChanged.connect(self.refresh_roles)
        layout.addWidget(self.role_search)

        self.roles_table = QTableWidget()
        self.roles_table.setColumnCount(2)
        self.roles_table.setHorizontalHeaderLabels(["المجموعة", "نشط"])
        self.roles_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.roles_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.roles_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.roles_table.verticalHeader().setVisible(False)
        self.roles_table.horizontalHeader().setStretchLastSection(True)
        self.roles_table.setStyleSheet(self._table_style())
        self.roles_table.itemSelectionChanged.connect(self.load_selected_role)
        layout.addWidget(self.roles_table, 1)

        form = QFormLayout()
        form.setSpacing(7)
        self.role_code = QLineEdit()
        self.role_name_ar = QLineEdit()
        self.role_name_en = QLineEdit()
        self.role_active = QCheckBox("نشط")
        self.role_desc = QTextEdit()
        self.role_desc.setFixedHeight(48)
        for w in (self.role_code, self.role_name_ar, self.role_name_en):
            w.setFixedHeight(32)
        form.addRow("الكود", self.role_code)
        form.addRow("الاسم (عربي)", self.role_name_ar)
        form.addRow("الاسم (إنجليزي)", self.role_name_en)
        form.addRow("الحالة", self.role_active)
        form.addRow("الوصف", self.role_desc)
        layout.addLayout(form)

        btns = QHBoxLayout()
        self.new_role_btn = QPushButton("جديد")
        self.edit_role_btn = QPushButton("تعديل")
        self.save_role_btn = QPushButton("حفظ")
        for b, bg, hover in ((self.new_role_btn, "#1A7A3C", "#166534"),
                             (self.edit_role_btn, "#64748B", "#475569"),
                             (self.save_role_btn, "#2563EB", "#1D4ED8")):
            b.setFixedHeight(34)
            b.setStyleSheet(_button_style(bg, hover))
            btns.addWidget(b)
        layout.addLayout(btns)
        self.new_role_btn.clicked.connect(self.new_role)
        self.edit_role_btn.clicked.connect(lambda: self.set_mode("edit") if self.current_role_id else None)
        self.save_role_btn.clicked.connect(self.save_role)
        return box

    # --- matrix panel -------------------------------------------------------
    def _build_matrix_panel(self) -> QGroupBox:
        box = QGroupBox("مصفوفة الصلاحيات")
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.filter_group = QButtonGroup(self)
        for idx, (code, label) in enumerate((("all", "الكل"), ("screen", "الشاشات"), ("report", "التقارير"))):
            rb = QRadioButton(label)
            rb.setProperty("scope", code)
            if code == "all":
                rb.setChecked(True)
            rb.toggled.connect(self._on_filter_changed)
            self.filter_group.addButton(rb, idx)
            top.addWidget(rb)
        top.addSpacing(10)
        self.search_perm = QLineEdit()
        self.search_perm.setPlaceholderText("بحث في الشاشات/التقارير")
        self.search_perm.setFixedHeight(32)
        self.search_perm.textChanged.connect(self._apply_search)
        top.addWidget(self.search_perm, 1)
        layout.addLayout(top)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.check_all_btn = QPushButton("تحديد الكل")
        self.clear_all_btn = QPushButton("مسح الكل")
        self.sync_btn = QPushButton("مزامنة الصلاحيات")
        self.save_perms_btn = QPushButton("حفظ صلاحيات المجموعة")
        for b, bg, hover in ((self.check_all_btn, "#0F766E", "#115E59"),
                             (self.clear_all_btn, "#6B7280", "#4B5563"),
                             (self.sync_btn, "#B45309", "#92400E"),
                             (self.save_perms_btn, "#2563EB", "#1D4ED8")):
            b.setFixedHeight(34)
            b.setStyleSheet(_button_style(bg, hover))
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.check_all_btn.clicked.connect(lambda: self._set_all(True))
        self.clear_all_btn.clicked.connect(lambda: self._set_all(False))
        self.sync_btn.clicked.connect(self.sync_permissions)
        self.save_perms_btn.clicked.connect(self.save_permissions)

        hint = QLabel("اضغط على رأس أي عمود لتحديد/إلغاء العمود بالكامل")
        hint.setStyleSheet("color:#64748B; font-weight:700;")
        layout.addWidget(hint)

        self.matrix = QTableWidget()
        self.matrix.setSelectionMode(QAbstractItemView.NoSelection)
        self.matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix.setAlternatingRowColors(True)
        self.matrix.verticalHeader().setVisible(False)
        self.matrix.setStyleSheet(self._table_style())
        self.matrix.horizontalHeader().sectionClicked.connect(self._toggle_column)
        layout.addWidget(self.matrix, 1)
        return box

    # --- roles data ---------------------------------------------------------
    def refresh_roles(self) -> None:
        try:
            roles = self.permissions.list_roles(self.role_search.text())
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        self._roles = {r["id"]: r for r in roles}
        self.roles_table.setRowCount(0)
        for r, role in enumerate(roles):
            self.roles_table.insertRow(r)
            name = QTableWidgetItem(role.get("role_name_ar") or role.get("role_code"))
            name.setData(Qt.UserRole, role["id"])
            active = QTableWidgetItem("نعم" if role.get("is_active") else "لا")
            active.setTextAlignment(Qt.AlignCenter)
            self.roles_table.setItem(r, 0, name)
            self.roles_table.setItem(r, 1, active)

    def _auto_select_first_role(self) -> None:
        if self.roles_table.rowCount() == 0:
            return
        self.roles_table.selectRow(0)
        item = self.roles_table.item(0, 0)
        if item is not None:
            self._load_role(item.data(Qt.UserRole))

    def load_selected_role(self) -> None:
        if self.mode in ("new", "edit"):
            return
        row = self.roles_table.currentRow()
        if row < 0:
            selected = self.roles_table.selectionModel().selectedRows()
            row = selected[0].row() if selected else -1
        if row < 0:
            return
        item = self.roles_table.item(row, 0)
        if item is None:
            return
        target_id = item.data(Qt.UserRole)
        # Re-clicking the already-selected role must NOT reload from the DB,
        # otherwise unsaved checkbox edits would be wiped.
        if target_id == self.current_role_id:
            return
        self._load_role(target_id)

    def _load_role(self, role_id) -> None:
        role = self._roles.get(role_id)
        if not role:
            return
        self.current_role_id = role["id"]
        self.role_code.setText(role.get("role_code") or "")
        self.role_name_ar.setText(role.get("role_name_ar") or "")
        self.role_name_en.setText(role.get("role_name_en") or "")
        self.role_active.setChecked(bool(role.get("is_active")))
        self.role_desc.setPlainText(role.get("description") or "")
        self.set_mode("view")
        self._load_role_permissions()

    def new_role(self) -> None:
        self.current_role_id = None
        for w in (self.role_code, self.role_name_ar, self.role_name_en):
            w.clear()
        self.role_desc.clear()
        self.role_active.setChecked(True)
        self.set_mode("new")

    def save_role(self) -> None:
        data = {
            "role_code": self.role_code.text().strip(),
            "role_name_ar": self.role_name_ar.text().strip() or None,
            "role_name_en": self.role_name_en.text().strip() or None,
            "description": self.role_desc.toPlainText().strip() or None,
            "is_active": self.role_active.isChecked(),
        }
        try:
            if self.mode == "new":
                self.current_role_id = self.permissions.create_role(data)
            else:
                self.permissions.update_role(self.current_role_id, data)
            self.refresh_roles()
            self.set_mode("view")
            QMessageBox.information(self, "تم", "تم حفظ المجموعة.")
        except Exception as exc:
            QMessageBox.critical(self, "تعذر الحفظ", str(exc))

    # --- matrix data --------------------------------------------------------
    def _columns_for_scope(self) -> list[str]:
        if self.scope == "screen":
            return [c for c, _ar, _en in SCREEN_ACTIONS]
        if self.scope == "report":
            return [c for c, _ar, _en in REPORT_ACTIONS]
        seen, cols = set(), []
        for c, _ar, _en in (*SCREEN_ACTIONS, *REPORT_ACTIONS):
            if c not in seen:
                seen.add(c)
                cols.append(c)
        return cols

    def rebuild_matrix(self) -> None:
        try:
            self.rows = self.permissions.build_matrix(self.scope)
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            self.rows = []
        self.active_columns = self._columns_for_scope()
        headers = ["الشاشة / التقرير"] + [ACTION_NAMES_AR.get(c, c) for c in self.active_columns]
        self.matrix.clear()
        self.matrix.setColumnCount(len(headers))
        self.matrix.setHorizontalHeaderLabels(headers)
        self.matrix.setRowCount(len(self.rows))
        self.matrix.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(headers)):
            self.matrix.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for r, row in enumerate(self.rows):
            label = QTableWidgetItem(f"{row['target_name_ar']}  ({'تقرير' if row['permission_type']=='report' else 'شاشة'})")
            label.setFlags(Qt.ItemIsEnabled)
            self.matrix.setItem(r, 0, label)
            for c, action in enumerate(self.active_columns, start=1):
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                pid = row["actions"].get(action)
                if pid is None:
                    item.setText("—")
                    item.setFlags(Qt.NoItemFlags)  # not applicable for this target
                else:
                    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    item.setCheckState(Qt.Unchecked)
                    item.setData(Qt.UserRole, pid)
                self.matrix.setItem(r, c, item)
        self._load_role_permissions()

    def _load_role_permissions(self) -> None:
        if self.current_role_id is None:
            return
        try:
            allowed = self.permissions.get_role_permission_map(self.current_role_id)
        except Exception:
            allowed = {}
        for r in range(self.matrix.rowCount()):
            for c in range(1, self.matrix.columnCount()):
                item = self.matrix.item(r, c)
                if item is None:
                    continue
                pid = item.data(Qt.UserRole)
                if pid is None:
                    continue
                item.setCheckState(Qt.Checked if allowed.get(int(pid), False) else Qt.Unchecked)

    def _on_filter_changed(self) -> None:
        button = self.filter_group.checkedButton()
        if button is None:
            return
        self.scope = button.property("scope")
        self.rebuild_matrix()
        self._apply_search()

    def _apply_search(self) -> None:
        text = self.search_perm.text().strip()
        for r in range(self.matrix.rowCount()):
            item = self.matrix.item(r, 0)
            visible = (not text) or (item is not None and text in item.text())
            self.matrix.setRowHidden(r, not visible)

    def _set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.matrix.rowCount()):
            if self.matrix.isRowHidden(r):
                continue
            for c in range(1, self.matrix.columnCount()):
                item = self.matrix.item(r, c)
                if item and (item.flags() & Qt.ItemIsUserCheckable):
                    item.setCheckState(state)

    def _toggle_column(self, col: int) -> None:
        if col == 0:
            return
        # If any checkable cell in the column is unchecked, check all; else clear.
        any_unchecked = False
        for r in range(self.matrix.rowCount()):
            item = self.matrix.item(r, col)
            if item and (item.flags() & Qt.ItemIsUserCheckable) and item.checkState() != Qt.Checked:
                any_unchecked = True
                break
        state = Qt.Checked if any_unchecked else Qt.Unchecked
        for r in range(self.matrix.rowCount()):
            item = self.matrix.item(r, col)
            if item and (item.flags() & Qt.ItemIsUserCheckable):
                item.setCheckState(state)

    def save_permissions(self) -> None:
        if self.current_role_id is None:
            QMessageBox.information(self, "تنبيه", "اختر مجموعة أولًا.")
            return
        allowed_map: dict[int, bool] = {}
        for r in range(self.matrix.rowCount()):
            for c in range(1, self.matrix.columnCount()):
                item = self.matrix.item(r, c)
                if item is None:
                    continue
                pid = item.data(Qt.UserRole)
                if pid is None:
                    continue
                allowed_map[int(pid)] = item.checkState() == Qt.Checked
        try:
            self.permissions.save_role_permissions(self.current_role_id, allowed_map)
            QMessageBox.information(self, "تم", "تم حفظ صلاحيات المجموعة.")
        except Exception as exc:
            QMessageBox.critical(self, "تعذر الحفظ", str(exc))

    def sync_permissions(self) -> None:
        try:
            summary = self.sync_service.sync()
        except Exception as exc:
            QMessageBox.critical(self, "تعذرت المزامنة", str(exc))
            return
        self.rebuild_matrix()
        QMessageBox.information(
            self, "تمت المزامنة",
            f"إجمالي الصلاحيات: {summary['total']}\nجديدة: {summary['added']}\n"
            f"تم تعطيل: {summary['deactivated']}",
        )

    # --- shared -------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        editing = mode in ("new", "edit")
        can_create = SESSION.can(f"{T}.create")
        can_edit = SESSION.can(f"{T}.edit")
        can_save = SESSION.can(f"{T}.save")
        for w in (self.role_code, self.role_name_ar, self.role_name_en, self.role_active, self.role_desc):
            w.setEnabled(editing)
        self.new_role_btn.setEnabled(not editing and can_create)
        self.edit_role_btn.setEnabled(not editing and self.current_role_id is not None and can_edit)
        self.save_role_btn.setEnabled(editing and can_save)
        self.save_perms_btn.setEnabled(can_save)
        self.check_all_btn.setEnabled(can_save)
        self.clear_all_btn.setEnabled(can_save)

    def _group_style(self) -> str:
        return (
            "QGroupBox { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; margin-top:14px; font-weight:900; }"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:14px; padding:0 10px; color:{GREEN}; }}"
        )

    def _table_style(self) -> str:
        return (
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; gridline-color:#E5E7EB; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:8px; }}"
        )
