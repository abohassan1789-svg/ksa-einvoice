"""Tests for the experimental Cashier/POS service.

Two layers:

* **Pure calculation tests** (no database) — line/total maths in Decimal, the
  default quantity (1) and VAT rate (15), the "same product increases quantity
  instead of duplicating" rule, quantity-change recalculation and line removal.
* **Integration tests** against the real ``InvPhase2`` database — draft creation
  with a DB-generated number, product/price/name snapshots, local issue with
  seller/buyer snapshots and the DRAFT→ISSUED flip, issued-invoice immutability,
  the no-double-issue guard, and proof that no sales-invoice / receipt /
  accounting / inventory / ZATCA rows are created. Every row the integration
  tests create is deleted afterwards, and existing tables/counts are checked
  unchanged.

If no database is reachable the integration tests self-skip.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.database.db import Database
from app.services.cashier_service import (
    CashierPermissionError,
    CashierService,
    CashierValidationError,
)
from app.repositories.cashier_repository import (
    CashierRepository,
    StaleCashierInvoiceError,
)


# ---------------------------------------------------------------------------
# Pure calculation tests (no database)
# ---------------------------------------------------------------------------

def _svc() -> CashierService:
    # Constructing the service does not open a DB connection (Database is lazy).
    return CashierService()


def test_compute_line_amounts_decimal():
    svc = _svc()
    amounts = svc.compute_line_amounts(Decimal("2"), Decimal("50"), Decimal("15.00"))
    assert amounts["line_subtotal"] == Decimal("100.00")
    assert amounts["vat_amount"] == Decimal("15.00")
    assert amounts["line_total"] == Decimal("115.00")
    # All values are Decimal, never float.
    assert all(isinstance(v, Decimal) for v in amounts.values())


def test_build_line_from_product_defaults():
    svc = _svc()
    product = {"id": 7, "item_code": 1001, "item_name": "شاي أحمر", "price": Decimal("10.00")}
    line = svc.build_line_from_product(product)
    assert line["quantity"] == Decimal("1")          # default quantity = 1
    assert line["vat_rate"] == Decimal("15.00")       # default VAT = 15
    assert line["unit_price"] == Decimal("10.00")     # price from products.price
    assert line["product_code"] == "1001"
    assert line["product_name"] == "شاي أحمر"
    assert line["line_total"] == Decimal("11.50")


def test_same_product_increases_quantity_not_duplicated():
    svc = _svc()
    product = {"id": 7, "item_code": 1001, "item_name": "شاي", "price": Decimal("10")}
    lines: list = []
    svc.add_cashier_line(lines, product)
    svc.add_cashier_line(lines, product)
    svc.add_cashier_line(lines, product)
    assert len(lines) == 1
    assert lines[0]["quantity"] == Decimal("3")
    assert lines[0]["line_subtotal"] == Decimal("30.00")


def test_update_quantity_recalculates_and_zero_rejected():
    svc = _svc()
    product = {"id": 7, "item_code": 1001, "item_name": "شاي", "price": Decimal("10")}
    lines: list = []
    svc.add_cashier_line(lines, product)
    svc.update_cashier_line_quantity(lines, 7, Decimal("4"))
    assert lines[0]["quantity"] == Decimal("4")
    assert lines[0]["line_total"] == Decimal("46.00")
    with pytest.raises(CashierValidationError):
        svc.update_cashier_line_quantity(lines, 7, Decimal("0"))


def test_remove_line_and_totals():
    svc = _svc()
    p1 = {"id": 1, "item_code": 1, "item_name": "A", "price": Decimal("10")}
    p2 = {"id": 2, "item_code": 2, "item_name": "B", "price": Decimal("20")}
    lines: list = []
    svc.add_cashier_line(lines, p1)
    svc.add_cashier_line(lines, p2)
    totals = svc.compute_totals(lines)
    assert totals["subtotal"] == Decimal("30.00")
    assert totals["vat_amount"] == Decimal("4.50")
    assert totals["grand_total"] == Decimal("34.50")
    lines = svc.remove_cashier_line(lines, 1)
    assert len(lines) == 1
    totals = svc.compute_totals(lines)
    assert totals["subtotal"] == Decimal("20.00")
    assert totals["grand_total"] == Decimal("23.00")


def test_totals_equal_sum_of_lines():
    svc = _svc()
    lines: list = []
    for i, price in enumerate([Decimal("3.33"), Decimal("7.77"), Decimal("1.11")], start=1):
        svc.add_cashier_line(lines, {"id": i, "item_code": i, "item_name": f"P{i}", "price": price})
    totals = svc.compute_totals(lines)
    manual_sub = sum(l["line_subtotal"] for l in lines)
    manual_vat = sum(l["vat_amount"] for l in lines)
    assert totals["subtotal"] == manual_sub
    assert totals["vat_amount"] == manual_vat
    assert totals["grand_total"] == manual_sub + manual_vat


# ---------------------------------------------------------------------------
# Deletion (fake repository — a real bulk delete would wipe live drafts)
# ---------------------------------------------------------------------------

class _FakeDeleteRepo:
    """Stands in for CashierRepository so no live invoice is ever deleted."""

    def __init__(self, invoice: dict | None = None) -> None:
        self.invoice = invoice
        self.delete_all_calls: list[bool] = []
        self.deleted_ids: list[int] = []

    def load_invoice(self, invoice_id):
        return self.invoice

    def delete_draft(self, invoice_id):
        self.deleted_ids.append(int(invoice_id))
        return True

    def delete_all_invoices(self, include_issued: bool = False):
        self.delete_all_calls.append(include_issued)
        return {"deleted": 3, "issued_deleted": 1 if include_issued else 0, "protected": 0}


def _invoice(status: str) -> dict:
    return {"header": {"id": 5, "invoice_number": "CINV-000005", "invoice_status": status}}


def test_delete_all_spares_issued_invoices_by_default():
    repo = _FakeDeleteRepo()
    svc = CashierService(repository=repo)
    result = svc.delete_all_cashier_invoices(user_id=1)
    assert repo.delete_all_calls == [False]  # the DRAFT guard stays on
    assert result["issued_deleted"] == 0


def test_delete_all_including_issued_is_passed_through():
    repo = _FakeDeleteRepo()
    svc = CashierService(repository=repo)
    result = svc.delete_all_cashier_invoices(include_issued=True, user_id=1)
    assert repo.delete_all_calls == [True]
    assert result["issued_deleted"] == 1


def test_delete_all_requires_permission():
    repo = _FakeDeleteRepo()
    svc = CashierService(repository=repo, permission_check=lambda perm: False)
    with pytest.raises(CashierPermissionError):
        svc.delete_all_cashier_invoices(user_id=1)
    assert repo.delete_all_calls == []  # denied before touching the database


def test_delete_all_including_issued_also_requires_unpost_permission():
    # A user who may delete drafts must not wipe completed sales without the
    # authority to un-issue them.
    repo = _FakeDeleteRepo()
    svc = CashierService(
        repository=repo, permission_check=lambda perm: not perm.endswith(".unpost")
    )
    with pytest.raises(CashierPermissionError):
        svc.delete_all_cashier_invoices(include_issued=True, user_id=1)
    assert repo.delete_all_calls == []
    # ...while the drafts-only sweep is still allowed for them.
    svc.delete_all_cashier_invoices(user_id=1)
    assert repo.delete_all_calls == [False]


def test_delete_single_draft_refuses_an_issued_invoice():
    repo = _FakeDeleteRepo(_invoice("ISSUED"))
    svc = CashierService(repository=repo)
    with pytest.raises(CashierValidationError):
        svc.delete_cashier_draft(5)
    assert repo.deleted_ids == []


# ---------------------------------------------------------------------------
# Integration tests (real database, rows cleaned up)
# ---------------------------------------------------------------------------

def _try_connect() -> Database | None:
    try:
        db = Database()
        db.execute("SELECT 1")
        return db
    except Exception:
        return None


_probe = _try_connect()
_db_required = pytest.mark.skipif(
    _probe is None,
    reason="No PostgreSQL reachable (set DB_* env / .env to run Cashier DB tests).",
)


@pytest.fixture()
def db_and_cleanup():
    """Yield an admin Database + a list to register cashier invoice ids to purge."""
    admin = Database()
    created: list[int] = []
    yield admin, created
    for invoice_id in created:
        # Direct delete (cascade removes only this invoice's own lines). Removes
        # both DRAFT and ISSUED test rows so nothing persists.
        admin.execute("DELETE FROM cashier_invoices WHERE id = %s", [invoice_id])


def _side_effect_counts(admin: Database) -> dict[str, int]:
    tables = [
        "sales_invoices", "sales_invoice_lines", "sales_invoice_zatca_data",
        "sales_invoice_zatca_submissions", "sales_invoice_audit_logs",
        "receipt_vouchers", "products", "customers", "companies",
    ]
    counts = {}
    for t in tables:
        counts[t] = admin.fetch_one(f"SELECT COUNT(*) c FROM {t}")["c"]
    return counts


@_db_required
def test_cashier_full_workflow_and_no_side_effects(db_and_cleanup):
    admin, created = db_and_cleanup

    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, item_code, item_name, price FROM products ORDER BY id LIMIT 1")
    customer = admin.fetch_one(
        "SELECT customer_id FROM customers WHERE COALESCE(customer_name,'') <> '' "
        "ORDER BY customer_id LIMIT 1"
    )
    if company is None or product is None:
        pytest.skip("need at least one company and one product in InvPhase2")

    before = _side_effect_counts(admin)
    tables_before = {
        r["table_name"] for r in admin.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    }

    svc = CashierService()

    # 1) New draft -> DB-generated number, no row yet.
    draft = svc.create_cashier_draft(user_id=None)
    assert draft["id"] is None
    assert draft["invoice_number"].startswith("CINV-")
    assert draft["invoice_status"] == "DRAFT"

    # 2) Save the draft (WITHOUT a customer) with one line.
    form = {
        "invoice_number": draft["invoice_number"],
        "company_id": company["id"],
        "customer_id": None,
        "notes": "اختبار كاشير",
        "lines": [{"product_id": product["id"], "quantity": Decimal("2")}],
    }
    saved = svc.save_cashier_draft(None, form, user_id=None)
    invoice_id = saved["header"]["id"]
    created.append(invoice_id)

    # DB generated the number; snapshots stay NULL while DRAFT; totals correct.
    assert saved["header"]["invoice_number"] == draft["invoice_number"]
    assert saved["header"]["invoice_status"] == "DRAFT"
    assert saved["header"]["seller_name_snapshot"] is None
    assert saved["header"]["buyer_name_snapshot"] is None
    assert saved["lines"][0]["product_code_snapshot"] == str(product["item_code"])
    assert saved["lines"][0]["product_name_snapshot"] == product["item_name"]
    assert saved["lines"][0]["vat_rate"] == Decimal("15.00")
    assert saved["lines"][0]["unit_price"] == Decimal(str(product["price"]))
    expected_sub = (Decimal("2") * Decimal(str(product["price"]))).quantize(Decimal("0.01"))
    assert saved["header"]["subtotal"] == expected_sub

    # 3) Local issue -> DRAFT becomes ISSUED, snapshots populated.
    issue_form = dict(form)
    issue_form["customer_id"] = customer["customer_id"] if customer else None
    issued = svc.issue_cashier_invoice_locally(invoice_id, issue_form, user_id=None)
    assert issued["header"]["invoice_status"] == "ISSUED"
    assert issued["header"]["seller_name_snapshot"]  # seller snapshot filled
    if customer:
        assert issued["header"]["buyer_name_snapshot"]  # buyer snapshot filled
    # Issuing also generates the local Phase-2 QR data (tags 6-9 from the
    # test-only signer; see app.services.cashier_zatca_signing).
    assert issued["header"]["zatca_status"] == "READY"
    assert issued["header"]["zatca_invoice_hash"]
    assert issued["header"]["zatca_signature"]
    assert issued["header"]["zatca_public_key"]
    assert issued["header"]["zatca_cryptographic_stamp"]
    assert issued["header"]["invoice_uuid"]
    assert int(issued["header"]["zatca_icv"]) >= 1

    # Re-signing is a no-op: the QR of an issued invoice never changes.
    signed_again = svc.ensure_cashier_zatca_data(invoice_id)
    assert signed_again["header"]["zatca_invoice_hash"] == issued["header"]["zatca_invoice_hash"]
    assert signed_again["header"]["zatca_signature"] == issued["header"]["zatca_signature"]

    # 4) Issued invoice cannot be edited through the service.
    with pytest.raises(CashierValidationError):
        svc.save_cashier_draft(invoice_id, issue_form, user_id=None)

    # 5) No double issue (service guard + repo atomic guard).
    with pytest.raises(CashierValidationError):
        svc.issue_cashier_invoice_locally(invoice_id, issue_form, user_id=None)
    repo = CashierRepository()
    with pytest.raises(StaleCashierInvoiceError):
        repo.issue_invoice(invoice_id, {"company_id": company["id"]}, [])

    # 6) No side effects: existing tables + counts unchanged (except our +1 cashier row),
    #    and no new tables were created.
    after = _side_effect_counts(admin)
    assert after == before, "existing sales/receipt/master tables must be unchanged"
    tables_after = {
        r["table_name"] for r in admin.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    }
    assert tables_after == tables_before, "no new tables may be created"


@_db_required
def test_issue_requires_company_and_lines(db_and_cleanup):
    admin, created = db_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, price FROM products ORDER BY id LIMIT 1")
    if company is None or product is None:
        pytest.skip("need a company and a product")

    svc = CashierService()

    # Save without a company must fail (DB company_id is NOT NULL).
    with pytest.raises(CashierValidationError):
        svc.save_cashier_draft(None, {"company_id": None, "lines": []}, user_id=None)

    # Save a draft with a company but no lines, then issuing must fail (empty).
    saved = svc.save_cashier_draft(
        None, {"company_id": company["id"], "customer_id": None, "lines": []}, user_id=None
    )
    created.append(saved["header"]["id"])
    with pytest.raises(CashierValidationError):
        svc.issue_cashier_invoice_locally(
            saved["header"]["id"], {"company_id": company["id"], "lines": []}, user_id=None
        )


@_db_required
def test_search_invoices_by_number_company_customer(db_and_cleanup):
    admin, created = db_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, price FROM products ORDER BY id LIMIT 1")
    customer = admin.fetch_one(
        "SELECT customer_id FROM customers WHERE COALESCE(customer_name,'') <> '' "
        "ORDER BY customer_id LIMIT 1"
    )
    if company is None or product is None:
        pytest.skip("need a company and a product")

    svc = CashierService()
    saved = svc.save_cashier_draft(
        None,
        {"company_id": company["id"],
         "customer_id": customer["customer_id"] if customer else None,
         "lines": [{"product_id": product["id"], "quantity": Decimal("1")}]},
        user_id=None,
    )
    invoice_id = saved["header"]["id"]
    number = saved["header"]["invoice_number"]
    created.append(invoice_id)

    # By invoice number.
    by_number = svc.search_cashier_invoices(keyword=number)
    assert any(r["id"] == invoice_id for r in by_number)
    # By company (drop-down filter).
    by_company = svc.search_cashier_invoices(company_id=company["id"])
    assert any(r["id"] == invoice_id for r in by_company)
    # By customer (drop-down filter), when one was attached.
    if customer:
        by_customer = svc.search_cashier_invoices(customer_id=customer["customer_id"])
        assert any(r["id"] == invoice_id for r in by_customer)
    # Search covers all statuses (this one is a DRAFT).
    assert any(r["invoice_status"] == "DRAFT" and r["id"] == invoice_id for r in by_number)


@_db_required
def test_unissue_reverts_issued_to_editable_draft(db_and_cleanup):
    admin, created = db_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, price FROM products ORDER BY id LIMIT 1")
    if company is None or product is None:
        pytest.skip("need a company and a product")

    svc = CashierService()
    form = {"company_id": company["id"], "customer_id": None,
            "lines": [{"product_id": product["id"], "quantity": Decimal("1")}]}
    saved = svc.save_cashier_draft(None, form, user_id=None)
    invoice_id = saved["header"]["id"]
    created.append(invoice_id)

    issued = svc.issue_cashier_invoice_locally(invoice_id, form, user_id=None)
    assert issued["header"]["invoice_status"] == "ISSUED"

    # Un-issue -> back to DRAFT and editable again.
    reverted = svc.unissue_cashier_invoice(invoice_id, user_id=None)
    assert reverted["header"]["invoice_status"] == "DRAFT"
    # Now a save succeeds (was blocked while ISSUED).
    again = svc.save_cashier_draft(invoice_id, form, user_id=None)
    assert again["header"]["invoice_status"] == "DRAFT"
    # Un-issuing a non-issued invoice is rejected.
    with pytest.raises(CashierValidationError):
        svc.unissue_cashier_invoice(invoice_id, user_id=None)


@_db_required
def test_delete_draft_cascades_only_its_lines(db_and_cleanup):
    admin, created = db_and_cleanup
    company = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    product = admin.fetch_one("SELECT id, price FROM products ORDER BY id LIMIT 1")
    if company is None or product is None:
        pytest.skip("need a company and a product")

    svc = CashierService()
    saved = svc.save_cashier_draft(
        None,
        {"company_id": company["id"], "customer_id": None,
         "lines": [{"product_id": product["id"], "quantity": Decimal("1")}]},
        user_id=None,
    )
    invoice_id = saved["header"]["id"]
    lines_before = admin.fetch_one(
        "SELECT COUNT(*) c FROM cashier_invoice_lines WHERE cashier_invoice_id = %s", [invoice_id]
    )["c"]
    assert lines_before == 1

    assert svc.delete_cashier_draft(invoice_id) is True
    # Header gone + its lines cascaded away.
    assert svc.get_cashier_invoice(invoice_id) is None
    lines_after = admin.fetch_one(
        "SELECT COUNT(*) c FROM cashier_invoice_lines WHERE cashier_invoice_id = %s", [invoice_id]
    )["c"]
    assert lines_after == 0
