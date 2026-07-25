"""Connection screen (شاشة الاتصال بالخادم).

One reusable widget, two entry points:

* :class:`FirstRunConnectionDialog` — shown at startup when no valid connection
  profile exists yet, or when the database cannot be reached. The app cannot
  continue until a working connection is saved (or the user cancels).
* :class:`ConnectionSettingsScreen` — the same widget embedded in the main
  window so the administrator can switch between Local (LAN) and Cloud, or point
  at a new hostname/port later, without reinstalling.

The widget mirrors the installer's Connection Mode step and calls the exact same
Python helpers (:mod:`app.services.network_service`,
:mod:`app.config.connection_config`), so behaviour is identical in both places.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import connection_config as cc
from app.services import network_service as net
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK, TEXT, _button_style


class ConnectionSettingsWidget(QWidget):
    """Editable connection profile with Local/Cloud modes and live validation."""

    def __init__(self, profile: cc.ConnectionProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self._profile = profile or cc.load() or cc.ConnectionProfile()
        self._build_ui()
        self._load_into_fields()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("إعدادات الاتصال بالخادم")
        title.setStyleSheet(f"font-size:18px; font-weight:700; color:{GREEN_DARK};")
        root.addWidget(title)

        # --- Mode selector ---
        mode_row = QHBoxLayout()
        self.rb_lan = QRadioButton("شبكة محلية (LAN)")
        self.rb_cloud = QRadioButton("خادم سحابي / عام (Cloud)")
        self.rb_lan.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.rb_lan)
        mode_row.addWidget(self.rb_cloud)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_lan_page())
        self.stack.addWidget(self._build_cloud_page())
        root.addWidget(self.stack)

        # --- Database credentials (shared) ---
        root.addWidget(self._build_db_page())

        # --- Actions ---
        actions = QHBoxLayout()
        self.btn_test = QPushButton("اختبار الاتصال")
        self.btn_test.setStyleSheet(_button_style("#2563EB", "#1D4ED8"))
        self.btn_test.clicked.connect(self.test_connection)
        self.btn_save = QPushButton("حفظ")
        self.btn_save.setStyleSheet(_button_style(GREEN, GREEN_DARK))
        self.btn_save.clicked.connect(self._on_save_clicked)
        actions.addWidget(self.btn_test)
        actions.addStretch(1)
        actions.addWidget(self.btn_save)
        root.addLayout(actions)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch(1)

    def _panel(self) -> tuple[QFrame, QFormLayout]:
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background:#FFFFFF; border:1px solid {BORDER}; border-radius:8px; }}")
        form = QFormLayout(frame)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)
        return frame, form

    def _build_lan_page(self) -> QWidget:
        frame, form = self._panel()
        self.cmb_address = QComboBox()
        self.cmb_address.setEditable(True)  # allow manual entry
        self.cmb_address.setMinimumWidth(240)
        for ip in net.detect_ipv4_addresses():
            self.cmb_address.addItem(ip)

        btn_refresh = QPushButton("تحديث العناوين")
        btn_refresh.clicked.connect(self._refresh_ips)
        addr_row = QHBoxLayout()
        addr_row.addWidget(self.cmb_address, 1)
        addr_row.addWidget(btn_refresh)
        addr_holder = QWidget()
        addr_holder.setLayout(addr_row)

        self.spn_lan_port = QSpinBox()
        self.spn_lan_port.setRange(1, 65535)
        self.spn_lan_port.setValue(cc.DEFAULT_PORT)

        form.addRow("عنوان الخادم (IPv4):", addr_holder)
        form.addRow("المنفذ (Port):", self.spn_lan_port)
        return frame

    def _build_cloud_page(self) -> QWidget:
        frame, form = self._panel()
        self.txt_hostname = QLineEdit()
        self.txt_hostname.setPlaceholderText("erp.company.com  أو  123.45.67.89")
        self.spn_cloud_port = QSpinBox()
        self.spn_cloud_port.setRange(1, 65535)
        self.spn_cloud_port.setValue(cc.DEFAULT_PORT)
        self.chk_https = QCheckBox("تفعيل HTTPS")
        self.chk_proxy = QCheckBox("خلف بروكسي عكسي (Reverse Proxy)")
        self.txt_ssl = QLineEdit()
        self.txt_ssl.setPlaceholderText("مسار شهادة SSL (اختياري)")

        form.addRow("اسم المضيف / النطاق:", self.txt_hostname)
        form.addRow("المنفذ (Port):", self.spn_cloud_port)
        form.addRow("", self.chk_https)
        form.addRow("", self.chk_proxy)
        form.addRow("شهادة SSL:", self.txt_ssl)
        return frame

    def _build_db_page(self) -> QWidget:
        frame, form = self._panel()
        self.txt_db = QLineEdit()
        self.txt_user = QLineEdit()
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        self.cmb_ssl = QComboBox()
        self.cmb_ssl.addItems(["prefer", "require", "disable", "verify-full"])

        form.addRow("اسم قاعدة البيانات:", self.txt_db)
        form.addRow("المستخدم:", self.txt_user)
        form.addRow("كلمة المرور:", self.txt_password)
        form.addRow("وضع SSL:", self.cmb_ssl)
        return frame

    # -- Data binding -----------------------------------------------------

    def _load_into_fields(self) -> None:
        p = self._profile
        if p.connection_mode == cc.MODE_CLOUD:
            self.rb_cloud.setChecked(True)
            self.stack.setCurrentIndex(1)
        else:
            self.rb_lan.setChecked(True)
            self.stack.setCurrentIndex(0)

        if p.server_address:
            self.cmb_address.setCurrentText(p.server_address)
        self.spn_lan_port.setValue(int(p.server_port or cc.DEFAULT_PORT))
        self.txt_hostname.setText(p.cloud_hostname)
        self.spn_cloud_port.setValue(int(p.server_port or cc.DEFAULT_PORT))
        self.chk_https.setChecked(p.https)
        self.chk_proxy.setChecked(p.reverse_proxy)
        self.txt_ssl.setText(p.ssl_cert_path)
        self.txt_db.setText(p.db_name or "crm")
        self.txt_user.setText(p.db_user or "postgres")
        self.txt_password.setText(p.db_password or "")
        self.cmb_ssl.setCurrentText(p.sslmode or "prefer")

    def collect(self) -> cc.ConnectionProfile:
        """Build a profile from the current field values."""
        cloud = self.rb_cloud.isChecked()
        if cloud:
            address = self.txt_hostname.text().strip()
            port = self.spn_cloud_port.value()
        else:
            address = self.cmb_address.currentText().strip()
            port = self.spn_lan_port.value()
        return cc.ConnectionProfile(
            connection_mode=cc.MODE_CLOUD if cloud else cc.MODE_LAN,
            server_address=address,
            server_port=port,
            db_name=self.txt_db.text().strip() or "crm",
            db_user=self.txt_user.text().strip() or "postgres",
            db_password=self.txt_password.text(),
            sslmode=self.cmb_ssl.currentText(),
            cloud_hostname=self.txt_hostname.text().strip() if cloud else "",
            https=self.chk_https.isChecked(),
            reverse_proxy=self.chk_proxy.isChecked(),
            ssl_cert_path=self.txt_ssl.text().strip(),
        )

    # -- Actions ----------------------------------------------------------

    def _on_mode_changed(self) -> None:
        self.stack.setCurrentIndex(0 if self.rb_lan.isChecked() else 1)

    def _refresh_ips(self) -> None:
        current = self.cmb_address.currentText()
        self.cmb_address.clear()
        for ip in net.detect_ipv4_addresses():
            self.cmb_address.addItem(ip)
        if current:
            self.cmb_address.setCurrentText(current)

    def _set_status(self, ok: bool, message: str) -> None:
        color = GREEN_DARK if ok else "#B91C1C"
        self.status.setStyleSheet(f"color:{color}; font-weight:600;")
        self.status.setText(("✓ " if ok else "✗ ") + message)

    def test_connection(self) -> bool:
        profile = self.collect()
        errors = profile.validate()
        if errors:
            self._set_status(False, " | ".join(errors))
            return False
        result = net.test_database(
            host=profile.effective_host, port=profile.server_port,
            db_name=profile.db_name, user=profile.db_user,
            password=profile.db_password, sslmode=profile.sslmode,
        )
        self._set_status(result.ok, result.message)
        return result.ok

    def _on_save_clicked(self) -> None:
        if self.save():
            QMessageBox.information(self, "تم الحفظ", "تم حفظ إعدادات الاتصال بنجاح.")

    def save(self, require_reachable: bool = False) -> bool:
        profile = self.collect()
        errors = profile.validate()
        if errors:
            self._set_status(False, " | ".join(errors))
            return False
        if require_reachable and not self.test_connection():
            return False
        try:
            cc.save(profile)
            self._profile = profile
            self._set_status(True, "تم حفظ الإعدادات.")
            return True
        except Exception as exc:
            self._set_status(False, f"تعذّر حفظ الإعدادات: {exc}")
            return False


class ConnectionSettingsScreen(QWidget):
    """Main-window screen wrapper so the profile can be changed after install."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعدادات الاتصال - KSA")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.widget = ConnectionSettingsWidget(parent=self)
        layout.addWidget(self.widget)


class FirstRunConnectionDialog(QDialog):
    """Blocking dialog shown when no working connection profile exists yet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعداد الاتصال بالخادم - KSA")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        intro = QLabel(
            "لم يتم العثور على إعدادات اتصال صالحة. الرجاء تحديد كيفية اتصال هذا "
            "الجهاز بخادم قاعدة البيانات ثم الضغط على «اختبار الاتصال» ثم «حفظ»."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("padding:14px 20px; background:#FFF7ED; color:#9A3412;")
        layout.addWidget(intro)

        self.widget = ConnectionSettingsWidget(parent=self)
        # Re-point Save so a successful save also closes the dialog.
        self.widget.btn_save.clicked.disconnect()
        self.widget.btn_save.clicked.connect(self._save_and_close)
        layout.addWidget(self.widget)

    def _save_and_close(self) -> None:
        if self.widget.save(require_reachable=True):
            self.accept()


def _profile_is_reachable(profile: cc.ConnectionProfile | None) -> bool:
    """Return True only when the saved profile can reach PostgreSQL now."""
    if profile is None or profile.validate():
        return False
    try:
        result = net.test_database(
            host=profile.effective_host,
            port=profile.server_port,
            db_name=profile.db_name,
            user=profile.db_user,
            password=profile.db_password,
            sslmode=profile.sslmode,
        )
    except Exception:
        return False
    return result.ok


def ensure_connection(parent: QWidget | None = None) -> bool:
    """Return True if a usable connection exists (possibly after prompting).

    Shows :class:`FirstRunConnectionDialog` when no profile is present or the
    saved profile cannot reach PostgreSQL. Returns False only if the user
    dismisses the dialog without saving a reachable profile.
    """
    if _profile_is_reachable(cc.load()):
        return True
    dialog = FirstRunConnectionDialog(parent)
    return dialog.exec() == QDialog.Accepted
