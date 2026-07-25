"""Business logic for the Products / Items module (المنتجات / الأصناف).

Sits between the UI (out of scope for this task) and ``ProductsRepository``. It
validates input via the Pydantic schema, enforces item-code uniqueness against
the database, assigns automatic codes from the sequence, and delegates all SQL to
the repository. Total is computed by the database (generated column) and is never
trusted from the caller.

All validation failures raise ``ProductValidationError`` with an Arabic message
suitable for showing directly to the user.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.repositories.products_repository import ProductsRepository
from app.schemas.product_schema import (
    MSG_ITEM_CODE_DUPLICATE,
    MSG_ITEM_CODE_EMPTY,
    ProductCreate,
    ProductUpdate,
)


class ProductValidationError(Exception):
    """Raised when a product fails validation. ``message`` is Arabic-friendly."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _first_error_message(exc: ValidationError) -> str:
    """Extract the first human message from a Pydantic ValidationError.

    Our validators raise ``ValueError`` with ready-made Arabic strings, which
    Pydantic wraps as ``value_error`` entries; we surface that message verbatim.
    """
    for err in exc.errors():
        msg = err.get("msg", "")
        # Pydantic prefixes custom ValueErrors with "Value error, ".
        if msg.startswith("Value error, "):
            return msg[len("Value error, "):]
        if msg:
            return msg
    return "قيمة غير صالحة."


class ProductService:
    def __init__(self, repository: ProductsRepository | None = None) -> None:
        self.repository = repository or ProductsRepository()

    def ensure_schema(self) -> None:
        self.repository.ensure_schema()

    # --- code helpers -------------------------------------------------------
    def suggest_item_code(self) -> int:
        """Advisory next automatic code for pre-filling a form (not a reservation)."""
        return self.repository.peek_next_item_code()

    # --- create -------------------------------------------------------------
    def create_product(
        self,
        item_name: Any,
        quantity: Any = 0,
        price: Any = 0,
        item_code: Any = None,
    ) -> dict[str, Any]:
        """Validate and create a product; return the stored row (incl. total).

        ``item_code`` may be omitted/blank to auto-assign the next sequence code,
        or provided (e.g. after the user edited the suggested code) — in which
        case it is validated for uniqueness before saving.
        """
        try:
            data = ProductCreate(
                item_name=item_name,
                quantity=quantity,
                price=price,
                item_code=item_code,
            )
        except ValidationError as exc:
            raise ProductValidationError(_first_error_message(exc)) from exc

        if data.item_code is not None and self.repository.item_code_exists(data.item_code):
            raise ProductValidationError(MSG_ITEM_CODE_DUPLICATE)

        return self.repository.insert_product(
            item_name=data.item_name,
            quantity=data.quantity,
            price=data.price,
            item_code=data.item_code,
        )

    # --- update -------------------------------------------------------------
    def update_product(self, product_id: int, **changes: Any) -> dict[str, Any]:
        """Validate and apply a partial update; return the updated row.

        Only supplied fields are changed. ``total`` recomputes automatically. A
        new ``item_code`` is checked for uniqueness (ignoring this same row).
        Raises ProductValidationError if the product does not exist.
        """
        existing = self.repository.get_by_id(product_id)
        if existing is None:
            raise ProductValidationError("الصنف غير موجود.")

        # Only validate keys the caller actually passed.
        supplied = {k: v for k, v in changes.items() if k in ("item_code", "item_name", "quantity", "price")}
        try:
            data = ProductUpdate(**supplied)
        except ValidationError as exc:
            raise ProductValidationError(_first_error_message(exc)) from exc

        fields: dict[str, Any] = {}
        if "item_name" in supplied:
            fields["item_name"] = data.item_name
        if "quantity" in supplied:
            fields["quantity"] = data.quantity
        if "price" in supplied:
            fields["price"] = data.price
        if "item_code" in supplied:
            if data.item_code is None:
                raise ProductValidationError(MSG_ITEM_CODE_EMPTY)
            if self.repository.item_code_exists(data.item_code, exclude_id=product_id):
                raise ProductValidationError(MSG_ITEM_CODE_DUPLICATE)
            fields["item_code"] = data.item_code

        updated = self.repository.update_product(product_id, fields)
        if updated is None:
            raise ProductValidationError("الصنف غير موجود.")
        return updated

    # --- reads --------------------------------------------------------------
    def get_product(self, product_id: int) -> dict[str, Any] | None:
        return self.repository.get_by_id(product_id)

    def get_product_by_code(self, item_code: int) -> dict[str, Any] | None:
        return self.repository.get_by_item_code(item_code)

    def list_products(self, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return self.repository.list_products(limit=limit, offset=offset)

    def search_products(self, keyword: str, limit: int = 500) -> list[dict[str, Any]]:
        return self.repository.search_products(keyword, limit=limit)


__all__ = ["ProductService", "ProductValidationError", "Decimal"]
