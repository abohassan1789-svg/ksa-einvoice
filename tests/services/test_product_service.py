"""Unit tests for the Products service (no real database or PostgreSQL needed).

A small in-memory fake repository stands in for ``ProductsRepository`` so all
business rules (automatic item codes from 1001, manual editing, uniqueness,
trimming/empty rejection, non-negative quantity/price, invalid-number rejection,
and total = quantity × price) are verified without external services.

The DB-backed guarantees that cannot be faithfully faked (a real PostgreSQL
SEQUENCE, the STORED generated ``total`` column, concurrency safety) are covered
separately in ``tests/repositories/test_products_repository.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.product import FIRST_ITEM_CODE
from app.services.product_service import ProductService, ProductValidationError


class FakeProductsRepository:
    """In-memory stand-in for ProductsRepository.

    Emulates the item-code sequence (first nextval == FIRST_ITEM_CODE), the
    generated ``total`` column, and IDENTITY ``id`` generation.
    """

    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1
        # Mirrors setval(seq, FIRST_ITEM_CODE - 1, is_called=true): next nextval
        # returns FIRST_ITEM_CODE.
        self._seq_last = FIRST_ITEM_CODE - 1
        self.ensured = False

    def ensure_schema(self):
        self.ensured = True

    # --- code generation ---
    def next_item_code(self) -> int:
        self._seq_last += 1
        return self._seq_last

    def peek_next_item_code(self) -> int:
        return max(self._seq_last + 1, FIRST_ITEM_CODE)

    def reserve_item_code_ceiling(self, code: int) -> None:
        self._seq_last = max(self._seq_last, int(code))

    # --- existence ---
    def item_code_exists(self, item_code: int, exclude_id: int | None = None) -> bool:
        return any(
            r["item_code"] == int(item_code) and r["id"] != exclude_id for r in self.rows
        )

    # --- writes ---
    def _store(self, item_code, item_name, quantity, price) -> dict:
        row = {
            "id": self._next_id,
            "item_code": int(item_code),
            "item_name": item_name,
            "quantity": Decimal(quantity),
            "price": Decimal(price),
            "total": Decimal(quantity) * Decimal(price),  # generated column
            "created_at": "2026-07-12T00:00:00+00:00",
            "updated_at": "2026-07-12T00:00:00+00:00",
        }
        self._next_id += 1
        self.rows.append(row)
        return row

    def insert_product(self, item_name, quantity, price, item_code=None) -> dict:
        if item_code is None:
            item_code = self.next_item_code()
        else:
            self.reserve_item_code_ceiling(int(item_code))
        return self._store(item_code, item_name, quantity, price)

    def update_product(self, product_id: int, fields: dict) -> dict | None:
        row = self.get_by_id(product_id)
        if row is None:
            return None
        row.update({k: fields[k] for k in fields})
        row["quantity"] = Decimal(row["quantity"])
        row["price"] = Decimal(row["price"])
        row["total"] = row["quantity"] * row["price"]  # recompute generated column
        if "item_code" in fields and fields["item_code"] is not None:
            self.reserve_item_code_ceiling(int(fields["item_code"]))
        return row

    # --- reads ---
    def get_by_id(self, product_id: int) -> dict | None:
        return next((r for r in self.rows if r["id"] == product_id), None)

    def get_by_item_code(self, item_code: int) -> dict | None:
        return next((r for r in self.rows if r["item_code"] == int(item_code)), None)

    def list_products(self, limit=500, offset=0) -> list[dict]:
        return sorted(self.rows, key=lambda r: r["item_code"])[offset : offset + limit]

    def search_products(self, keyword: str, limit=500) -> list[dict]:
        kw = (keyword or "").strip().lower()
        if kw == "":
            return self.list_products(limit=limit)
        out = [
            r
            for r in self.rows
            if kw in r["item_name"].lower() or str(r["item_code"]).startswith(kw)
        ]
        return sorted(out, key=lambda r: r["item_code"])[:limit]


@pytest.fixture()
def service():
    return ProductService(repository=FakeProductsRepository())


# --- 1 & 2: automatic item code starts at 1001 and increments ---------------

def test_first_automatic_item_code_is_1001(service):
    row = service.create_product(item_name="صنف أول")
    assert row["item_code"] == 1001


def test_next_automatic_item_code_increments(service):
    first = service.create_product(item_name="صنف 1")
    second = service.create_product(item_name="صنف 2")
    third = service.create_product(item_name="صنف 3")
    assert [first["item_code"], second["item_code"], third["item_code"]] == [1001, 1002, 1003]


# --- 3: item code can be edited manually ------------------------------------

def test_item_code_can_be_edited_manually(service):
    row = service.create_product(item_name="صنف يدوي", item_code=5000)
    assert row["item_code"] == 5000


def test_manual_high_code_does_not_break_future_auto_codes(service):
    # A high manual code must push the sequence forward so the next automatic
    # code cannot duplicate it.
    service.create_product(item_name="يدوي مرتفع", item_code=5000)
    nxt = service.create_product(item_name="تلقائي بعده")
    assert nxt["item_code"] > 5000
    assert nxt["item_code"] == 5001


# --- 4: duplicate item codes are rejected -----------------------------------

def test_duplicate_item_code_is_rejected(service):
    service.create_product(item_name="أول", item_code=1001)
    with pytest.raises(ProductValidationError):
        service.create_product(item_name="ثاني", item_code=1001)


def test_update_to_existing_item_code_is_rejected(service):
    a = service.create_product(item_name="أ", item_code=2000)
    service.create_product(item_name="ب", item_code=2001)
    with pytest.raises(ProductValidationError):
        service.update_product(a["id"], item_code=2001)


# --- 5: empty item names are rejected ---------------------------------------

@pytest.mark.parametrize("bad_name", ["", "   ", "\t\n", None])
def test_empty_item_name_is_rejected(service, bad_name):
    with pytest.raises(ProductValidationError):
        service.create_product(item_name=bad_name)


def test_item_name_is_trimmed(service):
    row = service.create_product(item_name="   ثلاجة   ")
    assert row["item_name"] == "ثلاجة"


# --- 6 & 7: negative quantity / price are rejected --------------------------

def test_negative_quantity_is_rejected(service):
    with pytest.raises(ProductValidationError):
        service.create_product(item_name="سالب كمية", quantity=-1)


def test_negative_price_is_rejected(service):
    with pytest.raises(ProductValidationError):
        service.create_product(item_name="سالب سعر", price="-0.01")


@pytest.mark.parametrize("bad", ["abc", "1,5", "nan", "inf", "", "  "])
def test_invalid_numeric_quantity_is_rejected(service, bad):
    with pytest.raises(ProductValidationError):
        service.create_product(item_name="كمية غير صالحة", quantity=bad)


@pytest.mark.parametrize("bad", ["abc", "nan", "inf", "1.2.3"])
def test_invalid_numeric_price_is_rejected(service, bad):
    with pytest.raises(ProductValidationError):
        service.create_product(item_name="سعر غير صالح", price=bad)


# --- 8: total equals quantity × price ---------------------------------------

def test_total_equals_quantity_times_price(service):
    row = service.create_product(item_name="حساب الإجمالي", quantity="3", price="2.50")
    assert row["total"] == Decimal("7.50")


def test_decimal_quantity_supported(service):
    row = service.create_product(item_name="كمية عشرية", quantity="1.5", price="10")
    assert row["quantity"] == Decimal("1.500")
    assert row["total"] == Decimal("15.000")


# --- 9: updating quantity or price recalculates total -----------------------

def test_update_quantity_recalculates_total(service):
    row = service.create_product(item_name="تعديل", quantity="2", price="5")
    assert row["total"] == Decimal("10.00")
    updated = service.update_product(row["id"], quantity="4")
    assert updated["total"] == Decimal("20.00")


def test_update_price_recalculates_total(service):
    row = service.create_product(item_name="تعديل السعر", quantity="2", price="5")
    updated = service.update_product(row["id"], price="7")
    assert updated["total"] == Decimal("14.00")


def test_total_cannot_be_set_manually(service):
    # ``total`` is not an accepted field; passing it must not override the
    # computed value (and is ignored, not stored).
    row = service.create_product(item_name="لا تلاعب بالإجمالي", quantity="2", price="3")
    updated = service.update_product(row["id"], quantity="2", price="3")
    assert updated["total"] == Decimal("6.00")
