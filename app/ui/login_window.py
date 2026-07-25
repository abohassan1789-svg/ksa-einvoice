"""Login window for the CRM application.

UI only: collects username/password and asks ``AuthService`` to authenticate.
On success it exposes ``self.user`` and ``self.login_log_id`` and accepts.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.auth_service import AuthService
from app.ui.common.theme import GREEN, GREEN_DARK


class LoginWindow(QDialog):
    def __init__(self, auth_service: AuthService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth_service = auth_service
        self.user: dict[str, Any] | None = None
        self.login_log_id: int | None = None
        self.setWindowTitle("تسجيل الدخول - KSA")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFixedSize(440, 480)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet("QFrame { background:#FFFFFF; }")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(12)

        header = QFrame()
        header.setMinimumHeight(120)
        header.setStyleSheet(f"QFrame {{ background:{GREEN_DARK}; border-radius:12px; }}")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 22, 20, 22)
        header_layout.setSpacing(10)
        title = QLabel("إدارة الفواتير الإلكترونية")
        title.setAlignment(Qt.AlignCenter)
        title.setMinimumHeight(40)
        title.setStyleSheet(
            "color:#FFFFFF; font-size:23px; font-weight:800; background:transparent; padding:2px 0;"
        )
        subtitle = QLabel("تسجيل الدخول")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setMinimumHeight(22)
        subtitle.setStyleSheet(
            "color:#BBF7D0; font-size:14px; font-weight:700; background:transparent; padding:2px 0;"
        )
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addWidget(header)
        layout.addSpacing(10)

        layout.addWidget(self._label("اسم المستخدم"))
        self.username = QLineEdit()
        self.username.setPlaceholderText("اسم المستخدم")
        self.username.setText("Admin")
        self._style_input(self.username)
        layout.addWidget(self.username)

        layout.addWidget(self._label("كلمة المرور"))
        self.password = QLineEdit()
        self.password.setPlaceholderText("كلمة المرور")
        self.password.setEchoMode(QLineEdit.Password)
        self._style_input(self.password)
        self.password.returnPressed.connect(self._attempt_login)
        layout.addWidget(self.password)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#DC2626; font-weight:800; background:transparent;")
        layout.addWidget(self.error_label)

        layout.addStretch(1)

        self.login_button = QPushButton("دخول")
        self.login_button.setFixedHeight(44)
        self.login_button.setCursor(Qt.PointingHandCursor)
        self.login_button.setStyleSheet(
            f"QPushButton {{ background:{GREEN}; color:#FFFFFF; border:none; border-radius:8px; "
            "font-size:16px; font-weight:900; }}"
            f"QPushButton:hover {{ background:{GREEN_DARK}; }}"
        )
        self.login_button.clicked.connect(self._attempt_login)
        layout.addWidget(self.login_button)

        cancel = QPushButton("خروج")
        cancel.setFixedHeight(36)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            "QPushButton { background:#FFFFFF; color:#374151; border:1px solid #D1D5DB; "
            "border-radius:8px; font-weight:800; }"
            "QPushButton:hover { background:#F3F4F6; }"
        )
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

        self.password.setFocus()

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color:#111827; font-size:13px; font-weight:800; background:transparent;")
        return label

    def _style_input(self, field: QLineEdit) -> None:
        field.setFixedHeight(42)
        field.setStyleSheet(
            f"QLineEdit {{ background:#FFFFFF; border:1px solid {GREEN}; border-radius:8px; "
            "padding:6px 12px; font-size:15px; font-weight:700; color:#111827; }}"
            f"QLineEdit:focus {{ border:2px solid {GREEN_DARK}; }}"
        )

    def _attempt_login(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        try:
            user, error = self.auth_service.authenticate(username, password)
        except Exception as exc:
            from app.config.database import describe_db_error
            from app.config.settings import get_settings

            cfg = get_settings()
            self.error_label.setText(
                describe_db_error(
                    exc,
                    host=cfg.db_host,
                    port=cfg.db_port,
                    db_name=cfg.db_name,
                    password=cfg.db_password,
                )
            )
            return
        if error:
            self.error_label.setText(error)
            try:
                self.auth_service.record_failed_login(username, error)
            except Exception:
                pass
            return
        self.user = user
        try:
            self.login_log_id = self.auth_service.record_login(user)
        except Exception:
            self.login_log_id = None
        self.accept()
