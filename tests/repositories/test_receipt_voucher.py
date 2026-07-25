"""Tests for the Receipt Vouchers data layer (سندات القبض).

Two tiers:

* Pure unit tests for the numbering formatter (no database) — always run.
* Integration tests against a real PostgreSQL that exercise the guarantees only
  the database can give: the ``PA-001`` sequence (first number, atomic
  increments, concurrency), the ``amount > 0`` / ``payment_type`` CHECK
  constraints, the UNIQUE voucher number, the RESTRICT foreign keys to
  customers/companies, the CURRENT_DATE default and the updated_at trigger.

Isolation: every integration test runs inside a throwaway schema
(``rv_test_<n>``) created in setup and ``DROP SCHEMA ... CASCADE``-d in teardown.
Stub ``customers`` / ``companies`` / ``app_users`` parent tables and the
``receipt_vouchers`` table are all created *inside* that schema via
``search_path``, so nothing in ``public`` (real app data) is created, altered, or
dropped. If no database is reachable the integration module is skipped; the pure
unit tests still run.
"""

from __future__ import annotations

import os
import threading
from decimal import Decimal

import pytest

from app.models.receipt_voucher import RECEIPT_VOUCHERS_SCHEMA_SQL
from app.repositories.receipt_voucher_repository import ReceiptVoucherRepository
from app.services.receipt_voucher_numbering_service import (
    ReceiptVoucherNumberingService,
    format_number,
    parse_sequence_value,
)


# ============================================================================
# Pure unit tests — numbering formatter (no database)
# ============================================================================

def test_format_number_first_is_pa_001():
    assert format_number(1) == "PA-001"


def test_format_number_is_sequential_and_zero_padded():
    assert [format_number(n) for n in (1, 2, 3, 4)] == [
        "PA-001",
        "PA-002",
        "PA-003",
        "PA-004",
    ]


def test_format_number_grows_past_999():
    assert format_number(999) == "PA-999"
    assert format_number(1000) == "PA-1000"


def test_parse_sequence_value_roundtrips_auto_numbers():
    assert parse_sequence_value("PA-001") == 1
    assert parse_sequence_value("PA-042") == 42
    assert parse_sequence_value("PA-1000") == 1000


def test_parse_sequence_value_ignores_non_auto_numbers():
    assert parse_sequence_value("REFUND-1") is None
    assert parse_sequence_value("PA-XYZ") is None
    assert parse_sequence_value("") is None
    assert parse_sequence_value(None) is None


# ============================================================================
# Integration tests — real PostgreSQL, throwaway schema
# ============================================================================

from app.database.db import Database  # noqa: E402  (after the pure tests above)

_SCHEMA_SEQ = iter(range(1, 1_000_000))


def _try_connect() -> Database | None:
    try:
        db = Database()
        db.execute("SELECT 1")
        return db
    except Exception:
        return None


_probe = _try_connect()
_needs_db = pytest.mark.skipif(
    _probe is None,
    reason="No PostgreSQL reachable (set DB_* env / .env to run receipt-voucher DB tests).",
)


def _bound_db(schema: str) -> Database:
    """A fresh Database whose connection has search_path set to ``schema, public``."""
    db = Database()
    db.execute(f'SET search_path TO "{schema}", public')
    return db


# Minimal stand-ins for the real master tables so the FKs resolve *inside* the
# throwaway schema (nothing in public is referenced or touched).
_STUB_PARENTS_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id integer PRIMARY KEY,
    customer_name varchar(200)
);
CREATE TABLE IF NOT EXISTS companies (
    id integer PRIMARY KEY,
    name_ar varchar(200)
);
CREATE TABLE IF NOT EXISTS app_users (
    id integer PRIMARY KEY,
    username varchar(100)
);
INSERT INTO customers (customer_id, customer_name) VALUES (1, 'عميل تجريبي') ON CONFLICT DO NOTHING;
INSERT INTO companies (id, name_ar) VALUES (1, 'شركة تجريبية') ON CONFLICT DO NOTHING;
INSERT INTO app_users (id, username) VALUES (1, 'tester') ON CONFLICT DO NOTHING;
"""

CUSTOMER_ID = 1
COMPANY_ID = 1
USER_ID = 1


@pytest.fixture()
def schema():
    name = f"rv_test_{next(_SCHEMA_SEQ)}_{os.getpid()}"
    admin = Database()
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def _prepared_db(schema: str) -> Database:
    """Bound Database with stub parents + receipt_vouchers schema created."""
    db = _bound_db(schema)
    db.execute_script(_STUB_PARENTS_SQL)
    db.execute_script(RECEIPT_VOUCHERS_SCHEMA_SQL)
    return db


@pytest.fixture()
def repo(schema):
    return ReceiptVoucherRepository(_prepared_db(schema))


def _insert(repo: ReceiptVoucherRepository, **overrides):
    """Insert a valid voucher, letting callers override any field."""
    params = dict(
        customer_id=CUSTOMER_ID,
        company_id=COMPANY_ID,
        payment_type="cash",
        amount=Decimal("100.00"),
    )
    params.update(overrides)
    return repo.insert_voucher(**params)


# --- schema / DDL -----------------------------------------------------------

@_needs_db
def test_ensure_schema_is_idempotent(schema):
    db = _bound_db(schema)
    db.execute_script(_STUB_PARENTS_SQL)
    r = ReceiptVoucherRepository(db)
    r.ensure_schema()
    r.ensure_schema()  # second run must not raise
    row = _insert(r)
    assert row["voucher_number"] == "PA-001"


# --- 3 & 4: first number PA-001, then sequential --------------------------

@_needs_db
def test_first_voucher_number_is_pa_001(repo):
    row = _insert(repo)
    assert row["voucher_number"] == "PA-001"


@_needs_db
def test_voucher_numbers_increment(repo):
    numbers = [_insert(repo)["voucher_number"] for _ in range(4)]
    assert numbers == ["PA-001", "PA-002", "PA-003", "PA-004"]


@_needs_db
def test_numbering_service_next_matches_db_default(schema):
    """The Python formatter and the SQL DEFAULT produce identical numbers."""
    svc = ReceiptVoucherNumberingService(ReceiptVoucherRepository(_prepared_db(schema)))
    # peek does not consume; first automatic number is PA-001.
    assert svc.peek_next_number() == "PA-001"
    # Inserting with the DB default yields the same value peek predicted.
    repo = ReceiptVoucherRepository(_bound_db(schema))
    assert _insert(repo)["voucher_number"] == "PA-001"
    # After one consumed, the next automatic number is PA-002.
    assert svc.peek_next_number() == "PA-002"


# --- 5: manual editing is stored --------------------------------------------

@_needs_db
def test_manual_voucher_number_is_stored(repo):
    row = _insert(repo, voucher_number="PA-500")
    assert row["voucher_number"] == "PA-500"
    assert repo.get_by_number("PA-500")["id"] == row["id"]


@_needs_db
def test_manual_number_pushes_sequence_via_service(schema):
    repo = ReceiptVoucherRepository(_prepared_db(schema))
    svc = ReceiptVoucherNumberingService(repo)
    _insert(repo, voucher_number="PA-050")
    svc.register_manual_number("PA-050")
    nxt = _insert(repo)  # automatic (DB default)
    assert nxt["voucher_number"] == "PA-051"


@_needs_db
def test_free_form_manual_number_is_stored_and_ignored_by_sequence(schema):
    repo = ReceiptVoucherRepository(_prepared_db(schema))
    svc = ReceiptVoucherNumberingService(repo)
    row = _insert(repo, voucher_number="REFUND-2026-1")
    assert row["voucher_number"] == "REFUND-2026-1"
    svc.register_manual_number("REFUND-2026-1")  # not PA-<n> -> no-op
    assert _insert(repo)["voucher_number"] == "PA-001"


# --- 6: duplicate voucher numbers rejected ----------------------------------

@_needs_db
def test_duplicate_voucher_number_raises_at_db(repo):
    import psycopg

    _insert(repo, voucher_number="PA-777")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(repo, voucher_number="PA-777")


@_needs_db
def test_voucher_number_exists_probe(repo):
    row = _insert(repo, voucher_number="PA-321")
    assert repo.voucher_number_exists("PA-321") is True
    assert repo.voucher_number_exists("PA-321", exclude_id=row["id"]) is False
    assert repo.voucher_number_exists("PA-999999") is False


# --- 7: invalid payment types rejected --------------------------------------

@_needs_db
def test_invalid_payment_type_rejected(repo):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(repo, payment_type="cheque")


@_needs_db
def test_both_valid_payment_types_accepted(repo):
    assert _insert(repo, payment_type="cash")["payment_type"] == "cash"
    assert _insert(repo, payment_type="bank_transfer")["payment_type"] == "bank_transfer"


# --- 8: zero and negative amounts rejected ----------------------------------

@_needs_db
def test_zero_amount_rejected(repo):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(repo, amount=Decimal("0.00"))


@_needs_db
def test_negative_amount_rejected(repo):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(repo, amount=Decimal("-1.00"))


@_needs_db
def test_amount_is_numeric_two_dp(repo):
    row = _insert(repo, amount=Decimal("1234.50"))
    assert row["amount"] == Decimal("1234.50")
    assert isinstance(row["amount"], Decimal)


# --- 9 & 10: foreign keys ----------------------------------------------------

@_needs_db
def test_valid_customer_and_company_accepted(repo):
    row = _insert(repo)
    assert row["customer_id"] == CUSTOMER_ID
    assert row["company_id"] == COMPANY_ID


@_needs_db
def test_invalid_customer_id_rejected(repo):
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert(repo, customer_id=999999)


@_needs_db
def test_invalid_company_id_rejected(repo):
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _insert(repo, company_id=999999)


@_needs_db
def test_linked_customer_cannot_be_deleted(schema):
    """RESTRICT: deleting a customer with vouchers raises (delete is blocked)."""
    import psycopg

    db = _prepared_db(schema)
    repo = ReceiptVoucherRepository(db)
    _insert(repo)
    # ON DELETE RESTRICT raises RestrictViolation (SQLSTATE 23001).
    with pytest.raises(psycopg.errors.RestrictViolation):
        db.execute("DELETE FROM customers WHERE customer_id = %s", [CUSTOMER_ID])


@_needs_db
def test_linked_company_cannot_be_deleted(schema):
    import psycopg

    db = _prepared_db(schema)
    repo = ReceiptVoucherRepository(db)
    _insert(repo)
    # ON DELETE RESTRICT raises RestrictViolation (SQLSTATE 23001).
    with pytest.raises(psycopg.errors.RestrictViolation):
        db.execute("DELETE FROM companies WHERE id = %s", [COMPANY_ID])


# --- defaults & audit --------------------------------------------------------

@_needs_db
def test_voucher_date_defaults_to_today(repo):
    from datetime import date

    row = _insert(repo)
    assert row["voucher_date"] == date.today()


@_needs_db
def test_explicit_voucher_date_is_stored(repo):
    from datetime import date

    row = _insert(repo, voucher_date=date(2026, 1, 15))
    assert row["voucher_date"] == date(2026, 1, 15)


@_needs_db
def test_description_supports_unicode(repo):
    row = _insert(repo, description="سداد دفعة نقدية — payment note")
    assert row["description"] == "سداد دفعة نقدية — payment note"


@_needs_db
def test_created_by_updated_by_stored(repo):
    row = _insert(repo, created_by=USER_ID, updated_by=USER_ID)
    assert row["created_by"] == USER_ID
    assert row["updated_by"] == USER_ID


@_needs_db
def test_update_refreshes_updated_at_trigger(repo):
    row = _insert(repo, amount=Decimal("100.00"))
    updated = repo.update_voucher(row["id"], {"amount": Decimal("250.00")})
    assert updated["amount"] == Decimal("250.00")
    assert updated["updated_at"] >= row["updated_at"]


# --- concurrency: no duplicate voucher numbers ------------------------------

@_needs_db
def test_concurrent_creation_has_no_duplicate_numbers(schema):
    n_threads = 25
    results: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    # Ensure the parents + table exist before the threads race on them.
    _prepared_db(schema)

    def worker(i: int) -> None:
        try:
            r = ReceiptVoucherRepository(_bound_db(schema))
            barrier.wait()  # maximise contention
            row = _insert(r)
            with lock:
                results.append(row["voucher_number"])
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors during concurrent insert: {errors[:3]}"
    assert len(results) == n_threads
    assert len(set(results)) == n_threads, "duplicate voucher numbers were generated"
    assert "PA-001" in results
