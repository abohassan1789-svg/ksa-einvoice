"""Business logic for the Customer Statement report (كشف حساب العميل).

The UI calls only this service; this service calls only the repository. No SQL
and no Qt widgets live here — just the row mapping, the exact Arabic
descriptions, the fixed-decimal totals and the input validation.

The report is strictly **read-only and operational**: it combines existing sales
invoices (as debit movements) and existing receipt vouchers (as credit
movements) into one customer statement. It never posts a journal entry, never
updates a customer balance, never marks an invoice paid, never changes any
invoice/voucher status, and never creates a receipt voucher. Opening or
generating the report performs SELECTs only.

Money is handled with :class:`decimal.Decimal` throughout (never float), matching
the invoice/voucher money columns (``numeric``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.sales_invoice import voucher_serial_from_invoice_number
from app.repositories.customer_statement_report_repository import (
    CustomerStatementFilters,
    CustomerStatementReportRepository,
)

# Sales-invoice payment type that means an immediate (cash) sale. Anything else
# (e.g. "credit") is treated as a credit / outstanding sale.
PAYMENT_CASH = "cash"

# Two decimal places for all money values (matches numeric(18,2)/numeric columns).
_MONEY_QUANT = Decimal("0.01")
_ZERO = Decimal("0.00")


class CustomerStatementValidationError(Exception):
    """Raised for invalid filters. ``message`` is a ready-to-show Arabic string."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ReportColumn:
    """Lightweight column descriptor reused by the Excel/PDF/print exporters."""

    key: str
    label: str


@dataclass(frozen=True)
class CustomerStatementRequest:
    customer_id: int | None = None
    company_id: int | None = None
    date_from: str | None = None
    date_to: str | None = None


@dataclass(frozen=True)
class CustomerStatementResult:
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]          # numeric rows (Decimal debit/credit) for totals/tests
    export_rows: list[dict[str, Any]]   # formatted strings keyed by column.key (UI + exports)
    summary: dict[str, Any]             # Decimal totals + formatted labels
    customer_name: str | None
    is_empty: bool


# Visible columns, in the exact required order. The UI renders RTL, so the first
# column (التاريخ) shows on the right — matching the reference reports.
REPORT_COLUMNS: tuple[ReportColumn, ...] = (
    ReportColumn("transaction_date", "التاريخ"),
    ReportColumn("customer_name", "اسم العميل"),
    ReportColumn("company_name", "اسم الشركة"),
    ReportColumn("sales_invoice_number", "فاتورة المبيعات"),
    ReportColumn("debit", "المدين"),
    ReportColumn("credit", "الدائن"),
    ReportColumn("description", "البيان"),
)

EMPTY_MESSAGE = "لا توجد حركات للعميل خلال الفترة المحددة"


class CustomerStatementReportService:
    """Build the read-only Customer Statement from invoices + receipt vouchers."""

    def __init__(self, repository: CustomerStatementReportRepository | None = None) -> None:
        self.repository = repository or CustomerStatementReportRepository()
        self.columns = list(REPORT_COLUMNS)

    # --- public API ---------------------------------------------------------
    def fetch_report(self, request: CustomerStatementRequest) -> CustomerStatementResult:
        # Customer and company are BOTH optional: with neither set the statement
        # returns every movement (all customers / all companies) in the range.
        customer_id = self._optional_id(request.customer_id, "العميل المحدد غير صالح.")
        company_id = self._optional_id(request.company_id, "الشركة المحددة غير صالحة.")
        date_from, date_to = self._validate_dates(request.date_from, request.date_to)

        filters = CustomerStatementFilters(
            customer_id=customer_id,
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
        )
        raw_rows = self.repository.fetch_statement(filters)

        rows: list[dict[str, Any]] = []
        total_debit = _ZERO
        total_credit = _ZERO
        customer_name: str | None = None
        for raw in raw_rows:
            debit = self._money(raw.get("debit"))
            credit = self._money(raw.get("credit"))
            total_debit += debit
            total_credit += credit
            if customer_name is None:
                customer_name = (raw.get("customer_name") or None)
            rows.append(
                {
                    # visible/logical fields
                    "transaction_date": self._format_date(raw.get("transaction_date")),
                    "customer_name": raw.get("customer_name") or "",
                    "company_name": raw.get("company_name") or "",
                    "sales_invoice_number": self._invoice_cell(raw),
                    "debit": debit,
                    "credit": credit,
                    "description": self._description(raw),
                    # hidden metadata (returned by the service, not shown)
                    "source_type": raw.get("source_type"),
                    "source_id": raw.get("source_id"),
                    "source_sequence": raw.get("source_sequence"),
                    "invoice_status": raw.get("invoice_status"),
                }
            )

        # A single-customer statement keeps the selected customer's name for the
        # header; the all-customers view leaves it None (header shows "الكل").
        if customer_name is None and customer_id is not None:
            customer_name = self.repository.fetch_customer_name(customer_id)

        export_rows = [self._export_row(row) for row in rows]
        difference = total_debit - total_credit
        summary = {
            "total_debit": total_debit,
            "total_credit": total_credit,
            "difference": difference,
            "total_debit_label": self._money_text(total_debit),
            "total_credit_label": self._money_text(total_credit),
            "difference_label": self._money_text(difference),
        }
        return CustomerStatementResult(
            columns=self.columns,
            rows=rows,
            export_rows=export_rows,
            summary=summary,
            customer_name=customer_name,
            is_empty=not rows,
        )

    def load_customer_options(self) -> list[dict[str, Any]]:
        try:
            return list(self.repository.fetch_customer_options())
        except Exception:  # noqa: BLE001 - selector options must never break the screen
            return []

    def load_company_options(self) -> list[dict[str, Any]]:
        try:
            return list(self.repository.fetch_company_options())
        except Exception:  # noqa: BLE001
            return []

    def company_info(self) -> dict[str, str]:
        try:
            return self.repository.fetch_company_info()
        except Exception:  # noqa: BLE001
            return {}

    def company_letterhead(self, company_id: Any) -> dict[str, Any] | None:
        """The selected company's identity for the print letterhead, or ``None``.

        ``None`` for «الكل» — a statement spanning several companies has no single
        issuer, so the templates print their title without a letterhead rather than
        head the sheet with a company that did not issue all of it.
        """
        if company_id in (None, ""):
            return None
        try:
            return self.repository.fetch_company_letterhead(int(company_id))
        except (TypeError, ValueError):
            return None
        except Exception:  # noqa: BLE001 - never break a print over the letterhead
            return None

    # --- row logic ----------------------------------------------------------
    def _description(self, raw: dict[str, Any]) -> str:
        """The exact Arabic البيان text for each movement type."""
        source_type = raw.get("source_type")
        invoice_number = raw.get("sales_invoice_number") or ""
        if source_type == "sales_invoice":
            if self._is_cash(raw.get("payment_type")):
                return f"فاتورة مبيعات نقدية رقم {invoice_number}"
            return f"فاتورة مبيعات آجلة رقم {invoice_number}"
        if source_type == "cash_invoice_payment":
            # Derived settlement of a cash invoice (credit side). This IS the cash
            # invoice's سند, so the البيان reads «سند قبض رقم <serial>» carrying the
            # سند's own offset serial (invoice number + 123), identical to the سند
            # printed from the sales-invoice screen. (User change 2026-07-23: the
            # wording moved from «سداد فاتورة مبيعات رقم» to «سند قبض رقم», and the
            # number from the invoice's own back to the offset serial.)
            return f"سند قبض رقم {voucher_serial_from_invoice_number(invoice_number)}"
        # receipt voucher
        if invoice_number:  # only when an explicit DB relationship provided one
            return f"سداد فاتورة مبيعات رقم {invoice_number}"
        return f"سند قبض رقم {raw.get('voucher_number') or ''}"

    @staticmethod
    def _invoice_cell(raw: dict[str, Any]) -> str:
        """فاتورة المبيعات column: invoice number for invoices / linked vouchers,
        otherwise a dash for a general receipt voucher.

        The derived cash-settlement (سند) row shows the voucher serial (invoice
        number + 123) so the statement matches the printed سند; the invoice's own
        debit row keeps the real invoice number."""
        number = raw.get("sales_invoice_number")
        if not number:
            return "-"
        if raw.get("source_type") == "cash_invoice_payment":
            return voucher_serial_from_invoice_number(number)
        return str(number)

    @staticmethod
    def _is_cash(payment_type: Any) -> bool:
        return str(payment_type or "").strip().lower() == PAYMENT_CASH

    # --- money / formatting -------------------------------------------------
    @staticmethod
    def _money(value: Any) -> Decimal:
        if value is None:
            return _ZERO
        if isinstance(value, Decimal):
            return value.quantize(_MONEY_QUANT)
        return Decimal(str(value)).quantize(_MONEY_QUANT)

    @staticmethod
    def _money_text(value: Decimal) -> str:
        return f"{value:,.2f}"

    def _money_cell(self, value: Decimal) -> str:
        """A movement shows its amount only in its own column; the opposite
        column is left blank (standard statement presentation)."""
        return self._money_text(value) if value != _ZERO else ""

    @staticmethod
    def _format_date(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        return str(value)[:10]

    def _export_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "transaction_date": row["transaction_date"],
            "customer_name": row["customer_name"],
            "company_name": row["company_name"],
            "sales_invoice_number": row["sales_invoice_number"],
            "debit": self._money_cell(row["debit"]),
            "credit": self._money_cell(row["credit"]),
            "description": row["description"],
        }

    # --- validation ---------------------------------------------------------
    @staticmethod
    def _optional_id(value: Any, invalid_message: str) -> int | None:
        """Coerce an optional selector id to int; None/blank means 'no filter'."""
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise CustomerStatementValidationError(invalid_message) from exc

    @staticmethod
    def _validate_dates(
        date_from: str | None, date_to: str | None
    ) -> tuple[str | None, str | None]:
        start = (date_from or "").strip() or None
        end = (date_to or "").strip() or None
        if start and end and start > end:
            raise CustomerStatementValidationError(
                "نطاق التاريخ غير صحيح: يجب أن يكون تاريخ (من) أقل من أو يساوي تاريخ (إلى)."
            )
        return start, end
