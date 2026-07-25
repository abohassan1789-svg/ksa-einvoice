"""Backup Management screen (شاشة إدارة النسخ الاحتياطي).

UI only. All backup logic (running pg_dump, writing plain-SQL files, recording
outcomes, retention, settings) lives in :class:`BackupService` (service layer)
and :class:`BackupRepository` (database layer). This screen never touches the
database or the filesystem directly — it calls the service and renders results.

The backup output is always a plain-text ``.sql`` file; this screen only creates,
lists, and deletes those backups and toggles the optional startup/shutdown/
retention settings.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.security.session_context import SESSION
from app.services.backup_service import BackupService
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK, TEXT

BG = "#F1F5F9"
CARD_BG = "#FFFFFF"
TEXT_MUTED = "#64748B"

# Human-readable Arabic labels for stored codes.
TYPE_LABELS = {
    "manual": "يدوي",
    "startup": "بدء التشغيل",
    "shutdown": "الإغلاق",
}
STATUS_LABELS = {
    "success": "ناجحة",
    "failed": "فاشلة",
}

# (stored key, Arabic column header) — order matches the requested layout.
TABLE_COLUMNS = (
    ("id", "الكود"),
    ("created_at", "التاريخ والوقت"),
    ("backup_type", "النوع"),
    ("file_name", "اسم الملف"),
    ("file_path", "المسار"),
    ("status", "الحالة"),
    ("error_message", "رسالة الخطأ"),
    ("created_by", "أنشئ بواسطة"),
)


def _button(text: str, bg: str, hover: str, fg: str = "#FFFFFF") -> QPushButton:
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


class BackupManagementScreen(QWidget):
    """Manual backups, backup history, and optional startup/shutdown settings."""

    def __init__(self, service: BackupService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service or BackupService()

        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(f"BackupManagementScreen {{ background:{BG}; }}")
        self._build_ui()

        try:
            self.service.ensure_schema()
        except Exception:  # never block the screen if the DB is momentarily down
            pass
        self._load_settings()
        self.refresh()

    # --- layout -------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)
        outer.addWidget(self._build_header())
        outer.addWidget(self._build_toolbar())
        outer.addWidget(self._build_settings_card())
        outer.addWidget(self._build_status_bar())
        outer.addWidget(self._build_table(), 1)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background:{GREEN}; border-radius:12px; }} "
            "QLabel { background:transparent; color:#FFFFFF; }"
        )
        box = QHBoxLayout(header)
        box.setContentsMargins(20, 16, 20, 16)
        icon = QLabel("🗄")
        icon.setFixedSize(54, 54)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            "background:rgba(255,255,255,0.16); border:1px solid rgba(255,255,255,0.28); "
            "border-radius:10px; font-size:26px;"
        )
        text_box = QVBoxLayout()
        title = QLabel("إدارة النسخ الاحتياطي")
        title.setStyleSheet("font-size:26px; font-weight:900;")
        subtitle = QLabel("نسخ احتياطية بصيغة SQL نصية باستخدام pg_dump")
        subtitle.setStyleSheet("font-size:13px; font-weight:700; color:#E8FBEF;")
        text_box.addWidget(title)
        text_box.addWidget(subtitle)
        box.addWidget(icon)
        box.addLayout(text_box, 1)
        return header

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(7)

        self.backup_button = _button("نسخة احتياطية يدوية", "#1A7A3C", "#166534")
        self.open_folder_button = _button("فتح مجلد النسخ", GREEN, GREEN_DARK)
        self.refresh_button = _button("تحديث السجل", "#2563EB", "#1D4ED8")
        self.delete_button = _button("حذف النسخة المحددة", "#DC2626", "#B91C1C")

        for button in (
            self.backup_button, self.open_folder_button,
            self.refresh_button, self.delete_button,
        ):
            row.addWidget(button)
        row.addStretch(1)

        self.backup_button.clicked.connect(self.manual_backup)
        self.open_folder_button.clicked.connect(self.open_backups_folder)
        self.refresh_button.clicked.connect(self.refresh)
        self.delete_button.clicked.connect(self.delete_selected)
        return bar

    def _build_settings_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        heading = QLabel("الإعدادات")
        heading.setStyleSheet(f"color:{GREEN}; font-size:14px; font-weight:900; border:none;")
        grid.addWidget(heading, 0, 0, 1, 4)

        self.startup_checkbox = QCheckBox("إنشاء نسخة احتياطية عند بدء التشغيل")
        self.shutdown_checkbox = QCheckBox("إنشاء نسخة احتياطية عند الإغلاق")
        for box in (self.startup_checkbox, self.shutdown_checkbox):
            box.setStyleSheet(f"font-weight:800; color:{TEXT}; border:none;")
        grid.addWidget(self.startup_checkbox, 1, 0, 1, 2)
        grid.addWidget(self.shutdown_checkbox, 1, 2, 1, 2)

        max_label = QLabel("أقصى عدد للنسخ المحفوظة (0 = بلا حد):")
        max_label.setStyleSheet(f"font-weight:800; color:{TEXT}; border:none;")
        self.max_spin = QSpinBox()
        self.max_spin.setRange(0, 100000)
        self.max_spin.setFixedWidth(120)
        self.max_spin.setStyleSheet(
            f"QSpinBox {{ background:#FFFFFF; border:1px solid {GREEN}; border-radius:8px; "
            f"padding:5px 8px; font-weight:800; color:{TEXT}; }}"
        )
        grid.addWidget(max_label, 2, 0)
        grid.addWidget(self.max_spin, 2, 1)

        self.save_settings_button = _button("حفظ الإعدادات", "#7C3AED", "#6D28D9")
        self.save_settings_button.clicked.connect(self.save_settings)
        grid.addWidget(self.save_settings_button, 2, 3, alignment=Qt.AlignLeft)

        grid.setColumnStretch(2, 1)
        return card

    def _build_status_bar(self) -> QLabel:
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "background:transparent; color:#64748B; font-size:13px; font-weight:800; padding:0 4px;"
        )
        return self.status_label

    def _build_table(self) -> QTableWidget:
        self.table = QTableWidget()
        self.table.setColumnCount(len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _key, label in TABLE_COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; "
            "border:1px solid #E2E8F0; gridline-color:#E5E7EB; font-size:12px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; "
            "border:none; padding:9px 6px; }}"
            "QTableWidget::item:selected { background:#DDF3E6; color:#111827; }"
        )
        return self.table

    # --- data flow ----------------------------------------------------------
    def refresh(self) -> None:
        """Reload the backup history table from backup_logs."""
        try:
            rows = self.service.list_backups()
        except Exception as exc:  # noqa: BLE001
            self._error("تعذر تحميل سجل النسخ الاحتياطي", exc)
            return
        self.table.setRowCount(0)
        for row_index, record in enumerate(rows):
            self.table.insertRow(row_index)
            for col_index, (key, _label) in enumerate(TABLE_COLUMNS):
                item = QTableWidgetItem(self._cell_text(key, record))
                item.setTextAlignment(Qt.AlignCenter)
                if col_index == 0:
                    item.setData(Qt.UserRole, record.get("id"))
                if key == "status":
                    item.setForeground(
                        Qt.GlobalColor.darkGreen
                        if record.get("status") == "success"
                        else Qt.GlobalColor.red
                    )
                self.table.setItem(row_index, col_index, item)
        self._info(f"عدد النسخ في السجل: {len(rows)}")

    def _cell_text(self, key: str, record: dict[str, Any]) -> str:
        value = record.get(key)
        if key == "backup_type":
            return TYPE_LABELS.get(value, value or "")
        if key == "status":
            return STATUS_LABELS.get(value, value or "")
        if key == "created_at" and value is not None:
            return str(value)[:19]
        if key == "created_by":
            return "—" if value in (None, "") else str(value)
        return "" if value is None else str(value)

    # --- actions ------------------------------------------------------------
    def manual_backup(self) -> None:
        self.backup_button.setEnabled(False)
        self._info("جارٍ إنشاء نسخة احتياطية…")
        try:
            result = self.service.create_backup("manual", created_by=self._current_user_id())
        except Exception as exc:  # noqa: BLE001 - unexpected; still keep UI alive
            self.backup_button.setEnabled(True)
            self._error("تعذر إنشاء النسخة الاحتياطية", exc)
            return
        self.backup_button.setEnabled(True)
        self.refresh()
        if result.success:
            self._info(f"تم إنشاء النسخة الاحتياطية بنجاح: {result.file_name}")
            QMessageBox.information(
                self, "تمت العملية",
                f"تم إنشاء النسخة الاحتياطية بنجاح:\n{result.file_name}",
            )
        else:
            message = result.error_message or "سبب غير معروف."
            self._warn(f"فشل إنشاء النسخة الاحتياطية: {message}")
            QMessageBox.critical(
                self, "فشل النسخ الاحتياطي",
                f"تعذر إنشاء النسخة الاحتياطية.\n\nالسبب:\n{message}",
            )

    def open_backups_folder(self) -> None:
        try:
            folder = self.service.ensure_backup_dir()
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            self._info(f"تم فتح المجلد: {folder}")
        except Exception as exc:  # noqa: BLE001
            self._error("تعذر فتح مجلد النسخ الاحتياطي", exc)

    def delete_selected(self) -> None:
        log_id = self._selected_id()
        if log_id is None:
            self._warn("اختر نسخة احتياطية من الجدول أولًا.")
            return
        answer = QMessageBox.question(
            self, "تأكيد الحذف",
            "هل تريد حذف النسخة الاحتياطية المحددة؟\n"
            "سيتم حذف ملف SQL من مجلد النسخ وحذف سجله.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.service.delete_backup(log_id)
            self.refresh()
            self._info("تم حذف النسخة الاحتياطية المحددة.")
        except Exception as exc:  # noqa: BLE001
            self._error("تعذر حذف النسخة الاحتياطية", exc)

    def save_settings(self) -> None:
        try:
            self.service.save_settings({
                "backup_on_startup": self.startup_checkbox.isChecked(),
                "backup_on_shutdown": self.shutdown_checkbox.isChecked(),
                "max_backups": int(self.max_spin.value()),
            })
            self._info("تم حفظ الإعدادات.")
        except Exception as exc:  # noqa: BLE001
            self._error("تعذر حفظ الإعدادات", exc)

    def _load_settings(self) -> None:
        settings = self.service.get_settings()
        self.startup_checkbox.setChecked(bool(settings.get("backup_on_startup", True)))
        self.shutdown_checkbox.setChecked(bool(settings.get("backup_on_shutdown", True)))
        try:
            self.max_spin.setValue(int(settings.get("max_backups", 20)))
        except (TypeError, ValueError):
            self.max_spin.setValue(20)

    # --- helpers ------------------------------------------------------------
    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _current_user_id() -> int | None:
        user = SESSION.user or {}
        try:
            return int(user.get("id")) if user.get("id") is not None else None
        except (TypeError, ValueError):
            return None

    def _info(self, message: str) -> None:
        self.status_label.setStyleSheet(
            "background:transparent; color:#087443; font-size:13px; font-weight:800; padding:0 4px;"
        )
        self.status_label.setText(message)

    def _warn(self, message: str) -> None:
        self.status_label.setStyleSheet(
            "background:transparent; color:#B45309; font-size:13px; font-weight:800; padding:0 4px;"
        )
        self.status_label.setText(message)

    def _error(self, title: str, exc: Exception) -> None:
        self.status_label.setStyleSheet(
            "background:transparent; color:#B91C1C; font-size:13px; font-weight:800; padding:0 4px;"
        )
        self.status_label.setText(f"{title}: {exc}")
        QMessageBox.critical(self, title, str(exc))
