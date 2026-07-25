"""Users Management screen (إدارة المستخدمين).

UI only: layout + interactions. All rules/queries go through ``AuthService`` and
``PermissionService``. Buttons are gated by the current session's permissions on
the ``security.users`` target.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.security.session_context import SESSION
from app.services.auth_service import AuthError, AuthService
from app.services.permission_service import PermissionService
from app.ui.common.theme import GREEN, _button_style

T = "security.users"


class UsersPage(QWidget):
    def __init__(self, auth_service: AuthService, permission_service: PermissionService,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth_service
        self.permissions = permission_service
        self.current_id: int | None = None
        self.mode = "view"
        self.roles: list[dict[str, Any]] = []
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("إدارة المستخدمين")
        self._build_ui()
        self._load_roles()
        self.refresh_table()
        self.set_mode("view")

    # --- layout -------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(self._build_toolbar())
        content = QHBoxLayout()
        content.setSpacing(12)
        content.addWidget(self._build_form(), 1)
        content.addWidget(self._build_list(), 1)
        root.addLayout(content, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.new_button = QPushButton("مستخدم جديد")
        self.edit_button = QPushButton("تعديل")
        self.save_button = QPushButton("حفظ")
        self.toggle_button = QPushButton("إيقاف / تفعيل")
        self.log_button = QPushButton("سجل الدخول")
        self.perms_button = QPushButton("معاينة الصلاحيات")
        for btn, bg, hover in (
            (self.new_button, "#1A7A3C", "#166534"),
            (self.edit_button, "#64748B", "#475569"),
            (self.save_button, "#2563EB", "#1D4ED8"),
            (self.toggle_button, "#B45309", "#92400E"),
            (self.log_button, "#0F766E", "#115E59"),
            (self.perms_button, "#6D28D9", "#5B21B6"),
        ):
            btn.setFixedHeight(38)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_button_style(bg, hover))
            layout.addWidget(btn)
        layout.addStretch(1)
        self.new_button.clicked.connect(self.new_user)
        self.edit_button.clicked.connect(lambda: self.set_mode("edit") if self.current_id else None)
        self.save_button.clicked.connect(self.save_user)
        self.toggle_button.clicked.connect(self.toggle_active)
        self.log_button.clicked.connect(self.show_login_log)
        self.perms_button.clicked.connect(self.preview_permissions)
        return bar

    def _build_form(self) -> QGroupBox:
        box = QGroupBox("بيانات المستخدم")
        box.setStyleSheet(self._group_style())
        form = QFormLayout(box)
        form.setContentsMargins(16, 18, 16, 16)
        form.setSpacing(9)

        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("اتركها فارغة لعدم التغيير")
        self.full_name = QLineEdit()
        self.email = QLineEdit()
        self.phone = QLineEdit()
        self.role_combo = QComboBox()
        self.language_combo = QComboBox()
        self.language_combo.addItem("العربية", "ar")
        self.language_combo.addItem("English", "en")
        self.is_active = QCheckBox("نشط")
        self.is_admin = QCheckBox("مدير")
        self.full_access = QCheckBox("صلاحية كاملة")
        self.notes = QTextEdit()
        self.notes.setFixedHeight(60)
        self.last_login = QLabel("-")
        self.last_login.setStyleSheet("color:#374151; font-weight:700;")

        for w in (self.username, self.password, self.full_name, self.email, self.phone):
            w.setFixedHeight(34)
        self.role_combo.setFixedHeight(34)
        self.language_combo.setFixedHeight(34)

        form.addRow("اسم المستخدم", self.username)
        form.addRow("كلمة المرور", self.password)
        form.addRow("الاسم الكامل", self.full_name)
        form.addRow("البريد", self.email)
        form.addRow("الهاتف", self.phone)
        form.addRow("المجموعة", self.role_combo)
        form.addRow("اللغة", self.language_combo)
        flags = QHBoxLayout()
        flags.addWidget(self.is_active)
        flags.addWidget(self.is_admin)
        flags.addWidget(self.full_access)
        flags_w = QWidget()
        flags_w.setLayout(flags)
        form.addRow("الحالة", flags_w)
        form.addRow("ملاحظات", self.notes)
        form.addRow("آخر دخول", self.last_login)
        return box

    def _build_list(self) -> QGroupBox:
        box = QGroupBox("المستخدمون")
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 12)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("بحث بالاسم أو البريد أو الهاتف")
        self.search.setFixedHeight(36)
        self.search.textChanged.connect(self.refresh_table)
        layout.addWidget(self.search)

        self.table = QTableWidget()
        self.columns = ["id", "username", "full_name", "role", "active", "admin"]
        headers = ["#", "اسم المستخدم", "الاسم", "المجموعة", "نشط", "مدير"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; gridline-color:#E5E7EB; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:8px; }}"
        )
        self.table.itemSelectionChanged.connect(self.load_selected)
        layout.addWidget(self.table, 1)
        return box

    def _group_style(self) -> str:
        return (
            "QGroupBox { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; margin-top:14px; font-weight:900; }"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:14px; padding:0 10px; color:{GREEN}; }}"
        )

    # --- data ---------------------------------------------------------------
    def _load_roles(self) -> None:
        try:
            self.roles = self.permissions.list_roles()
        except Exception:
            self.roles = []
        self.role_combo.clear()
        self.role_combo.addItem("— بدون مجموعة —", None)
        for role in self.roles:
            self.role_combo.addItem(role.get("role_name_ar") or role.get("role_code"), role["id"])

    def refresh_table(self) -> None:
        try:
            users = self.auth.list_users(self.search.text())
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        self._users = {u["id"]: u for u in users}
        self.table.setRowCount(0)
        for r, u in enumerate(users):
            self.table.insertRow(r)
            values = [
                str(u["id"]), u.get("username", ""), u.get("full_name") or "",
                u.get("role_name_ar") or "",
                "نعم" if u.get("is_active") else "لا",
                "نعم" if (u.get("is_admin") or u.get("full_access")) else "لا",
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if c == 0:
                    item.setData(Qt.UserRole, u["id"])
                self.table.setItem(r, c, item)

    def load_selected(self) -> None:
        if self.mode in ("new", "edit"):
            return
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        user = self._users.get(item.data(Qt.UserRole))
        if not user:
            return
        self.current_id = user["id"]
        self.username.setText(user.get("username", ""))
        self.password.clear()
        self.full_name.setText(user.get("full_name") or "")
        self.email.setText(user.get("email") or "")
        self.phone.setText(user.get("phone") or "")
        idx = self.role_combo.findData(user.get("role_id"))
        self.role_combo.setCurrentIndex(max(0, idx))
        lang_idx = self.language_combo.findData(user.get("language") or "ar")
        self.language_combo.setCurrentIndex(max(0, lang_idx))
        self.is_active.setChecked(bool(user.get("is_active")))
        self.is_admin.setChecked(bool(user.get("is_admin")))
        self.full_access.setChecked(bool(user.get("full_access")))
        self.notes.setPlainText(user.get("notes") or "")
        self.last_login.setText(str(user.get("last_login_at") or "-"))
        self.set_mode("view")

    # --- actions ------------------------------------------------------------
    def new_user(self) -> None:
        self.current_id = None
        for w in (self.username, self.password, self.full_name, self.email, self.phone):
            w.clear()
        self.notes.clear()
        self.role_combo.setCurrentIndex(0)
        self.language_combo.setCurrentIndex(0)
        self.is_active.setChecked(True)
        self.is_admin.setChecked(False)
        self.full_access.setChecked(False)
        self.last_login.setText("-")
        self.set_mode("new")

    def _form_payload(self) -> dict[str, Any]:
        return {
            "username": self.username.text().strip(),
            "full_name": self.full_name.text().strip() or None,
            "email": self.email.text().strip() or None,
            "phone": self.phone.text().strip() or None,
            "role_id": self.role_combo.currentData(),
            "language": self.language_combo.currentData(),
            "is_active": self.is_active.isChecked(),
            "is_admin": self.is_admin.isChecked(),
            "full_access": self.full_access.isChecked(),
            "notes": self.notes.toPlainText().strip() or None,
        }

    def save_user(self) -> None:
        payload = self._form_payload()
        password = self.password.text()
        try:
            if self.mode == "new":
                self.current_id = self.auth.create_user(payload, password)
            else:
                self.auth.update_user(self.current_id, payload, password or None)
            self.refresh_table()
            self.set_mode("view")
            self._select_row(self.current_id)
            QMessageBox.information(self, "تم", "تم حفظ المستخدم بنجاح.")
        except (AuthError, Exception) as exc:
            QMessageBox.critical(self, "تعذر الحفظ", str(exc))

    def toggle_active(self) -> None:
        if not self.current_id:
            return
        user = self._users.get(self.current_id)
        new_state = not bool(user.get("is_active")) if user else True
        try:
            self.auth.set_user_active(self.current_id, new_state)
            self.refresh_table()
            self._select_row(self.current_id)
        except Exception as exc:
            QMessageBox.critical(self, "تعذر التنفيذ", str(exc))

    def show_login_log(self) -> None:
        if not self.current_id:
            QMessageBox.information(self, "تنبيه", "اختر مستخدمًا أولًا.")
            return
        try:
            logs = self.auth.list_user_logins(self.current_id)
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        self._show_table_dialog("سجل الدخول", ["الدخول", "الخروج", "الحالة", "الجهاز", "ملاحظات"],
                                [[str(l.get("login_time") or ""), str(l.get("logout_time") or ""),
                                  l.get("status") or "", l.get("device_name") or "",
                                  l.get("notes") or ""] for l in logs])

    def preview_permissions(self) -> None:
        if not self.current_id:
            QMessageBox.information(self, "تنبيه", "اختر مستخدمًا أولًا.")
            return
        user = self._users.get(self.current_id)
        try:
            perms = self.permissions.repository.list_permissions(active_only=True)
            resolved = self.permissions.resolve_user(user)
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        data = []
        for p in perms:
            info = resolved.get(int(p["id"]), {})
            data.append([
                f"{p['target_name_ar']} - {p['action_name_ar']}",
                "مسموح" if info.get("allowed") else "ممنوع",
                {"full_access": "صلاحية كاملة", "override": "تخصيص", "role": "المجموعة"}.get(info.get("source"), ""),
            ])
        self._show_table_dialog("الصلاحيات الفعّالة", ["الصلاحية", "النتيجة", "المصدر"], data)

    # --- helpers ------------------------------------------------------------
    def _select_row(self, user_id: int | None) -> None:
        if user_id is None:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.UserRole) == user_id:
                self.table.selectRow(row)
                return

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        editing = mode in ("new", "edit")
        can_create = SESSION.can(f"{T}.create")
        can_edit = SESSION.can(f"{T}.edit")
        can_save = SESSION.can(f"{T}.save")
        for w in (self.full_name, self.email, self.phone, self.role_combo, self.language_combo,
                  self.is_active, self.is_admin, self.full_access, self.notes, self.password):
            w.setEnabled(editing)
        self.username.setEnabled(mode == "new")
        self.new_button.setEnabled(not editing and can_create)
        self.edit_button.setEnabled(not editing and self.current_id is not None and can_edit)
        self.save_button.setEnabled(editing and can_save)
        self.toggle_button.setEnabled(not editing and self.current_id is not None and can_edit)

    def _show_table_dialog(self, title: str, headers: list[str], rows: list[list[str]]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLayoutDirection(Qt.RightToLeft)
        dialog.resize(680, 460)
        layout = QVBoxLayout(dialog)
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(r, c, item)
        layout.addWidget(table)
        close = QPushButton("إغلاق")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
