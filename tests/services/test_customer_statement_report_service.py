"""Service-level tests for the Customer Statement report (كشف حساب العميل).

These use a fake repository (no database) to pin the pure logic: the exact Arabic
البيان descriptions, the debit/credit mapping, the fixed-decimal totals and
difference, deterministic ordering (the service preserves the repository order),
inclusion of every invoice status, the empty-state, and filter validation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.customer_statement_report_service import (
    CustomerStatementReportService,
    CustomerStatementRequest,
    CustomerStatementValidationError,
    EMPTY_MESSAGE,
)


class _FakeRepo:
    """Returns canned combined rows; records that no write ever happens."""

    def __init__(self, rows):
        self._rows = rows
        self.fetch_calls: list = []

    def fetch_statement(self, filters):
        self.fetch_calls.append(filters)
        return list(self._rows)

    def fetch_customer_name(self, customer_id):
        return "عميل تجريبي"

    def fetch_customer_options(self):
        return [{"id": 1, "label": "عميل تجريبي"}]

    def fetch_company_options(self):
        return [{"id": 10, "label": "شركتي"}]

    def fetch_company_info(self):
        return {"name": "شركتي", "tax_number": "300000000000003"}


def _invoice_row(source_id, number, payment_type, total, *, status="draft", d=date(2026, 1, 10),
                 company_name="شركتي"):
    return {
        "source_type": "sales_invoice",
        "source_id": source_id,
        "source_sequence": 0,
        "transaction_date": d,
        "customer_id": 1,
        "customer_name": "عميل تجريبي",
        "company_id": 10,
        "company_name": company_name,
        "sales_invoice_number": number,
        "debit": Decimal(str(total)),
        "credit": Decimal("0"),
        "payment_type": payment_type,
        "invoice_status": status,
        "voucher_number": None,
    }


def _cash_payment_row(source_id, number, total, *, d=date(2026, 1, 10)):
    """A derived cash-invoice settlement row (credit side), as the repository
    UNION produces for a cash invoice."""
    return {
        "source_type": "cash_invoice_payment",
        "source_id": source_id,
        "source_sequence": 1,
        "transaction_date": d,
        "customer_id": 1,
        "customer_name": "عميل تجريبي",
        "company_id": 10,
        "company_name": "شركتي",
        "sales_invoice_number": number,
        "debit": Decimal("0"),
        "credit": Decimal(str(total)),
        "payment_type": "cash",
        "invoice_status": "draft",
        "voucher_number": None,
    }


def _voucher_row(source_id, number, amount, *, linked_invoice=None, d=date(2026, 1, 10)):
    return {
        "source_type": "receipt_voucher",
        "source_id": source_id,
        "source_sequence": 2,
        "transaction_date": d,
        "customer_id": 1,
        "customer_name": "عميل تجريبي",
        "company_id": 10,
        "company_name": "شركتي",
        "sales_invoice_number": linked_invoice,
        "debit": Decimal("0"),
        "credit": Decimal(str(amount)),
        "payment_type": "cash",
        "invoice_status": None,
        "voucher_number": number,
    }


def _service(rows):
    return CustomerStatementReportService(_FakeRepo(rows))


def _run(rows, customer_id=1, date_from=None, date_to=None):
    svc = _service(rows)
    return svc.fetch_report(
        CustomerStatementRequest(customer_id=customer_id, date_from=date_from, date_to=date_to)
    )


# --- 1: credit invoice -> one debit row -------------------------------------
def test_credit_invoice_is_single_debit_row():
    result = _run([_invoice_row(1, "INV-1", "credit", "100.00")])
    assert len(result.export_rows) == 1
    row = result.export_rows[0]
    assert row["debit"] == "100.00"
    assert row["credit"] == ""                       # zero side blank
    assert row["sales_invoice_number"] == "INV-1"
    assert row["description"] == "فاتورة مبيعات آجلة رقم INV-1"


# --- 2: cash invoice -> debit invoice row -----------------------------------
def test_cash_invoice_debit_row_description():
    result = _run([_invoice_row(2, "INV-2", "cash", "230.00")])
    row = result.export_rows[0]
    assert row["debit"] == "230.00"
    assert row["description"] == "فاتورة مبيعات نقدية رقم INV-2"


# --- 3: cash invoice -> paired debit + credit settlement (two movements) -----
def test_cash_invoice_pairs_debit_and_settlement_credit():
    # The repository emits an invoice debit row AND a derived cash-settlement
    # credit row for a cash invoice. The debit keeps the invoice's own number.
    # For the settlement (the سند): the فاتورة المبيعات column shows the offset
    # serial — +123 on the trailing digits, "INV-2" -> "INV-125" — so the movement
    # number matches the printed سند, while the البيان names the real invoice.
    result = _run([
        _invoice_row(2, "INV-2", "cash", "230.00"),
        _cash_payment_row(2, "INV-2", "230.00"),
    ])
    assert len(result.export_rows) == 2            # never merged into one row
    invoice, settlement = result.export_rows
    assert invoice["debit"] == "230.00" and invoice["credit"] == ""
    assert invoice["description"] == "فاتورة مبيعات نقدية رقم INV-2"
    assert invoice["sales_invoice_number"] == "INV-2"       # invoice keeps its own number
    assert settlement["credit"] == "230.00" and settlement["debit"] == ""
    assert settlement["description"] == "سند قبض رقم INV-125"   # البيان: «سند قبض رقم» + offset serial
    assert settlement["sales_invoice_number"] == "INV-125"  # +123 offset in the فاتورة المبيعات column too
    # A fully-paid cash invoice nets to zero on the statement.
    assert result.summary["difference"] == Decimal("0.00")


def test_cash_settlement_shows_offset_serial_numeric_example():
    # The user's own example: a cash invoice numbered "100" settles as the سند
    # with the offset serial "223" (100 + 123) — both the فاتورة المبيعات column
    # and the البيان «سند قبض رقم 223» show it, matching the printed سند. The
    # invoice's own debit row stays "100".
    result = _run([
        _invoice_row(3, "100", "cash", "50.00"),
        _cash_payment_row(3, "100", "50.00"),
    ])
    invoice, settlement = result.export_rows
    assert invoice["sales_invoice_number"] == "100"
    assert settlement["sales_invoice_number"] == "223"              # offset in the number column
    assert settlement["description"] == "سند قبض رقم 223"           # البيان: «سند قبض رقم» + offset serial


def test_cash_invoice_and_actual_receipt_are_separate_rows():
    # A cash invoice's settlement and a real receipt voucher are distinct rows.
    result = _run([
        _invoice_row(2, "INV-2", "cash", "230.00"),
        _cash_payment_row(2, "INV-2", "230.00"),
        _voucher_row(5, "V-9", "100.00"),
    ])
    assert len(result.export_rows) == 3
    assert result.export_rows[2]["description"] == "سند قبض رقم V-9"


# --- 4: receipt linked to an invoice ----------------------------------------
def test_linked_receipt_description():
    result = _run([_voucher_row(6, "V-1", "50.00", linked_invoice="INV-7")])
    row = result.export_rows[0]
    assert row["description"] == "سداد فاتورة مبيعات رقم INV-7"
    assert row["sales_invoice_number"] == "INV-7"
    assert row["credit"] == "50.00"


# --- 5: general receipt voucher ---------------------------------------------
def test_general_receipt_description_and_dash():
    result = _run([_voucher_row(7, "V-2", "75.00")])
    row = result.export_rows[0]
    assert row["description"] == "سند قبض رقم V-2"
    assert row["sales_invoice_number"] == "-"      # no linked invoice
    assert row["credit"] == "75.00"


# --- 6/7/8: all invoice statuses are included (service filters none) ---------
def test_all_invoice_statuses_are_included():
    rows = [
        _invoice_row(1, "D-1", "cash", "10", status="draft"),
        _invoice_row(2, "A-1", "credit", "20", status="approved"),
        _invoice_row(3, "C-1", "cash", "30", status="cancelled"),
        _invoice_row(4, "X-1", "credit", "40", status="anything_else"),
    ]
    result = _run(rows)
    statuses = {r["invoice_status"] for r in result.rows}
    assert statuses == {"draft", "approved", "cancelled", "anything_else"}
    assert len(result.export_rows) == 4


# --- 14/15/16: totals + difference (fixed decimal) --------------------------
def test_totals_and_difference_are_decimal_and_correct():
    result = _run([
        _invoice_row(1, "INV-1", "credit", "1000.50"),
        _invoice_row(2, "INV-2", "cash", "499.50"),
        _voucher_row(3, "V-1", "600.25"),
    ])
    summary = result.summary
    assert summary["total_debit"] == Decimal("1500.00")
    assert summary["total_credit"] == Decimal("600.25")
    assert summary["difference"] == Decimal("899.75")
    # Decimal, never float.
    assert isinstance(summary["total_debit"], Decimal)
    assert isinstance(summary["difference"], Decimal)
    assert summary["total_debit_label"] == "1,500.00"
    assert summary["difference_label"] == "899.75"


# --- 17: no transaction is duplicated ---------------------------------------
def test_no_row_is_duplicated():
    rows = [_invoice_row(i, f"INV-{i}", "cash", "10") for i in range(1, 6)]
    result = _run(rows)
    ids = [(r["source_type"], r["source_id"]) for r in result.rows]
    assert len(ids) == len(set(ids)) == 5


# --- 18: same-date ordering is preserved from the repository ----------------
def test_service_preserves_repository_order():
    # Repository returns invoice (seq 0) before same-date voucher (seq 1).
    rows = [
        _invoice_row(1, "INV-1", "cash", "100", d=date(2026, 1, 10)),
        _voucher_row(2, "V-1", "40", d=date(2026, 1, 10)),
        _invoice_row(3, "INV-2", "credit", "200", d=date(2026, 1, 11)),
    ]
    result = _run(rows)
    order = [(r["source_type"], r["source_id"]) for r in result.rows]
    assert order == [
        ("sales_invoice", 1),
        ("receipt_voucher", 2),
        ("sales_invoice", 3),
    ]


# --- 19: empty state ---------------------------------------------------------
def test_empty_state():
    result = _run([])
    assert result.is_empty is True
    assert result.export_rows == []
    assert result.summary["total_debit"] == Decimal("0.00")
    assert result.summary["difference"] == Decimal("0.00")
    assert EMPTY_MESSAGE == "لا توجد حركات للعميل خلال الفترة المحددة"


# --- company column + optional filters --------------------------------------
def test_company_name_column_is_present():
    result = _run([_invoice_row(1, "INV-1", "cash", "100.00", company_name="شركة النور")])
    assert any(c.key == "company_name" and c.label == "اسم الشركة" for c in result.columns)
    assert result.export_rows[0]["company_name"] == "شركة النور"


def test_no_customer_or_company_returns_all_rows():
    # With neither filter, the service passes None/None and returns every row.
    svc = _service([_invoice_row(1, "INV-1", "cash", "10"), _voucher_row(2, "V-1", "5")])
    result = svc.fetch_report(CustomerStatementRequest())  # no customer, no company
    assert len(result.export_rows) == 2
    assert svc.repository.fetch_calls[0].customer_id is None
    assert svc.repository.fetch_calls[0].company_id is None


def test_company_filter_is_passed_to_repository_as_int():
    svc = _service([])
    svc.fetch_report(CustomerStatementRequest(company_id="10"))
    assert svc.repository.fetch_calls[0].company_id == 10


# --- validation --------------------------------------------------------------
def test_invalid_date_range_raises_arabic_message():
    with pytest.raises(CustomerStatementValidationError) as excinfo:
        _run([], date_from="2026-02-01", date_to="2026-01-01")
    assert "التاريخ" in excinfo.value.message


def test_equal_dates_are_valid():
    # date_from == date_to is a valid single-day range.
    result = _run([_invoice_row(1, "INV-1", "cash", "10")],
                  date_from="2026-01-10", date_to="2026-01-10")
    assert len(result.export_rows) == 1


def test_customer_id_is_passed_to_repository_as_int():
    svc = _service([])
    svc.fetch_report(CustomerStatementRequest(customer_id="29582"))
    assert svc.repository.fetch_calls[0].customer_id == 29582
