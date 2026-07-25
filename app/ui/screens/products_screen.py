"""Products / Items module screen (إدارة الأصناف).

UI only: it reuses :class:`BaseCrudScreen` bound to the ``products`` table spec,
so it inherits the exact same layout, colours, borders, toolbar and CRUD logic
as the customers screen (same design family). Business/database logic stays in
``ReviewDataService`` + ``TABLE_SPECS['products']``.

Two small, products-specific touches are layered on top of the generic screen:

* ``الإجمالي`` (total) is shown as a **live** read-only preview that updates as
  the user types quantity/price. The authoritative total is the database
  generated column (quantity × price); this is only a convenience preview.
* Negative quantity/price are rejected *before* the save with a clear Arabic
  message, instead of surfacing the raw database CHECK-constraint error.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QMessageBox, QWidget

from app.services.review_data_service import ReviewDataService, TABLE_SPECS
from app.ui.screens.base_crud_screen import BaseCrudScreen


class ProductsScreen(BaseCrudScreen):
    SPEC_KEY = "products"

    # Products are searched by code or name, not phone/number.
    SEARCH_PLACEHOLDER = "البحث بكود الصنف أو اسم الصنف"

    # Fields that must never be negative, with their Arabic labels for messages.
    _NON_NEGATIVE_FIELDS = (("quantity", "الكمية"), ("price", "سعر الوحدة"))

    def __init__(self, service: ReviewDataService, parent: QWidget | None = None) -> None:
        super().__init__(service, TABLE_SPECS[self.SPEC_KEY], parent)
        # Keep the total preview in sync as quantity/price change.
        for name in ("quantity", "price"):
            editor = self.inputs.get(name)
            if isinstance(editor, QLineEdit):
                editor.textChanged.connect(self._recompute_total)
        self._recompute_total()

    # --- live total preview -------------------------------------------------
    @staticmethod
    def _to_number(text: str) -> float | None:
        text = (text or "").strip()
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _recompute_total(self) -> None:
        total_editor = self.inputs.get("total")
        if not isinstance(total_editor, QLineEdit):
            return
        quantity = self._to_number(self.inputs["quantity"].text())
        price = self._to_number(self.inputs["price"].text())
        if quantity is None or price is None:
            total_editor.setText("")
        else:
            total_editor.setText(f"{quantity * price:.2f}")

    def _fill_form(self, record: dict) -> None:  # type: ignore[override]
        super()._fill_form(record)
        # ``total`` is a virtual field (not returned by get_record), so refresh
        # the preview from the loaded quantity/price.
        self._recompute_total()

    # --- validation before save --------------------------------------------
    def save_record(self) -> None:  # type: ignore[override]
        # Quantity and price are NOT NULL columns (default 0). An empty editor
        # would otherwise be sent to the database as NULL and rejected; default a
        # blank quantity/price to "0" so saving a product with only a name works.
        for name in ("quantity", "price"):
            editor = self.inputs.get(name)
            if isinstance(editor, QLineEdit) and editor.text().strip() == "":
                editor.setText("0")
        for name, label in self._NON_NEGATIVE_FIELDS:
            editor = self.inputs.get(name)
            if not isinstance(editor, QLineEdit):
                continue
            value = self._to_number(editor.text())
            if value is not None and value < 0:
                QMessageBox.warning(
                    self,
                    "قيمة غير صالحة",
                    f"{label} لا يمكن أن تكون بالسالب.",
                )
                return
        # Non-numeric input is caught (with an Arabic message) by the generic
        # coercion in ReviewDataService; required/empty name likewise. Everything
        # else — sequence-assigned code, generated total — flows through the base.
        super().save_record()
