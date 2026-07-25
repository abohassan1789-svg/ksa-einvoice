"""Integration tests for ProductsRepository against a real PostgreSQL.

These exercise the guarantees that only a real database can provide:

* the item-code SEQUENCE (first code == 1001, atomic increments),
* the STORED generated ``total`` column (and its recomputation on update),
* concurrency safety (many simultaneous creators never share a code),
* and that creating the products objects leaves existing tables untouched.

Isolation: every test runs inside a throwaway schema (``prod_test_<n>``) created
in setup and ``DROP SCHEMA ... CASCADE``-d in teardown. The products table,
sequence, trigger and indexes are all created *inside* that schema via
``search_path``, so nothing in ``public`` (real app data) is created, altered, or
dropped. If no database is reachable the whole module is skipped.
"""

from __future__ import annotations

import os
import threading
from decimal import Decimal

import pytest

from app.database.db import Database
from app.models.product import PRODUCTS_SCHEMA_SQL
from app.repositories.products_repository import ProductsRepository

# A per-test-run counter for unique schema names (Date/random are avoided so the
# suite stays deterministic across reruns within a session).
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
    reason="No PostgreSQL reachable (set DB_* env / .env to run product DB tests).",
)


def _bound_db(schema: str) -> Database:
    """A fresh Database whose connection has search_path set to ``schema, public``."""
    db = Database()
    db.execute(f'SET search_path TO "{schema}", public')
    return db


@pytest.fixture()
def schema():
    name = f"prod_test_{next(_SCHEMA_SEQ)}_{os.getpid()}"
    admin = Database()
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture()
def repo(schema):
    db = _bound_db(schema)
    r = ProductsRepository(db)
    r.ensure_schema()
    return r


# --- schema / DDL -----------------------------------------------------------

def test_ensure_schema_is_idempotent(schema):
    db = _bound_db(schema)
    r = ProductsRepository(db)
    r.ensure_schema()
    r.ensure_schema()  # second run must not raise
    row = r.insert_product("مكرر آمن", Decimal("1"), Decimal("1"))
    assert row["item_code"] == 1001


# --- 1 & 2: first code 1001, then increments --------------------------------

def test_first_item_code_is_1001(repo):
    row = repo.insert_product("أول صنف", Decimal("0"), Decimal("0"))
    assert row["item_code"] == 1001


def test_item_codes_increment(repo):
    codes = [
        repo.insert_product(f"صنف {i}", Decimal("1"), Decimal("1"))["item_code"]
        for i in range(3)
    ]
    assert codes == [1001, 1002, 1003]


# --- 3: manual editing ------------------------------------------------------

def test_manual_item_code_is_stored(repo):
    row = repo.insert_product("يدوي", Decimal("1"), Decimal("1"), item_code=7777)
    assert row["item_code"] == 7777
    assert repo.get_by_item_code(7777)["id"] == row["id"]


def test_manual_high_code_pushes_sequence(repo):
    repo.insert_product("يدوي مرتفع", Decimal("1"), Decimal("1"), item_code=5000)
    nxt = repo.insert_product("تلقائي", Decimal("1"), Decimal("1"))
    assert nxt["item_code"] == 5001


# --- 4: duplicate rejection (UNIQUE index is the final guard) ----------------

def test_duplicate_item_code_raises_at_db(repo):
    import psycopg

    repo.insert_product("أول", Decimal("1"), Decimal("1"), item_code=1001)
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.insert_product("ثاني", Decimal("1"), Decimal("1"), item_code=1001)


# --- 8: total is a generated column -----------------------------------------

def test_total_is_generated(repo):
    row = repo.insert_product("إجمالي", Decimal("3"), Decimal("2.50"))
    assert row["total"] == Decimal("7.50")


def test_decimal_quantity_and_total(repo):
    row = repo.insert_product("عشري", Decimal("1.5"), Decimal("10.00"))
    assert row["quantity"] == Decimal("1.500")
    assert row["total"] == Decimal("15.000")


# --- 9: update recomputes total (and updated_at trigger fires) --------------

def test_update_recomputes_total(repo):
    row = repo.insert_product("تعديل", Decimal("2"), Decimal("5"))
    assert row["total"] == Decimal("10.00")
    updated = repo.update_product(row["id"], {"quantity": Decimal("4")})
    assert updated["total"] == Decimal("20.00")
    updated2 = repo.update_product(row["id"], {"price": Decimal("7")})
    assert updated2["total"] == Decimal("28.00")
    assert updated2["updated_at"] >= row["updated_at"]


# --- 10: concurrency — no duplicate item codes ------------------------------

def test_concurrent_creation_has_no_duplicate_codes(schema):
    n_threads = 25
    results: list[int] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    # Ensure the table exists before the threads race on it.
    ProductsRepository(_bound_db(schema)).ensure_schema()

    def worker(i: int) -> None:
        try:
            r = ProductsRepository(_bound_db(schema))
            barrier.wait()  # maximise contention
            row = r.insert_product(f"متزامن {i}", Decimal("1"), Decimal("1"))
            with lock:
                results.append(row["item_code"])
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
    assert len(set(results)) == n_threads, "duplicate item codes were generated"
    assert min(results) == 1001


# --- 11: existing tables/data remain unaffected -----------------------------

def test_existing_public_tables_untouched(schema):
    admin = Database()
    before = {
        r["table_name"]
        for r in admin.fetch_all(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    }

    # Do a full round of products work inside the isolated schema.
    r = ProductsRepository(_bound_db(schema))
    r.ensure_schema()
    r.insert_product("عمل معزول", Decimal("2"), Decimal("3"))

    after = {
        row["table_name"]
        for row in admin.fetch_all(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    }
    assert before == after
    # And the isolated work did not leak a products table into public.
    assert "products" not in (after - before)
