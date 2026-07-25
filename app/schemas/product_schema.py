"""Validation schema for the Products / Items module (المنتجات / الأصناف).

Pydantic v2 models that enforce the business rules *before* anything reaches the
database, returning clear Arabic-friendly messages. Uniqueness of ``item_code``
depends on the current table contents and is therefore validated in the service
against the database (the UNIQUE index is the final guard); everything that can
be checked from the input alone lives here.

Money and quantity use ``decimal.Decimal`` — never floating point.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.product import (
    MONEY_SCALE,
    QUANTITY_SCALE,
)

# --- Arabic validation messages ---------------------------------------------

MSG_ITEM_CODE_EMPTY = "كود الصنف مطلوب ولا يمكن أن يكون فارغاً."
MSG_ITEM_CODE_INVALID = "كود الصنف يجب أن يكون رقماً صحيحاً موجباً."
MSG_ITEM_CODE_DUPLICATE = "كود الصنف مستخدم من قبل، الرجاء اختيار كود آخر."
MSG_ITEM_NAME_EMPTY = "اسم الصنف مطلوب ولا يمكن أن يكون فارغاً."
MSG_QUANTITY_INVALID = "الكمية يجب أن تكون قيمة رقمية صحيحة."
MSG_QUANTITY_NEGATIVE = "الكمية لا يمكن أن تكون بالسالب."
MSG_PRICE_INVALID = "السعر يجب أن يكون قيمة رقمية صحيحة."
MSG_PRICE_NEGATIVE = "السعر لا يمكن أن يكون بالسالب."


def _coerce_item_code(value: object) -> int | None:
    """Validate/normalise an item_code input, or None when not provided.

    Empty / whitespace-only strings mean "not provided" (auto-generate). Any
    other value must be a positive integer.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        value = stripped
    if isinstance(value, bool):
        raise ValueError(MSG_ITEM_CODE_INVALID)
    try:
        code = int(value)
    except (TypeError, ValueError):
        raise ValueError(MSG_ITEM_CODE_INVALID)
    if code <= 0:
        raise ValueError(MSG_ITEM_CODE_INVALID)
    return code


def _to_decimal(value: object, *, invalid_msg: str) -> Decimal:
    """Coerce ``value`` to Decimal or raise ValueError with an Arabic message.

    Rejects NaN/Infinity and unparseable input. Accepts ints, Decimals, numeric
    strings (with surrounding whitespace) and — deliberately last — floats.
    """
    if isinstance(value, bool):  # bool is an int subclass; not a valid amount
        raise ValueError(invalid_msg)
    if isinstance(value, Decimal):
        dec = value
    else:
        try:
            text = str(value).strip()
            if text == "":
                raise ValueError(invalid_msg)
            dec = Decimal(text)
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(invalid_msg)
    if not dec.is_finite():
        raise ValueError(invalid_msg)
    return dec


class ProductCreate(BaseModel):
    """Input for creating a product.

    ``item_code`` is optional: when omitted (None) the service assigns the next
    automatic code from the sequence. When provided it must be a positive
    integer; its uniqueness is checked by the service against the database.
    """

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    item_code: int | None = None
    item_name: str
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")

    @field_validator("item_code", mode="before")
    @classmethod
    def _validate_item_code(cls, value: object) -> int | None:
        return _coerce_item_code(value)

    @field_validator("item_name", mode="before")
    @classmethod
    def _validate_item_name(cls, value: object) -> str:
        if value is None:
            raise ValueError(MSG_ITEM_NAME_EMPTY)
        name = str(value).strip()
        if name == "":
            raise ValueError(MSG_ITEM_NAME_EMPTY)
        return name

    @field_validator("quantity", mode="before")
    @classmethod
    def _validate_quantity(cls, value: object) -> Decimal:
        if value is None:
            return Decimal("0")
        dec = _to_decimal(value, invalid_msg=MSG_QUANTITY_INVALID)
        if dec < 0:
            raise ValueError(MSG_QUANTITY_NEGATIVE)
        return dec.quantize(Decimal(1).scaleb(-QUANTITY_SCALE))

    @field_validator("price", mode="before")
    @classmethod
    def _validate_price(cls, value: object) -> Decimal:
        if value is None:
            return Decimal("0")
        dec = _to_decimal(value, invalid_msg=MSG_PRICE_INVALID)
        if dec < 0:
            raise ValueError(MSG_PRICE_NEGATIVE)
        return dec.quantize(Decimal(1).scaleb(-MONEY_SCALE))


class ProductUpdate(BaseModel):
    """Input for updating a product. Every field is optional (partial update).

    Only the fields supplied are changed; the same per-field rules apply. Note
    ``total`` is never accepted here — it is a generated column recomputed by the
    database from quantity × price.
    """

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    item_code: int | None = None
    item_name: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None

    @field_validator("item_code", mode="before")
    @classmethod
    def _validate_item_code(cls, value: object) -> int | None:
        return _coerce_item_code(value)

    @field_validator("item_name", mode="before")
    @classmethod
    def _validate_item_name(cls, value: object) -> str | None:
        if value is None:
            return None
        name = str(value).strip()
        if name == "":
            raise ValueError(MSG_ITEM_NAME_EMPTY)
        return name

    @field_validator("quantity", mode="before")
    @classmethod
    def _validate_quantity(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        dec = _to_decimal(value, invalid_msg=MSG_QUANTITY_INVALID)
        if dec < 0:
            raise ValueError(MSG_QUANTITY_NEGATIVE)
        return dec.quantize(Decimal(1).scaleb(-QUANTITY_SCALE))

    @field_validator("price", mode="before")
    @classmethod
    def _validate_price(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        dec = _to_decimal(value, invalid_msg=MSG_PRICE_INVALID)
        if dec < 0:
            raise ValueError(MSG_PRICE_NEGATIVE)
        return dec.quantize(Decimal(1).scaleb(-MONEY_SCALE))
