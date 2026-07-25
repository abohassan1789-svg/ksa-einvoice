"""Customers module screen (إدارة العملاء).

UI only: it reuses :class:`BaseCrudScreen` bound to the ``customers`` table
spec. Business/database logic stays in ``ReviewDataService``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGroupBox, QMessageBox, QPushButton, QWidget

from app.services import whatsapp_service
from app.services.review_data_service import FieldSpec, ReviewDataService, TABLE_SPECS
from app.ui.screens.base_crud_screen import BaseCrudScreen


class CustomersScreen(BaseCrudScreen):
    SPEC_KEY = "customers"
    AUTO_SEND_DELAY_MS = 25_000

    def __init__(self, service: ReviewDataService, parent: QWidget | None = None) -> None:
        super().__init__(service, TABLE_SPECS[self.SPEC_KEY], parent)

    def _build_field_group(self, title: str, fields: list[FieldSpec]) -> QGroupBox:
        box = super()._build_field_group(title, fields)
        grid = box.layout()

        self.whatsapp_button = QPushButton("WhatsApp")
        self.whatsapp_button.setFixedHeight(38)
        self.whatsapp_button.setMinimumWidth(150)
        self.whatsapp_button.setStyleSheet(
            "QPushButton { background:#25D366; color:#FFFFFF; border:none; "
            "border-radius:6px; padding:7px 16px; font-weight:900; }"
            "QPushButton:hover { background:#1EBE5D; }"
        )
        self.whatsapp_button.clicked.connect(self._open_whatsapp)
        grid.addWidget(self.whatsapp_button, grid.rowCount(), 0, 1, 4, Qt.AlignRight)

        self.attachments_button = QPushButton("المرفقات")
        self.attachments_button.setFixedHeight(38)
        self.attachments_button.setMinimumWidth(150)
        self.attachments_button.setStyleSheet(
            "QPushButton { background:#0891B2; color:#FFFFFF; border:none; "
            "border-radius:6px; padding:7px 16px; font-weight:900; }"
            "QPushButton:hover { background:#0E7490; }"
        )
        self.attachments_button.clicked.connect(self._open_attachments)
        grid.addWidget(self.attachments_button, grid.rowCount(), 0, 1, 4, Qt.AlignRight)
        return box

    def _open_attachments(self) -> None:
        """Open the Attachments screen pre-filtered to the selected customer."""
        if self.current_id is None:
            QMessageBox.information(self, "المرفقات", "اختر عميلاً أولاً لعرض مرفقاته.")
            return
        try:
            from app.ui.screens.attachments_page import AttachmentsPage

            window = AttachmentsPage(entity_type="Customer", entity_id=int(self.current_id))
            window.setWindowTitle(f"KSA - مرفقات العميل {self.current_id}")
            window.setLayoutDirection(Qt.RightToLeft)
            # Keep a reference so the window is not garbage-collected.
            self._attachments_window = window
            window.showMaximized()
            window.raise_()
            window.activateWindow()
        except Exception as exc:  # never break the customer screen
            QMessageBox.critical(self, "تعذر فتح المرفقات", str(exc))

    def _open_whatsapp(self) -> None:
        phone = self.inputs["phone_number"].text()
        if not phone.strip():
            self._show_whatsapp_warning("يرجى إدخال رقم الهاتف أولاً.")
            return
        try:
            whatsapp_service.open_whatsapp_web(phone)
        except ValueError:
            self._show_whatsapp_warning("رقم الهاتف غير صالح. يرجى إدخال رقم موبايل مصري صحيح.")
        except RuntimeError:
            self._show_whatsapp_warning("تعذر فتح واتساب في المتصفح الافتراضي.")
        else:
            self.whatsapp_button.setEnabled(False)
            self.whatsapp_button.setText("جاري الإرسال...")
            QTimer.singleShot(self.AUTO_SEND_DELAY_MS, self._send_whatsapp_message)

    def _send_whatsapp_message(self) -> None:
        try:
            whatsapp_service.press_enter_key()
            QMessageBox.information(
                self,
                "تم الإرسال",
                "تم إرسال رسالة واتساب بنجاح.",
            )
        except RuntimeError:
            QMessageBox.warning(
                self,
                "تعذر إرسال رسالة واتساب",
                "تم فتح واتساب، لكن تعذر الضغط على زر الإرسال تلقائيًا.",
            )
        finally:
            self.whatsapp_button.setText("WhatsApp")
            self.whatsapp_button.setEnabled(True)

    def _show_whatsapp_warning(self, message: str) -> None:
        QMessageBox.warning(self, "تعذر فتح واتساب", message)
