"""Daily follow-ups module screen (إدارة المتابعة اليومية).

UI only: reuses :class:`BaseCrudScreen` bound to the ``daily_followups`` table
spec, which drives the customer-linked form blocks and the F1 record lookup.

On top of the base behavior, this screen binds ``F5`` to open the reusable
``CustomerLookupDialog`` for quick customer selection. Selecting a customer
auto-fills only the customer-related form fields (code, name, phone, place,
unit, ...); all manual follow-up fields stay empty for the user to enter.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QWidget

from app.services.review_data_service import ReviewDataService, TABLE_SPECS
from app.ui.dialogs.customer_lookup_dialog import CustomerLookupDialog
from app.ui.screens.base_crud_screen import BaseCrudScreen


class DailyFollowupsScreen(BaseCrudScreen):
    SPEC_KEY = "daily_followups"

    def __init__(self, service: ReviewDataService, parent: QWidget | None = None) -> None:
        super().__init__(service, TABLE_SPECS[self.SPEC_KEY], parent)
        # F5 opens the customer selection popup. F1 (record lookup) is already
        # registered by BaseCrudScreen; F5 does not conflict with it.
        self.customer_pick_shortcut = QShortcut(QKeySequence("F5"), self)
        self.customer_pick_shortcut.setContext(Qt.ApplicationShortcut)
        self.customer_pick_shortcut.activated.connect(self.open_customer_lookup)
        try:
            self.new_button.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.new_button.clicked.connect(self.start_new_followup)

    def start_new_followup(self) -> None:
        self.new_record()
        self.open_customer_lookup()

    def open_customer_lookup(self) -> None:
        """Open the reusable customer picker and fill customer fields on select."""
        dialog = CustomerLookupDialog(self.service, self)
        if dialog.exec() != QDialog.Accepted:
            return
        customer_id: Any | None = dialog.selected_customer_id
        if customer_id is None:
            return
        # Enter a fresh follow-up record (clears manual fields, defaults
        # follow-up date today) before applying customer data so any previously
        # viewed/edited follow-up snapshot is replaced.
        if self.mode not in {"new", "edit"}:
            self.new_record()
        # The popup already returns the selected customer id. Load by that id
        # directly instead of round-tripping through the phone/name ComboBoxes;
        # _apply_customer still updates those visible fields after the record is
        # fetched, and this avoids a fragile currentData() dependency.
        self._load_customer(customer_id, prefill_installments=True)
        # Move focus to the first manual follow-up field (follow_up_date).
        first_manual = self.inputs.get("follow_up_date")
        if first_manual is not None:
            first_manual.setFocus()
            if hasattr(first_manual, "selectAll"):
                first_manual.selectAll()
