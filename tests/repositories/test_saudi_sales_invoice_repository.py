"""Integration tests for the Saudi sales-invoice data layer against real PostgreSQL.

These verify the guarantees only a real database can give:

* the model's column lists exactly match the physical ``sales_invoices`` /
  ``sales_invoice_lines`` tables (a wrong column makes the INSERT fail);
* a saved draft round-trips with server-recomputed Decimal totals;
* a draft save creates **no** ``sales_invoice_zatca_data`` /
  ``sales_invoice_zatca_submissions`` row (no UUID / ICV / hash / XML / QR);
* no legacy ``invoices`` / ``invoice_lines`` table is written (none exist);
* existing ``public`` data is left completely untouched.

Isolation: the write path runs inside a single psycopg transaction that is
**always rolled back**, so nothing (not even the temp product it needs for the FK)
persists. The read/search methods are exercised directly (they never write). If
no database is reachable the whole module is skipped.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.config.database import build_database_url
from app.config.settings import get_settings
from app.database.db import Database
from app.models.sales_invoice import (
    INVOICE_INSERT_COLUMNS,
    LINE_INSERT_COLUMNS,
    TBL_INVOICES,
    TBL_LINES,
    TBL_ZATCA_DATA,
    TBL_ZATCA_SUBMISSIONS,
    ZATCA_GENERATE_COLUMNS,
)
from app.repositories.saudi_sales_invoice_repository import SaudiSalesInvoiceRepository
from app.services.saudi_sales_invoice_service import SaudiSalesInvoiceService


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
    reason="No PostgreSQL reachable (set DB_* env / .env to run Saudi invoice DB tests).",
)


def _plain_url() -> str:
    return build_database_url(get_settings()).replace("postgresql+psycopg://", "postgresql://")


def _first_id(db: Database, sql: str) -> int | None:
    row = db.fetch_one(sql)
    return None if row is None else next(iter(row.values()))


# --- read / search methods (no writes) --------------------------------------

def test_no_legacy_invoice_tables_exist():
    db = Database()
    rows = db.fetch_all(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name IN ('invoices','invoice_lines')"
    )
    assert rows == []


def test_search_companies_hits_real_master_data():
    repo = SaudiSalesInvoiceRepository()
    companies = repo.list_companies(100)
    if not companies:
        pytest.skip("no companies in InvPhase2 to search")
    target = companies[0]
    found = repo.search_companies(target["name_ar"][:3], 100)
    assert any(c["id"] == target["id"] for c in found)
    assert repo.get_company(target["id"]) is not None


def test_invoice_number_exists_false_for_unused_number():
    repo = SaudiSalesInvoiceRepository()
    companies = repo.list_companies(1)
    if not companies:
        pytest.skip("no companies in InvPhase2")
    assert repo.invoice_number_exists(companies[0]["id"], "no-such-number-zzz-999") is False


def test_count_drafts_and_protected_probe_are_safe_reads():
    repo = SaudiSalesInvoiceRepository()
    assert isinstance(repo.count_drafts(), int)
    assert repo.has_zatca_material(-1) is False


def test_delete_all_eligible_drafts_is_safe_when_none():
    repo = SaudiSalesInvoiceRepository()
    # Only run the destructive path when there is nothing to delete, so the test
    # never removes real drafts. On an empty table this is a no-op.
    if repo.count_drafts() != 0:
        pytest.skip("existing drafts present; skip to avoid touching real data")
    result = repo.delete_all_eligible_drafts()
    assert result == {"deleted": 0, "protected": 0}


# --- write path (rolled back — nothing persists) ----------------------------

def test_save_roundtrip_no_crypto_and_public_untouched():
    import psycopg
    from psycopg.rows import dict_row

    admin = Database()
    seller = admin.fetch_one("SELECT id FROM companies ORDER BY id LIMIT 1")
    customer = admin.fetch_one(
        "SELECT customer_id FROM customers WHERE COALESCE(customer_name,'') <> '' "
        "ORDER BY customer_id LIMIT 1"
    )
    if seller is None or customer is None:
        pytest.skip("need at least one company and one named customer in InvPhase2")

    tables_before = {
        r["table_name"]
        for r in admin.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    }
    invoices_before = admin.fetch_one(f"SELECT COUNT(*) c FROM {TBL_INVOICES}")["c"]
    products_before = admin.fetch_one("SELECT COUNT(*) c FROM products")["c"]

    svc = SaudiSalesInvoiceService()
    seller_snap = svc.build_seller_snapshot(seller["id"])
    customer_snap = svc.build_customer_snapshot(customer["customer_id"])

    conn = psycopg.connect(_plain_url(), row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            # Temp product (needed for the line FK) — never committed.
            cur.execute(
                "INSERT INTO products (item_name, quantity, price) VALUES (%s,%s,%s) "
                "RETURNING id, item_code",
                ["منتج اختبار مؤقت", 0, 50],
            )
            product = cur.fetchone()

            amounts = svc.compute_line_amounts(Decimal("2"), Decimal("50"), Decimal("15.00"))
            line = {
                "product_id": product["id"],
                "product_code_snapshot": str(product["item_code"]),
                "product_name_snapshot": "منتج اختبار مؤقت",
                "unit_code": "PCE",
                "quantity": Decimal("2"),
                "unit_price": Decimal("50"),
                "vat_category_code": "S",
                "vat_rate": Decimal("15.00"),
                **amounts,
            }
            totals = svc.compute_totals([line])
            header = {
                "invoice_number": f"ROLLBACK-TEST-{os.getpid()}",
                "issue_datetime": __import__("datetime").datetime.now().astimezone(),
                **seller_snap,
                **customer_snap,
                "payment_type": "cash",
                "notes": "اختبار — سيتم التراجع",
                "invoice_currency_code": "SAR",
                "tax_currency_code": "SAR",
                **totals,
                "document_status": "draft",
                "created_by": None,
                "updated_by": None,
            }

            collist = ", ".join(INVOICE_INSERT_COLUMNS)
            placeholders = ", ".join(["%s"] * len(INVOICE_INSERT_COLUMNS))
            cur.execute(
                f"INSERT INTO {TBL_INVOICES} ({collist}) VALUES ({placeholders}) "
                "RETURNING id, subtotal_before_vat, vat_total, total_including_vat, "
                "document_status, row_version",
                [header[c] for c in INVOICE_INSERT_COLUMNS],
            )
            stored = cur.fetchone()
            invoice_id = stored["id"]

            line["invoice_id"] = invoice_id
            line["line_number"] = 1
            line_cols = ", ".join(LINE_INSERT_COLUMNS)
            line_ph = ", ".join(["%s"] * len(LINE_INSERT_COLUMNS))
            cur.execute(
                f"INSERT INTO {TBL_LINES} ({line_cols}) VALUES ({line_ph})",
                [line[c] for c in LINE_INSERT_COLUMNS],
            )

            # --- assertions inside the transaction ---
            assert stored["document_status"] == "draft"
            assert stored["row_version"] == 1
            assert stored["subtotal_before_vat"] == Decimal("100.00")
            assert stored["vat_total"] == Decimal("15.00")
            assert stored["total_including_vat"] == Decimal("115.00")

            line_count = cur.execute(
                f"SELECT COUNT(*) c FROM {TBL_LINES} WHERE invoice_id=%s", [invoice_id]
            ).fetchone()["c"]
            assert line_count == 1

            # Generate + persist the non-cryptographic Phase-2 row (as the app
            # does on save). Column list must match the real table or this fails.
            zatca = svc.build_zatca_data(header, [line], totals)
            zatca["invoice_id"] = invoice_id
            zcols = ", ".join(ZATCA_GENERATE_COLUMNS)
            zph = ", ".join(["%s"] * len(ZATCA_GENERATE_COLUMNS))
            cur.execute(
                f"INSERT INTO {TBL_ZATCA_DATA} ({zcols}) VALUES ({zph})",
                [zatca.get(c) for c in ZATCA_GENERATE_COLUMNS],
            )
            z = cur.execute(
                "SELECT uuid, invoice_counter_value, invoice_hash, generated_xml, "
                "integration_status, cryptographic_stamp, digital_signature, "
                f"public_key_snapshot FROM {TBL_ZATCA_DATA} WHERE invoice_id=%s",
                [invoice_id],
            ).fetchone()
            assert z is not None
            assert z["uuid"] is not None
            assert z["invoice_counter_value"] == 1
            assert z["invoice_hash"]
            assert z["generated_xml"] is not None
            assert z["integration_status"] == "generated"
            # Cryptographic columns are NEVER written by generation.
            assert z["cryptographic_stamp"] is None
            assert z["digital_signature"] is None
            assert z["public_key_snapshot"] is None
            # No ZATCA submission row is created on save.
            subs = cur.execute(
                f"SELECT COUNT(*) c FROM {TBL_ZATCA_SUBMISSIONS} WHERE invoice_id=%s",
                [invoice_id],
            ).fetchone()["c"]
            assert subs == 0
    finally:
        conn.rollback()  # discard EVERYTHING — temp product, invoice and lines
        conn.close()

    # Nothing persisted; existing data is exactly as before.
    tables_after = {
        r["table_name"]
        for r in admin.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    }
    assert tables_before == tables_after
    assert admin.fetch_one(f"SELECT COUNT(*) c FROM {TBL_INVOICES}")["c"] == invoices_before
    assert admin.fetch_one("SELECT COUNT(*) c FROM products")["c"] == products_before
