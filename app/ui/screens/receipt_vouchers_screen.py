"""Receipt Vouchers module screen (سندات القبض).

UI only: it reuses :class:`BaseCrudScreen` bound to the ``receipt_vouchers``
table spec, so it inherits the exact same layout, colours, borders, toolbar and
CRUD logic as the companies screen (same design family). Business/database logic
stays in ``ReviewDataService`` + ``TABLE_SPECS['receipt_vouchers']``; the search
grid (رقم السند / اسم العميل / التاريخ / المبلغ) and its search entries come from
the spec and the service's joined receipt-voucher query.

Receipt-voucher-specific touches layered on top of the generic screen:

* ``العميل`` / ``الشركة`` are dropdowns backed by the existing master tables —
  the stored value is the foreign-key id, never the display name.
* ``نوع الدفع`` is a dropdown of the two supported codes shown with their Arabic
  labels: ``cash`` = كاش and ``bank_transfer`` = تحويل.
* ``رقم السند`` auto-numbers from the PostgreSQL sequence (PA-001, PA-002,
  ...) when left empty — the database DEFAULT assigns it atomically (never
  MAX+1). The field stays editable: a manual number is validated for uniqueness
  *before* the save with a clear Arabic message (the UNIQUE index remains the
  final guard), and after a successful save the sequence is advanced past a
  manual ``PA-<n>`` so future automatic numbers can never collide.
* ``المبلغ`` must be a number greater than zero — rejected before the save with
  an Arabic message (the DB CHECK constraint remains the final guard).
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter, QMessageBox, QPushButton, QWidget

from app.services.receipt_voucher_numbering_service import ReceiptVoucherNumberingService
from app.services.review_data_service import FieldSpec, ReviewDataService, TABLE_SPECS
from app.ui.common.theme import _button_style
from app.ui.screens.base_crud_screen import BaseCrudScreen

# Stored payment-type codes with their Arabic display labels (the DB CHECK
# constraint allows exactly these two codes).
PAYMENT_CHOICES: tuple[tuple[str, str], ...] = (
    ("cash", "كاش"),
    ("bank_transfer", "تحويل"),
)


class ReceiptVouchersScreen(BaseCrudScreen):
    SPEC_KEY = "receipt_vouchers"

    # Vouchers are searched by voucher number, customer name, or date.
    SEARCH_PLACEHOLDER = "البحث برقم السند أو اسم العميل أو التاريخ"

    # Each field on its own line (single-column form), like the companies screen.
    FORM_COLUMNS = 1

    def __init__(self, service: ReviewDataService, parent: QWidget | None = None) -> None:
        super().__init__(service, TABLE_SPECS[self.SPEC_KEY], parent)

    # --- print voucher (طباعة سند) -------------------------------------------
    def _install_extra_toolbar_buttons(self, layout) -> None:
        """Add a "طباعة سند" button that previews/prints the current voucher.

        Uses the exact same preview → PDF/print flow and final layout as the
        sales-invoice receipt-voucher printout (:mod:`saudi_receipt_voucher_print`).
        """
        self.print_voucher_button = QPushButton("طباعة سند")
        self.print_voucher_button.setFixedHeight(38)
        self.print_voucher_button.setStyleSheet(_button_style("#4338CA", "#3730A3"))
        self.print_voucher_button.clicked.connect(self.print_voucher)
        layout.addWidget(self.print_voucher_button)

    def print_voucher(self) -> None:
        data = self._collect_voucher_print_data()
        if data is None:
            QMessageBox.information(
                self, "طباعة سند", "اختر سنداً من القائمة أو أدخل بياناته أولاً."
            )
            return
        from app.ui.screens.saudi_receipt_voucher_print import (
            SaudiReceiptVoucherPreviewDialog,
            choose_voucher_builder,
        )

        builder = choose_voucher_builder(self)
        if builder is None:
            return
        dialog = SaudiReceiptVoucherPreviewDialog(data, parent=self, html_builder=builder)
        dialog.exec()

    def _collect_voucher_print_data(self) -> dict[str, Any] | None:
        """Reshape the on-screen voucher into the receipt-voucher print template.

        Reads the currently displayed voucher straight from the form editors so a
        selected (saved) voucher prints exactly as shown. The company (seller)
        header — name / CR / VAT / address — is fetched fresh from the companies
        master so it matches the Saudi tax-invoice header.
        """
        def _text(name: str) -> str:
            editor = self.inputs.get(name)
            return editor.text().strip() if editor is not None and hasattr(editor, "text") else ""

        number = _text("voucher_number")
        date_text = _text("voucher_date")
        purpose = _text("description")
        amount_text = _text("amount")

        customer_combo = getattr(self, "voucher_customer_combo", None)
        company_combo = getattr(self, "voucher_company_combo", None)
        received_from = customer_combo.currentText().strip() if customer_combo is not None else ""
        company_id = company_combo.currentData() if company_combo is not None else None
        company_name = company_combo.currentText().strip() if company_combo is not None else ""

        # Fresh, empty screen with nothing selected — nothing to print.
        if self.current_id is None and not (received_from or amount_text or company_name):
            return None

        seller = {"name": company_name, "cr": "", "vat": "", "address": ""}
        if company_id not in (None, ""):
            try:
                company = self.service.get_company_details(company_id) or {}
            except Exception:
                company = {}
            seller = {
                "name": company.get("name_ar") or company_name,
                "name_en": company.get("name_en") or "",
                "cr": company.get("commercial_registration") or "",
                "vat": company.get("vat_number") or "",
                "address": company.get("address_ar") or "",
                "address_en": company.get("address_en") or company.get("address_ar") or "",
                "logo": company.get("logo"),
                "logo_mime": company.get("logo_mime"),
            }

        # The date editor holds YYYY-MM-DD text; hand the template a real date so
        # it renders dd-mm-yyyy (falls back to the raw text if it is not parseable).
        issue_date: Any = date_text
        try:
            issue_date = datetime.datetime.strptime(date_text, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

        try:
            amount: Any = Decimal(amount_text)
        except (InvalidOperation, ValueError, TypeError):
            amount = Decimal("0")

        # The Arabic label («كاش» / «تحويل»), not the stored code — النموذج الرابع
        # prints it. `receipt_vouchers.payment_type` has always held it; no
        # template asked for it until now.
        combo = getattr(self, "payment_type_combo", None)
        payment_type = combo.currentText().strip() if combo is not None else ""

        return {
            "number": number,
            "issue_date": issue_date,
            "amount": amount,
            "received_from": received_from,
            "purpose": purpose,
            "payment_type": payment_type,
            "seller": seller,
        }

    # --- editors -------------------------------------------------------------
    def _make_editor(self, field: FieldSpec):
        if field.name == "customer_id":
            combo = self._make_voucher_search_combo()
            self.voucher_customer_combo = combo
            self._fill_combo(
                combo,
                self._voucher_customer_options(),
                "customer_id",
                "customer_name",
                skip_blank_labels=True,
            )
            return combo
        if field.name == "company_id":
            combo = self._make_voucher_search_combo()
            self.voucher_company_combo = combo
            self._fill_combo(
                combo,
                self._voucher_company_options(),
                "id",
                "name_ar",
                skip_blank_labels=True,
            )
            return combo
        if field.name == "payment_type":
            combo = self._make_combo(read_only_edit=True)
            combo.addItem("", None)
            for code, label_ar in PAYMENT_CHOICES:
                combo.addItem(label_ar, code)
            self.payment_type_combo = combo
            return combo
        return super()._make_editor(field)

    def _make_voucher_search_combo(self) -> QComboBox:
        """Editable combo with contains-completion (search by any part of the name)."""
        combo = self._make_combo(read_only_edit=False)
        combo.setInsertPolicy(QComboBox.NoInsert)
        if combo.completer() is not None:
            combo.completer().setCompletionMode(QCompleter.PopupCompletion)
            combo.completer().setFilterMode(Qt.MatchContains)
        combo.lineEdit().editingFinished.connect(
            lambda c=combo: self._commit_source_combo_text(c)
        )
        return combo

    def _voucher_customer_options(self) -> list[dict]:
        try:
            return self.service.list_customers_for_selection()
        except Exception:
            return []

    def _voucher_company_options(self) -> list[dict]:
        try:
            return self.service.list_companies_for_selection()
        except Exception:
            return []

    def _reload_voucher_combos(self) -> None:
        """Rebuild the customer/company dropdowns, preserving the current pick."""
        for combo, options, data_key, label_key in (
            (
                getattr(self, "voucher_customer_combo", None),
                self._voucher_customer_options,
                "customer_id",
                "customer_name",
            ),
            (
                getattr(self, "voucher_company_combo", None),
                self._voucher_company_options,
                "id",
                "name_ar",
            ),
        ):
            if combo is None:
                continue
            current = combo.currentData()
            combo.clear()
            self._fill_combo(combo, options(), data_key, label_key, skip_blank_labels=True)
            self._set_editor_value(combo, current)

    def refresh_screen(self) -> None:  # type: ignore[override]
        self._reload_voucher_combos()
        super().refresh_screen()

    # --- new record ----------------------------------------------------------
    def new_record(self) -> None:  # type: ignore[override]
        super().new_record()
        # Default the voucher date to today (matches the DB DEFAULT CURRENT_DATE).
        self._set_editor_value(
            self.inputs.get("voucher_date"), datetime.date.today().strftime("%Y-%m-%d")
        )
        # Show the upcoming automatic number as a hint. The field stays EMPTY so
        # the database sequence assigns the real number atomically on save; an
        # authorised user may type a manual number instead.
        number_editor = self.inputs.get("voucher_number")
        if number_editor is not None:
            try:
                suggested = ReceiptVoucherNumberingService().peek_next_number()
                hint = f"تلقائي: {suggested} — اتركه فارغاً للترقيم التلقائي"
            except Exception:
                hint = "تلقائي (PA-...) — اتركه فارغاً للترقيم التلقائي"
            number_editor.setPlaceholderText(hint)

    # --- validation before save ----------------------------------------------
    def save_record(self) -> None:  # type: ignore[override]
        exclude_id = None if self.mode == "new" else self.current_id

        # Manual voucher number: pre-check uniqueness so the user gets a clear
        # Arabic message instead of a raw UNIQUE-index error (which remains the
        # final guard for the rare concurrent race).
        number_editor = self.inputs.get("voucher_number")
        number = (number_editor.text() if number_editor is not None else "").strip()
        if number:
            try:
                if self.service.value_exists(
                    self.spec, "voucher_number", number, exclude_id=exclude_id
                ):
                    QMessageBox.warning(self, "قيمة مكررة", "رقم السند مستخدم من قبل لسند آخر.")
                    return
            except Exception:
                # If the pre-check itself fails (e.g. transient DB issue), fall
                # through: the UNIQUE index is still the final guard on save.
                pass

        # Amount must be a number greater than zero (the DB CHECK is the final
        # guard; this gives the user a clear Arabic message first).
        amount_editor = self.inputs.get("amount")
        amount_text = str(self._editor_value(amount_editor) or "").strip() if amount_editor else ""
        try:
            amount_ok = float(amount_text) > 0
        except ValueError:
            amount_ok = False
        if not amount_ok:
            QMessageBox.warning(self, "قيمة غير صالحة", "يجب أن يكون المبلغ رقماً أكبر من صفر.")
            return

        # Required fields (date / customer / company / payment type) are enforced
        # by the generic ReviewDataService with Arabic messages; everything else —
        # IDENTITY id, automatic numbering — flows through the base save.
        super().save_record()

        # After a successful save (mode is back to "view"), advance the sequence
        # past a manual PA-<n> so future automatic numbers can never collide.
        # Advisory only — never blocks the already-saved record.
        if number and self.mode == "view":
            try:
                ReceiptVoucherNumberingService().register_manual_number(number)
            except Exception:
                pass
