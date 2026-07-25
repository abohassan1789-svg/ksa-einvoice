"""Integration tests for the Customer Statement repository (كشف حساب العميل).

These verify the guarantees only a real database (the UNION ALL + JOIN + ORDER BY)
can give: customer filtering, inclusive date-range filtering across BOTH sources,
inclusion of every invoice status, no duplicate rows, deterministic same-date
ordering (invoice before its payment), and that reading the statement writes
nothing.

Isolation: every test runs inside a throwaway schema (``cs_test_<n>``) created in
setup and ``DROP SCHEMA ... CASCADE``-d in teardown. Stub ``customers`` /
``sales_invoices`` / ``receipt_vouchers`` tables are created *inside* that schema
via ``search_path``, so nothing in ``public`` (real app data) is touched. If no
database is reachable the module is skipped.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.database.db import Database
from app.repositories.customer_statement_report_repository import (
    CustomerStatementFilters,
    CustomerStatementReportRepository,
)

_SCHEMA_SEQ = iter(range(1, 1_000_000))


def _try_connect() -> Database | None:
    try:
        db = Database()
        db.execute("SELECT 1")
        return db
    except Exception:
        return None


_probe = _try_connect()
pytestmark = pytest.mark.skipif(
    _probe is None,
    reason="No PostgreSQL reachable (set DB_* env / .env to run statement DB tests).",
)


def _bound_db(schema: str) -> Database:
    db = Database()
    db.execute(f'SET search_path TO "{schema}", public')
    return db


# Minimal stand-ins for the real tables so the query resolves inside the schema.
_STUB_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id integer PRIMARY KEY,
    customer_name varchar(200),
    phone_number varchar(50)
);
CREATE TABLE IF NOT EXISTS companies (
    id integer PRIMARY KEY,
    name_ar varchar(200)
);
CREATE TABLE IF NOT EXISTS sales_invoices (
    id bigint PRIMARY KEY,
    invoice_number varchar(50),
    issue_datetime timestamptz,
    customer_id integer,
    seller_company_id integer,
    seller_name_ar_snapshot varchar(200),
    payment_type varchar(20),
    total_including_vat numeric(18,2),
    document_status varchar(20)
);
CREATE TABLE IF NOT EXISTS receipt_vouchers (
    id integer PRIMARY KEY,
    voucher_number varchar(30),
    voucher_date date,
    customer_id integer,
    company_id integer,
    payment_type varchar(20),
    amount numeric(18,2)
);
INSERT INTO customers (customer_id, customer_name) VALUES
    (1, 'العميل الأول'), (2, 'عميل آخر');
INSERT INTO companies (id, name_ar) VALUES
    (10, 'شركة النور'), (20, 'شركة الأفق');
"""

TARGET = 1
OTHER = 2
COMPANY_A = 10
COMPANY_B = 20


@pytest.fixture()
def schema():
    name = f"cs_test_{next(_SCHEMA_SEQ)}_{os.getpid()}"
    admin = Database()
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def _seed(db: Database) -> None:
    db.execute_script(_STUB_SQL)

    def inv(i, num, dt, cust, pt, total, status, company=COMPANY_A):
        db.execute(
            "INSERT INTO sales_invoices "
            "(id, invoice_number, issue_datetime, customer_id, seller_company_id, "
            " seller_name_ar_snapshot, payment_type, total_including_vat, document_status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [i, num, dt, cust, company, "لقطة الاسم", pt, total, status],
        )

    def vch(i, num, d, cust, amount, company=COMPANY_A):
        db.execute(
            "INSERT INTO receipt_vouchers "
            "(id, voucher_number, voucher_date, customer_id, company_id, payment_type, amount) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [i, num, d, cust, company, "cash", amount],
        )

    # Target customer — three different statuses, spread across dates.
    inv(1, "INV-D", "2026-01-10 09:00+03", TARGET, "cash", "100.00", "draft")
    inv(2, "INV-A", "2026-01-15 23:30+03", TARGET, "credit", "200.00", "approved")
    inv(3, "INV-C", "2026-01-20 10:00+03", TARGET, "cash", "50.00", "cancelled")
    # Out-of-range invoices for the date tests.
    inv(4, "INV-OLD", "2025-12-01 10:00+03", TARGET, "cash", "999.00", "approved")
    inv(5, "INV-NEW", "2026-02-05 10:00+03", TARGET, "cash", "888.00", "approved")
    # Another customer's invoice (must never appear when filtering to TARGET).
    inv(6, "INV-OTHER", "2026-01-12 10:00+03", OTHER, "cash", "777.00", "approved")
    # A TARGET invoice belonging to a DIFFERENT company (for the company filter).
    inv(7, "INV-B", "2026-01-14 10:00+03", TARGET, "credit", "310.00", "approved", company=COMPANY_B)

    # Vouchers — one shares 2026-01-10 with invoice #1 (ordering test).
    vch(1, "V-1", "2026-01-10", TARGET, "30.00")
    vch(2, "V-2", "2026-01-16", TARGET, "40.00")
    vch(3, "V-OTHER", "2026-01-10", OTHER, "500.00")


@pytest.fixture()
def repo(schema):
    db = _bound_db(schema)
    _seed(db)
    return CustomerStatementReportRepository(db)


def _numbers(rows):
    out = []
    for r in rows:
        if r["source_type"] == "sales_invoice":
            out.append(("inv", r["sales_invoice_number"]))
        elif r["source_type"] == "cash_invoice_payment":
            out.append(("pay", r["sales_invoice_number"]))
        else:
            out.append(("vch", r["voucher_number"]))
    return out


# --- 9 & 13: customer filtering ---------------------------------------------
def test_only_target_customer_rows(repo):
    rows = repo.fetch_statement(CustomerStatementFilters(customer_id=TARGET))
    assert all(r["customer_id"] == TARGET for r in rows)
    assert ("inv", "INV-OTHER") not in _numbers(rows)
    assert ("vch", "V-OTHER") not in _numbers(rows)


def test_customer_name_joined_once(repo):
    rows = repo.fetch_statement(CustomerStatementFilters(customer_id=TARGET))
    assert rows and all(r["customer_name"] == "العميل الأول" for r in rows)


# --- company name column + company filter -----------------------------------
def test_company_name_is_joined(repo):
    rows = repo.fetch_statement(CustomerStatementFilters(customer_id=TARGET))
    by_num = {r["sales_invoice_number"]: r for r in rows if r["source_type"] == "sales_invoice"}
    assert by_num["INV-D"]["company_name"] == "شركة النور"     # company A
    assert by_num["INV-B"]["company_name"] == "شركة الأفق"     # company B
    # Vouchers also carry their company name (company A here).
    voucher = next(r for r in rows if r["source_type"] == "receipt_voucher")
    assert voucher["company_name"] == "شركة النور"


def test_company_filter_restricts_rows(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, company_id=COMPANY_B)
    )
    nums = _numbers(rows)
    assert ("inv", "INV-B") in nums          # company B kept
    assert ("inv", "INV-D") not in nums      # company A excluded
    assert all(r["company_id"] == COMPANY_B for r in rows)


def test_no_customer_no_company_returns_all_customers(repo):
    # Neither filter set -> both customers' same-day movements appear.
    rows = repo.fetch_statement(
        CustomerStatementFilters(date_from="2026-01-10", date_to="2026-01-10")
    )
    nums = _numbers(rows)
    assert ("inv", "INV-D") in nums          # TARGET
    assert ("vch", "V-OTHER") in nums        # OTHER customer
    customer_ids = {r["customer_id"] for r in rows}
    assert customer_ids == {TARGET, OTHER}


# --- 6/7/8: all invoice statuses included -----------------------------------
def test_all_statuses_included(repo):
    rows = repo.fetch_statement(CustomerStatementFilters(customer_id=TARGET))
    inv_statuses = {r["invoice_status"] for r in rows if r["source_type"] == "sales_invoice"}
    assert {"draft", "approved", "cancelled"} <= inv_statuses


# --- 10/11/12: inclusive date range across both sources ---------------------
def test_date_from_is_inclusive(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-15")
    )
    nums = _numbers(rows)
    assert ("inv", "INV-A") in nums          # 2026-01-15 kept (>= from)
    assert ("inv", "INV-D") not in nums      # 2026-01-10 excluded
    assert ("inv", "INV-OLD") not in nums    # 2025-12-01 excluded


def test_date_to_is_inclusive_including_late_timestamp(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_to="2026-01-15")
    )
    nums = _numbers(rows)
    # INV-A is at 2026-01-15 23:30 — must still be included by the <date_to+1day rule.
    assert ("inv", "INV-A") in nums
    assert ("inv", "INV-C") not in nums      # 2026-01-20 excluded
    assert ("inv", "INV-NEW") not in nums    # 2026-02-05 excluded


def test_range_excludes_outside_and_keeps_inside(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-10", date_to="2026-01-16")
    )
    nums = _numbers(rows)
    assert ("inv", "INV-OLD") not in nums and ("inv", "INV-NEW") not in nums
    assert ("inv", "INV-D") in nums and ("inv", "INV-A") in nums
    assert ("vch", "V-1") in nums and ("vch", "V-2") in nums


# --- cash invoice -> paired debit + settlement credit -----------------------
def test_cash_invoice_emits_debit_and_settlement(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-10", date_to="2026-01-10")
    )
    # 2026-01-10: cash invoice INV-D -> debit (seq 0) + settlement (seq 1),
    # then voucher V-1 (seq 2).
    assert _numbers(rows) == [("inv", "INV-D"), ("pay", "INV-D"), ("vch", "V-1")]
    debit = next(r for r in rows if r["source_type"] == "sales_invoice")
    settlement = next(r for r in rows if r["source_type"] == "cash_invoice_payment")
    assert debit["debit"] == Decimal("100.00") and debit["credit"] == Decimal("0")
    assert settlement["credit"] == Decimal("100.00") and settlement["debit"] == Decimal("0")
    assert settlement["sales_invoice_number"] == "INV-D"


def test_credit_invoice_has_no_settlement_row(repo):
    # INV-A is a CREDIT (آجل) invoice on 2026-01-15 -> debit only, no settlement.
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-15", date_to="2026-01-15")
    )
    assert ("inv", "INV-A") in _numbers(rows)
    assert ("pay", "INV-A") not in _numbers(rows)


# --- 17: no duplicated rows --------------------------------------------------
def test_no_duplicate_rows(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-01", date_to="2026-01-31")
    )
    keys = [(r["source_type"], r["source_id"]) for r in rows]
    assert len(keys) == len(set(keys))
    # invoices D/A/C/B (4) + cash settlements for D & C (2) + vouchers V-1,V-2 (2) = 8.
    assert len(rows) == 8


# --- 18: deterministic same-date ordering (invoice before payment) ----------
def test_same_date_invoice_before_payment(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-10", date_to="2026-01-10")
    )
    # 2026-01-10: invoice INV-D (seq 0), its cash settlement (seq 1), voucher V-1 (seq 2).
    assert _numbers(rows) == [("inv", "INV-D"), ("pay", "INV-D"), ("vch", "V-1")]


def test_each_cash_invoice_is_followed_by_its_own_settlement(schema):
    """Two cash invoices on the SAME date: each must be followed immediately by
    its OWN settlement (inv1, pay1, inv2, pay2) — not every invoice first and
    every settlement afterwards (inv1, inv2, pay1, pay2). A same-date receipt
    voucher still lands after the invoice groups."""
    db = _bound_db(schema)
    db.execute_script(_STUB_SQL)

    def inv(i, num):
        db.execute(
            "INSERT INTO sales_invoices "
            "(id, invoice_number, issue_datetime, customer_id, seller_company_id, "
            " seller_name_ar_snapshot, payment_type, total_including_vat, document_status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [i, num, "2026-03-01 10:00+03", TARGET, COMPANY_A, "لقطة", "cash", "100.00", "approved"],
        )

    inv(100, "INV-P1")
    inv(101, "INV-P2")
    db.execute(
        "INSERT INTO receipt_vouchers "
        "(id, voucher_number, voucher_date, customer_id, company_id, payment_type, amount) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        [50, "V-P", "2026-03-01", TARGET, COMPANY_A, "cash", "30.00"],
    )
    repo = CustomerStatementReportRepository(db)
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-03-01", date_to="2026-03-01")
    )
    assert _numbers(rows) == [
        ("inv", "INV-P1"), ("pay", "INV-P1"),
        ("inv", "INV-P2"), ("pay", "INV-P2"),
        ("vch", "V-P"),
    ]


def test_debit_credit_values(repo):
    rows = repo.fetch_statement(
        CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-10", date_to="2026-01-10")
    )
    invoice = next(r for r in rows if r["source_type"] == "sales_invoice")
    voucher = next(r for r in rows if r["source_type"] == "receipt_voucher")
    assert invoice["debit"] == Decimal("100.00") and invoice["credit"] == Decimal("0")
    assert voucher["credit"] == Decimal("30.00") and voucher["debit"] == Decimal("0")


# --- 20: reading the statement writes nothing -------------------------------
def test_fetch_statement_is_read_only(schema):
    db = _bound_db(schema)
    _seed(db)
    repo = CustomerStatementReportRepository(db)

    def counts():
        return {
            t: db.fetch_one(f"SELECT COUNT(*) c FROM {t}")["c"]
            for t in ("sales_invoices", "receipt_vouchers", "customers")
        }

    before = counts()
    repo.fetch_statement(CustomerStatementFilters(customer_id=TARGET))
    repo.fetch_statement(CustomerStatementFilters(customer_id=TARGET, date_from="2026-01-01"))
    assert counts() == before
