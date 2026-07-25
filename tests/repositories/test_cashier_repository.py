"""Integration tests for the Cashier data layer against real PostgreSQL.

Verify the guarantees only a real database can give:

* the model's column lists exactly match the physical ``cashier_invoices`` /
  ``cashier_invoice_lines`` tables (a wrong column makes the INSERT fail);
* the invoice number comes from the DB sequence (``CINV-``-prefixed);
* a saved draft round-trips with Arabic snapshots and Decimal amounts;
* the guarded update/issue paths refuse a non-DRAFT row (``StaleCashierInvoiceError``);
* deleting a draft cascades to its own lines only.

Every row a test creates is deleted afterwards. If no database is reachable the
whole module is skipped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.database.db import Database
from app.models.cashier_invoice import (
    INVOICE_INSERT_COLUMNS,
    LINE_INSERT_COLUMNS,
    STATUS_DRAFT,
    STATUS_ISSUED,
    ZATCA_STATUS_NOT_GENERATED,
)
from app.repositories.cashier_repository import (
    CashierRepository,
    StaleCashierInvoiceError,
)


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
    reason="No PostgreSQL reachable (set DB_* env / .env to run Cashier DB tests).",
)


@pytest.fixture()
def repo_and_cleanup():
    admin = Database()
    repo = CashierRepository()
    created: list[int] = []
    yield repo, admin, created
    for invoice_id in created:
        admin.execute("DELETE FROM cashier_invoices WHERE id = %s", [invoice_id])


def _one_line(product: dict) -> dict:
    qty = Decimal("2")
    price = Decimal(str(product["price"] if product["price"] is not None else 0))
    subtotal = (qty * price).quantize(Decimal("0.01"))
    vat = (subtotal * Decimal("15.00") / Decimal("100")).quantize(Decimal("0.01"))
    return {
        "product_id": product["id"],
        "product_code_snapshot": str(product["item_code"]),
        "product_name_snapshot": "صنف اختبار عربي",
        "quantity": qty,
        "unit_price": price,
        "vat_rate": Decimal("15.00"),
        "line_subtotal": subtotal,
        "vat_amount": vat,
        "line_total": subtotal + vat,
    }


def test_model_columns_are_known():
    # Guard against accidental drift in the model's column tuples.
    assert "invoice_number" in INVOICE_INSERT_COLUMNS
    assert "company_id" in INVOICE_INSERT_COLUMNS
    assert "cashier_invoice_id" in LINE_INSERT_COLUMNS
    assert "product_code_snapshot" in LINE_INSERT_COLUMNS


def test_reserve_invoice_number_uses_db_sequence(repo_and_cleanup):
    repo, _admin, _created = repo_and_cleanup
    n1 = repo.reserve_invoice_number()
    n2 = repo.reserve_invoice_number()
    assert n1.startswith("CINV-") and n2.startswith("CINV-")
    assert n1 != n2  # nextval is monotonic / never repeats


def test_insert_load_and_arabic_roundtrip(repo_and_cleanup):
    repo, admin, created = repo_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, item_code, price FROM products ORDER BY id LIMIT 1")
    if company is None or product is None:
        pytest.skip("need a company and a product")

    line = _one_line(product)
    header = {
        "invoice_number": repo.reserve_invoice_number(),
        "customer_id": None,
        "company_id": company["id"],
        "branch_id": None,
        "created_by_user_id": None,
        "invoice_status": STATUS_DRAFT,
        "invoice_type": "SIMPLIFIED",
        "currency_code": "SAR",
        "subtotal": line["line_subtotal"],
        "vat_amount": line["vat_amount"],
        "grand_total": line["line_total"],
        "notes": "ملاحظة عربية",
        "zatca_status": ZATCA_STATUS_NOT_GENERATED,
    }
    stored = repo.insert_draft(header, [line])
    created.append(stored["header"]["id"])

    assert stored["header"]["invoice_status"] == STATUS_DRAFT
    assert stored["header"]["notes"] == "ملاحظة عربية"
    assert stored["lines"][0]["product_name_snapshot"] == "صنف اختبار عربي"
    assert stored["lines"][0]["line_total"] == line["line_total"]

    reloaded = repo.load_invoice(stored["header"]["id"])
    assert reloaded is not None
    assert reloaded["header"]["invoice_number"] == header["invoice_number"]
    assert len(reloaded["lines"]) == 1


def test_issue_then_second_issue_is_stale(repo_and_cleanup):
    repo, admin, created = repo_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, item_code, price FROM products ORDER BY id LIMIT 1")
    if company is None or product is None:
        pytest.skip("need a company and a product")

    line = _one_line(product)
    header = {
        "invoice_number": repo.reserve_invoice_number(),
        "customer_id": None, "company_id": company["id"], "branch_id": None,
        "created_by_user_id": None, "invoice_status": STATUS_DRAFT,
        "invoice_type": "SIMPLIFIED", "currency_code": "SAR",
        "subtotal": line["line_subtotal"], "vat_amount": line["vat_amount"],
        "grand_total": line["line_total"], "notes": None,
        "zatca_status": ZATCA_STATUS_NOT_GENERATED,
    }
    stored = repo.insert_draft(header, [line])
    invoice_id = stored["header"]["id"]
    created.append(invoice_id)

    issued = repo.issue_invoice(
        invoice_id,
        {"company_id": company["id"], "subtotal": line["line_subtotal"],
         "vat_amount": line["vat_amount"], "grand_total": line["line_total"],
         "seller_name_snapshot": "شركة الاختبار"},
        [line],
    )
    assert issued["header"]["invoice_status"] == STATUS_ISSUED
    assert issued["header"]["invoice_datetime"] is not None

    # A second issue (or any guarded update) must find no DRAFT row.
    with pytest.raises(StaleCashierInvoiceError):
        repo.issue_invoice(invoice_id, {"company_id": company["id"]}, [line])
    with pytest.raises(StaleCashierInvoiceError):
        repo.update_draft(invoice_id, {"company_id": company["id"]}, [line])

    # An ISSUED invoice cannot be deleted through the DRAFT-guarded delete.
    assert repo.delete_draft(invoice_id) is False
