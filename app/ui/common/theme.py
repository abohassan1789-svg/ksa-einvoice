"""Visual theme constants and small style helpers for the review UI.

These are layout/presentation concerns only (colors, button styling, the
combo dropdown alignment delegate, and per-screen layout maps). No business
or database logic lives here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStyledItemDelegate


GREEN = "#137A38"
GREEN_DARK = "#0F6B30"
BORDER = "#D9E2EC"
TEXT = "#111827"

# Screens that use the side-by-side "form + search list" layout with an
# F1 lookup dialog (same design/idea as the reference customer screen).
LOOKUP_LAYOUT_KEYS = {"customers", "employees", "daily_followups", "products", "companies", "receipt_vouchers"}

# Labels for list columns that come from a JOIN (no matching FieldSpec).
EXTRA_COLUMN_LABELS = {
    "case_status_name": "الحالة",
    "employee_name": "اسم الموظف",
}

# Fields whose text should be right-aligned (numeric values), per screen.
# Everything else on the form stays left-aligned.
RIGHT_ALIGNED_FIELDS = {
    "customers": {
        "customer_id",
        "phone_number",
        "area_number",
        "unit_number",
        "building",
        "vat_number",
        "cr",
    },
    "employees": {
        "employee_id",
        "phone_number",
    },
    "products": {
        "item_code",
        "quantity",
        "price",
        "total",
    },
    "companies": {
        "id",
        "commercial_registration",
        "vat_number",
    },
    "daily_followups": {
        "daily_followup_id",
        "customer_id",
        "customer_phone",
        "case_status_id",
        "employee_id",
        "contact_count",
    },
    "receipt_vouchers": {
        "id",
        "voucher_number",
        "voucher_date",
        "amount",
    },
}


class _RightAlignDelegate(QStyledItemDelegate):
    """Render combo-box dropdown items left-aligned."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.displayAlignment = Qt.AlignLeft | Qt.AlignVCenter


def _button_style(bg: str, hover: str, fg: str = "#FFFFFF") -> str:
    return (
        f"QPushButton {{ background:{bg}; color:{fg}; border:none; border-radius:6px; "
        "font-weight:800; padding:7px 12px; }}"
        f"QPushButton:hover {{ background:{hover}; }}"
        "QPushButton:disabled { background:#CBD5E1; color:#FFFFFF; }"
    )
