"""User Permissions Override screen (صلاحيات المستخدم).

Shows, per user, the role-granted value, an inherit/allow/deny override, and the
resulting effective permission. UI only; logic via the services.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.security.session_context import SESSION
from app.services.auth_service import AuthService
from app.services.permission_service import ALLOW, DENY, INHERIT, PermissionService
from app.ui.common.theme import GREEN, _button_style

T = "security.user_permissions"


class _NoScrollComboBox(QComboBox):
    """Combo box that ignores the mouse wheel so scrolling the table does not
    accidentally change the inherit/allow/deny value of a row."""

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.ignore()


class UserPermissionsPage(QWidget):
    def __init__(self, auth_service: AuthService, permission_service: PermissionService,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth_service
        self.permissions = permission_service
        self.user: dict[str, Any] | None = None
        self.scope = "all"
        self.perms: list[dict[str, Any]] = []
        self.role_map: dict[int, bool] = {}
        self.user_map: dict[int, bool] = {}
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("صلاحيات المستخدم")
        self._build_ui()
        self._load_users()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("المستخدم:"))
        self.user_combo = QComboBox()
        self.user_combo.setFixedHeight(34)
        self.user_combo.setMinimumWidth(240)
        self.user_combo.currentIndexChanged.connect(self._on_user_changed)
        top.addWidget(self.user_combo)

        self.filter_group = QButtonGroup(self)
        for idx, (code, label) in enumerate(
            (("all", "الكل"), ("screen", "الشاشات"), ("report", "التقارير"), ("overrides", "التخصيصات فقط"))
        ):
            rb = QRadioButton(label)
            rb.setProperty("scope", code)
            if code == "all":
                rb.setChecked(True)
            rb.toggled.connect(self._on_filter_changed)
            self.filter_group.addButton(rb, idx)
            top.addWidget(rb)

        top.addStretch(1)
        self.reset_btn = QPushButton("تصفير التخصيصات")
        self.reset_btn.setFixedHeight(34)
        self.reset_btn.setStyleSheet(_button_style("#B45309", "#92400E"))
        self.reset_btn.clicked.connect(self.reset_overrides)
        top.addWidget(self.reset_btn)
        root.addLayout(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("بحث في الصلاحيات")
        self.search.setFixedHeight(32)
        self.search.textChanged.connect(self._apply_search)
        root.addWidget(self.search)

        self.note = QLabel("")
        self.note.setStyleSheet("color:#B45309; font-weight:800;")
        root.addWidget(self.note)

        self.table = QTableWidget()
        headers = ["الشاشة / التقرير", "الإجراء", "صلاحية المجموعة", "التخصيص", "النتيجة الفعّالة"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; gridline-color:#E5E7EB; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:8px; }}"
        )
        root.addWidget(self.table, 1)

    # --- data ---------------------------------------------------------------
    def _load_users(self) -> None:
        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        self.user_combo.addItem("— اختر مستخدمًا —", None)
        try:
            for u in self.auth.list_users():
                label = f"{u['username']} - {u.get('full_name') or ''}".strip(" -")
                self.user_combo.addItem(label, u["id"])
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
        self.user_combo.blockSignals(False)

    def _on_user_changed(self) -> None:
        user_id = self.user_combo.currentData()
        if user_id is None:
            self.user = None
            self.table.setRowCount(0)
            self.note.setText("")
            return
        self.user = self.auth.get_user(user_id)
        self._reload_maps()
        self.rebuild_table()

    def _reload_maps(self) -> None:
        if not self.user:
            return
        try:
            self.perms = self.permissions.repository.list_permissions(active_only=True)
            self.role_map = (self.permissions.get_role_permission_map(self.user["role_id"])
                             if self.user.get("role_id") else {})
            self.user_map = self.permissions.get_user_permission_map(self.user["id"])
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            self.perms, self.role_map, self.user_map = [], {}, {}
        if self.user.get("is_admin") or self.user.get("full_access"):
            self.note.setText("هذا المستخدم لديه صلاحية كاملة — كل الصلاحيات مسموحة بغض النظر عن التخصيص.")
        else:
            self.note.setText("")

    def _visible_perms(self) -> list[dict[str, Any]]:
        result = []
        for p in self.perms:
            if self.scope == "screen" and p["permission_type"] != "screen":
                continue
            if self.scope == "report" and p["permission_type"] != "report":
                continue
            if self.scope == "overrides" and int(p["id"]) not in self.user_map:
                continue
            result.append(p)
        return result

    def rebuild_table(self) -> None:
        rows = self._visible_perms()
        can_edit = SESSION.can(f"{T}.edit") and SESSION.can(f"{T}.save")
        is_full = bool(self.user and (self.user.get("is_admin") or self.user.get("full_access")))
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            pid = int(p["id"])
            base = bool(self.role_map.get(pid, False))
            self.table.setItem(r, 0, self._cell(p["target_name_ar"]))
            self.table.setItem(r, 1, self._cell(p["action_name_ar"], center=True))
            self.table.setItem(r, 2, self._cell("مسموح" if base else "ممنوع", center=True))

            combo = _NoScrollComboBox()
            combo.addItem("وراثة من المجموعة", INHERIT)
            combo.addItem("سماح", ALLOW)
            combo.addItem("منع", DENY)
            if pid in self.user_map:
                combo.setCurrentIndex(1 if self.user_map[pid] else 2)
            else:
                combo.setCurrentIndex(0)
            combo.setEnabled(can_edit and not is_full)
            combo.setProperty("pid", pid)
            combo.currentIndexChanged.connect(lambda _i, cb=combo, row=r: self._on_override_changed(cb, row))
            self.table.setCellWidget(r, 3, combo)

            self.table.setItem(r, 4, self._effective_cell(pid, base, is_full))
        self._apply_search()

    def _effective_cell(self, pid: int, base: bool, is_full: bool) -> QTableWidgetItem:
        if is_full:
            allowed = True
        elif pid in self.user_map:
            allowed = self.user_map[pid]
        else:
            allowed = base
        item = self._cell("مسموح" if allowed else "ممنوع", center=True)
        item.setForeground(QColor("#137A38") if allowed else QColor("#DC2626"))
        return item

    def _on_override_changed(self, combo: QComboBox, row: int) -> None:
        if not self.user:
            return
        pid = combo.property("pid")
        state = combo.currentData()
        try:
            self.permissions.set_user_override(self.user["id"], int(pid), state)
        except Exception as exc:
            QMessageBox.critical(self, "تعذر الحفظ", str(exc))
            return
        # Update local map + effective cell.
        if state == INHERIT:
            self.user_map.pop(int(pid), None)
        else:
            self.user_map[int(pid)] = (state == ALLOW)
        base = bool(self.role_map.get(int(pid), False))
        is_full = bool(self.user.get("is_admin") or self.user.get("full_access"))
        self.table.setItem(row, 4, self._effective_cell(int(pid), base, is_full))

    def reset_overrides(self) -> None:
        if not self.user:
            QMessageBox.information(self, "تنبيه", "اختر مستخدمًا أولًا.")
            return
        if not (SESSION.can(f"{T}.edit") and SESSION.can(f"{T}.save")):
            QMessageBox.warning(self, "غير مسموح", "ليس لديك صلاحية تعديل التخصيصات.")
            return
        if QMessageBox.question(self, "تأكيد", "تصفير كل التخصيصات لهذا المستخدم؟") != QMessageBox.Yes:
            return
        try:
            self.permissions.reset_user_overrides(self.user["id"])
        except Exception as exc:
            QMessageBox.critical(self, "خطأ", str(exc))
            return
        self.user_map = {}
        self.rebuild_table()

    def _on_filter_changed(self) -> None:
        button = self.filter_group.checkedButton()
        if button is None:
            return
        self.scope = button.property("scope")
        if self.user:
            self.rebuild_table()

    def _apply_search(self) -> None:
        text = self.search.text().strip()
        for r in range(self.table.rowCount()):
            target = self.table.item(r, 0)
            action = self.table.item(r, 1)
            hay = f"{target.text() if target else ''} {action.text() if action else ''}"
            self.table.setRowHidden(r, bool(text) and text not in hay)

    def _cell(self, text: str, center: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text or "")
        if center:
            item.setTextAlignment(Qt.AlignCenter)
        return item
