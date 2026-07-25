"""Business logic for the experimental Cashier/POS screen.

Sits between the UI (``CashierPage``) and ``CashierRepository``. It owns:

* all monetary maths in :class:`decimal.Decimal` (never ``float``), recomputed
  here and never trusted from the UI;
* server-side snapshot building (seller from ``companies``, buyer from
  ``customers``) at local-issue time, so an issued invoice keeps its own copy;
* the unit price loaded straight from ``products.price`` (the Cashier can never
  change a master product price);
* validation with Arabic, user-facing messages;
* the draft-only edit rule and the issued-invoice immutability rule enforced in
  the service (not only via disabled UI buttons);
* concurrency protection: local issue is a single atomic status flip in the
  repository, so the same draft can never be issued twice from two windows.

At issue time it also generates the invoice's local Phase-2 data (UUID, ICV, PIH,
UBL XML, hash and the signature tags) by delegating to
:class:`app.services.cashier_zatca_signing.CashierZatcaSigner`, so the thermal
receipt can print a tags 1–9 QR. That signature comes from a **local test-only**
key, not a ZATCA-onboarded CSID — see that module for exactly what this does and
does not mean. Signing is best-effort: a failure never blocks issuing the
invoice, and :meth:`CashierService.ensure_cashier_zatca_data` retries later.

What it deliberately does **not** do (out of scope this phase): call any ZATCA
API; report or clear invoices; print or export; post accounting journals; move
inventory; touch customer balances; create receipt vouchers; or write anything to
the ``sales_invoice*`` tables.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from app.models.cashier_invoice import (
    CURRENCY_CODE,
    DEFAULT_QUANTITY,
    DEFAULT_VAT_RATE,
    INVOICE_TYPE_SIMPLIFIED,
    MONEY_QUANT,
    QTY_PRICE_QUANT,
    STATUS_DRAFT,
    STATUS_ISSUED,
    VAT_NUMBER_MAX_LEN,
    VAT_RATE_QUANT,
    ZATCA_STATUS_NOT_GENERATED,
)
from app.repositories.cashier_repository import (
    CashierRepository,
    StaleCashierInvoiceError,
)
from app.services.cashier_zatca_signing import CashierZatcaSigner

# Permission codes (module "sales", target "cashier"). Enforced here in
# addition to the UI. Actions reuse the shared permission-registry action codes.
PERM_MODULE = "sales"
PERM_TARGET = "cashier"


def _perm(action: str) -> str:
    return f"{PERM_MODULE}.{PERM_TARGET}.{action}"


class CashierError(Exception):
    """Base for Cashier service errors; ``message`` is Arabic and safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CashierValidationError(CashierError):
    """A validation / business-rule failure."""


class CashierPermissionError(CashierError):
    """The current user lacks permission for the attempted action."""


class CashierConcurrencyError(CashierError):
    """The draft was issued/changed elsewhere (a second window or device)."""


def _to_decimal(value: Any, field: str) -> Decimal:
    """Parse ``value`` into a finite Decimal or raise a validation error."""
    if isinstance(value, Decimal):
        parsed = value
    else:
        if value is None:
            raise CashierValidationError(f"{field} مطلوب.")
        text = str(value).strip().replace(",", "")
        if text == "":
            raise CashierValidationError(f"{field} مطلوب.")
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise CashierValidationError(f"{field}: قيمة رقمية غير صالحة.") from exc
    if not parsed.is_finite():
        raise CashierValidationError(f"{field}: قيمة رقمية غير صالحة.")
    return parsed


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_PRICE_QUANT, rounding=ROUND_HALF_UP)


class CashierService:
    def __init__(
        self,
        repository: CashierRepository | None = None,
        permission_check: Callable[[str], bool] | None = None,
    ) -> None:
        self.repository = repository or CashierRepository()
        # Permissive by default (unit tests / standalone launch); the screen
        # wires this to SESSION.can so the action layer enforces permissions too.
        self._can = permission_check or (lambda _code: True)
        self._signer = CashierZatcaSigner(self.repository)

    # -- permissions ---------------------------------------------------------
    def _require(self, action: str) -> None:
        if not self._can(_perm(action)):
            raise CashierPermissionError("ليس لديك صلاحية لتنفيذ هذا الإجراء.")

    # ======================================================================
    # Master-data reads (thin pass-throughs; search the whole table)
    # ======================================================================
    def list_cashier_products(self, limit: int = 60) -> list[dict[str, Any]]:
        return self.repository.list_products(limit)

    def search_cashier_products(self, keyword: str, limit: int = 60) -> list[dict[str, Any]]:
        return self.repository.search_products(keyword, limit)

    def list_cashier_customers(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_customers(limit)

    def search_cashier_customers(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.search_customers(keyword, limit)

    def list_cashier_companies(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_companies(limit)

    def search_cashier_companies(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.search_companies(keyword, limit)

    def get_company(self, company_id: Any) -> dict[str, Any] | None:
        if company_id in (None, ""):
            return None
        return self.repository.get_company(int(company_id))

    def get_customer(self, customer_id: Any) -> dict[str, Any] | None:
        if customer_id in (None, ""):
            return None
        return self.repository.get_customer(int(customer_id))

    # ======================================================================
    # Line + total calculations (Decimal only)
    # ======================================================================
    def compute_line_amounts(
        self, quantity: Decimal, unit_price: Decimal, vat_rate: Decimal
    ) -> dict[str, Decimal]:
        subtotal = _money(quantity * unit_price)
        vat_amount = _money(subtotal * vat_rate / Decimal(100))
        total = _money(subtotal + vat_amount)
        return {
            "line_subtotal": subtotal,
            "vat_amount": vat_amount,
            "line_total": total,
        }

    def build_line_from_product(
        self, product: dict[str, Any], quantity: Any = DEFAULT_QUANTITY
    ) -> dict[str, Any]:
        """Build a working line dict from a product row + quantity.

        The unit price is taken straight from ``products.price`` — the Cashier
        never sets or edits it — and the VAT rate defaults to 15%.
        """
        qty = _qty(_to_decimal(quantity, "الكمية"))
        if qty <= 0:
            raise CashierValidationError("الكمية يجب أن تكون أكبر من صفر.")
        price = _qty(_to_decimal(product.get("price") if product.get("price") is not None else 0, "السعر"))
        if price < 0:
            raise CashierValidationError("السعر لا يمكن أن يكون سالبًا.")
        vat_rate = DEFAULT_VAT_RATE.quantize(VAT_RATE_QUANT)
        line = {
            "product_id": int(product["id"]),
            "product_code": str(product.get("item_code") or ""),
            "product_name": (product.get("item_name") or "").strip(),
            "quantity": qty,
            "unit_price": price,
            "vat_rate": vat_rate,
        }
        line.update(self.compute_line_amounts(qty, price, vat_rate))
        return line

    # -- pure working-set helpers (used by the UI and covered by tests) -----
    def add_cashier_line(
        self, lines: list[dict[str, Any]], product: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Add a product to the working line list, or +1 if already present.

        Never creates a duplicate line for the same product within one draft.
        """
        product_id = int(product["id"])
        for line in lines:
            if line["product_id"] == product_id:
                return self.update_cashier_line_quantity(
                    lines, product_id, line["quantity"] + Decimal("1")
                )
        lines.append(self.build_line_from_product(product, DEFAULT_QUANTITY))
        return lines

    def update_cashier_line_quantity(
        self, lines: list[dict[str, Any]], product_id: int, quantity: Any
    ) -> list[dict[str, Any]]:
        qty = _qty(_to_decimal(quantity, "الكمية"))
        if qty <= 0:
            raise CashierValidationError("الكمية يجب أن تكون أكبر من صفر.")
        for line in lines:
            if line["product_id"] == int(product_id):
                line["quantity"] = qty
                line.update(
                    self.compute_line_amounts(qty, line["unit_price"], line["vat_rate"])
                )
                return lines
        raise CashierValidationError("السطر غير موجود.")

    def remove_cashier_line(
        self, lines: list[dict[str, Any]], product_id: int
    ) -> list[dict[str, Any]]:
        return [line for line in lines if line["product_id"] != int(product_id)]

    def recalculate_cashier_invoice(
        self, lines: list[dict[str, Any]]
    ) -> dict[str, Decimal]:
        """Recompute every line's amounts in place and return the header totals."""
        for line in lines:
            line.update(
                self.compute_line_amounts(
                    line["quantity"], line["unit_price"], line["vat_rate"]
                )
            )
        return self.compute_totals(lines)

    def compute_totals(self, lines: list[dict[str, Any]]) -> dict[str, Decimal]:
        subtotal = sum((line["line_subtotal"] for line in lines), start=Decimal("0"))
        vat_amount = sum((line["vat_amount"] for line in lines), start=Decimal("0"))
        return {
            "subtotal": _money(subtotal),
            "vat_amount": _money(vat_amount),
            "grand_total": _money(subtotal + vat_amount),
        }

    # ======================================================================
    # Persistable line building (rebuilt from the products table)
    # ======================================================================
    def _build_persist_line(self, raw: dict[str, Any], index: int) -> dict[str, Any]:
        """Rebuild one storable line from raw ``{product_id, quantity}`` input.

        The product snapshot and the unit price are read from the ``products``
        table — never trusted from the UI — so a tampered price can't be stored.
        """
        label = f"السطر {index}"
        product_id = raw.get("product_id")
        if product_id in (None, ""):
            raise CashierValidationError(f"{label}: يجب اختيار الصنف.")
        product = self.repository.get_product(int(product_id))
        if product is None:
            raise CashierValidationError(f"{label}: الصنف غير موجود.")

        qty = _qty(_to_decimal(raw.get("quantity"), f"{label} - الكمية"))
        if qty <= 0:
            raise CashierValidationError(f"{label}: الكمية يجب أن تكون أكبر من صفر.")

        price = _qty(_to_decimal(
            product.get("price") if product.get("price") is not None else 0, f"{label} - السعر"
        ))
        if price < 0:
            raise CashierValidationError(f"{label}: السعر لا يمكن أن يكون سالبًا.")

        vat_rate = DEFAULT_VAT_RATE.quantize(VAT_RATE_QUANT)
        amounts = self.compute_line_amounts(qty, price, vat_rate)
        return {
            "product_id": int(product["id"]),
            "product_code_snapshot": str(product.get("item_code") or ""),
            "product_name_snapshot": (product.get("item_name") or "").strip(),
            "quantity": qty,
            "unit_price": price,
            "vat_rate": vat_rate,
            **amounts,
        }

    def build_lines(
        self, raw_lines: list[dict[str, Any]], *, require_at_least_one: bool
    ) -> list[dict[str, Any]]:
        if require_at_least_one and not raw_lines:
            raise CashierValidationError("يجب إضافة صنف واحد على الأقل إلى الفاتورة.")
        return [self._build_persist_line(raw, i) for i, raw in enumerate(raw_lines, start=1)]

    # ======================================================================
    # Snapshots (rebuilt from the database — never trusted from the UI)
    # ======================================================================
    def build_seller_snapshot(self, company_id: Any) -> dict[str, Any]:
        if company_id in (None, ""):
            raise CashierValidationError("يجب اختيار الشركة (البائع).")
        company = self.repository.get_company(int(company_id))
        if company is None:
            raise CashierValidationError("الشركة المحددة غير موجودة.")
        vat = (company.get("vat_number") or "").strip()
        if len(vat) > VAT_NUMBER_MAX_LEN:
            raise CashierValidationError("الرقم الضريبي للبائع أطول من الحد المسموح.")
        return {
            "seller_name_snapshot": (company.get("name_ar") or "").strip() or None,
            "seller_vat_number_snapshot": vat or None,
            "seller_commercial_registration_snapshot": (
                (company.get("commercial_registration") or "").strip() or None
            ),
            "seller_address_snapshot": (company.get("address_ar") or None),
        }

    def build_buyer_snapshot(self, customer_id: Any) -> dict[str, Any]:
        """Buyer snapshot from ``customers``; all-NULL when no customer chosen."""
        if customer_id in (None, ""):
            return {"buyer_name_snapshot": None, "buyer_vat_number_snapshot": None}
        customer = self.repository.get_customer(int(customer_id))
        if customer is None:
            raise CashierValidationError("العميل المحدد غير موجود.")
        vat = (customer.get("vat_number") or "").strip()
        if len(vat) > VAT_NUMBER_MAX_LEN:
            raise CashierValidationError("الرقم الضريبي للعميل أطول من الحد المسموح.")
        return {
            "buyer_name_snapshot": (customer.get("customer_name") or "").strip() or None,
            "buyer_vat_number_snapshot": vat or None,
        }

    # ======================================================================
    # Reads
    # ======================================================================
    def get_cashier_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        return self.repository.load_invoice(invoice_id)

    def list_cashier_drafts(self, limit: int = 300) -> list[dict[str, Any]]:
        return self.repository.list_drafts(limit)

    def search_cashier_invoices(
        self,
        keyword: str = "",
        company_id: int | None = None,
        customer_id: int | None = None,
        status: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Search ALL cashier invoices (any status) — used by the F1 lookup."""
        return self.repository.search_invoices(keyword, company_id, customer_id, status, limit)

    # ======================================================================
    # Create / save / issue / delete
    # ======================================================================
    def create_cashier_draft(self, *, user_id: int | None = None) -> dict[str, Any]:
        """Start a new in-memory draft with a DB-generated invoice number.

        The number is reserved from the concurrency-safe sequence and shown at
        once; the header row is inserted on first save (the ``company_id`` column
        is NOT NULL, so persistence requires a company). No DB write happens here
        beyond reserving the number.
        """
        self._require("save")
        return {
            "id": None,
            "invoice_number": self.repository.reserve_invoice_number(),
            "invoice_status": STATUS_DRAFT,
            "invoice_type": INVOICE_TYPE_SIMPLIFIED,
            "currency_code": CURRENCY_CODE,
            "zatca_status": ZATCA_STATUS_NOT_GENERATED,
            "created_by_user_id": user_id,
        }

    @staticmethod
    def _clean_notes(value: Any) -> str | None:
        notes = value.strip() if isinstance(value, str) else value
        return notes or None

    def save_cashier_draft(
        self, invoice_id: int | None, form: dict[str, Any], *, user_id: int | None = None
    ) -> dict[str, Any]:
        """Validate + persist a draft (insert if new, else update its lines).

        A company is required to persist (the ``company_id`` column is NOT NULL);
        a customer is optional. Recomputes all totals server-side. Never writes
        ZATCA / accounting / inventory / receipt rows.
        """
        self._require("save" if invoice_id is None else "edit")
        company_id = form.get("company_id")
        if company_id in (None, ""):
            raise CashierValidationError("يجب اختيار الشركة قبل حفظ المسودة.")
        customer_id = form.get("customer_id") or None
        # Validate the customer exists if one was chosen.
        if customer_id is not None and self.repository.get_customer(int(customer_id)) is None:
            raise CashierValidationError("العميل المحدد غير موجود.")

        lines = self.build_lines(form.get("lines") or [], require_at_least_one=False)
        totals = self.compute_totals(lines)
        notes = self._clean_notes(form.get("notes"))

        if invoice_id is None:
            invoice_number = form.get("invoice_number") or self.repository.reserve_invoice_number()
            header = {
                "invoice_number": invoice_number,
                "customer_id": int(customer_id) if customer_id is not None else None,
                "company_id": int(company_id),
                "branch_id": None,
                "created_by_user_id": user_id,
                "invoice_status": STATUS_DRAFT,
                "invoice_type": INVOICE_TYPE_SIMPLIFIED,
                "currency_code": CURRENCY_CODE,
                **totals,
                "notes": notes,
                "zatca_status": ZATCA_STATUS_NOT_GENERATED,
            }
            return self.repository.insert_draft(header, lines)

        existing = self.repository.load_invoice(int(invoice_id))
        if existing is None:
            raise CashierValidationError("الفاتورة غير موجودة.")
        if existing["header"]["invoice_status"] != STATUS_DRAFT:
            raise CashierValidationError("لا يمكن تعديل فاتورة تم إتمامها.")
        header_changes = {
            "customer_id": int(customer_id) if customer_id is not None else None,
            "company_id": int(company_id),
            "branch_id": None,
            **totals,
            "notes": notes,
        }
        try:
            return self.repository.update_draft(int(invoice_id), header_changes, lines)
        except StaleCashierInvoiceError as exc:
            raise CashierConcurrencyError(str(exc)) from exc

    def issue_cashier_invoice_locally(
        self, invoice_id: int | None, form: dict[str, Any], *, user_id: int | None = None
    ) -> dict[str, Any]:
        """Locally issue (complete) a draft: DRAFT -> ISSUED in one transaction.

        Validates a company is selected, at least one line exists, and every
        quantity is > 0. Populates the seller and buyer snapshots from the
        current master data and recomputes totals. This is the only issue path;
        the atomic status flip guards against issuing the same draft twice.

        Once issued, the local Phase-2 data (UUID/ICV/PIH/XML/hash/signature) is
        generated in a separate guarded transaction so the receipt can carry a
        QR. That step is best-effort: if it fails the invoice stays issued and
        correct, just unsigned, and the next print retries it. No ZATCA API call,
        print, accounting, inventory or receipt happens here.
        """
        self._require("post")
        if invoice_id is None:
            raise CashierValidationError("يجب حفظ المسودة قبل إتمام الفاتورة.")
        existing = self.repository.load_invoice(int(invoice_id))
        if existing is None:
            raise CashierValidationError("الفاتورة غير موجودة.")
        if existing["header"]["invoice_status"] != STATUS_DRAFT:
            raise CashierValidationError("الفاتورة ليست مسودة (ربما تم إتمامها مسبقًا).")

        company_id = form.get("company_id")
        if company_id in (None, ""):
            raise CashierValidationError("يجب اختيار الشركة قبل إتمام الفاتورة.")
        customer_id = form.get("customer_id") or None

        lines = self.build_lines(form.get("lines") or [], require_at_least_one=True)
        totals = self.compute_totals(lines)
        seller = self.build_seller_snapshot(company_id)
        buyer = self.build_buyer_snapshot(customer_id)
        notes = self._clean_notes(form.get("notes"))

        header_changes = {
            "customer_id": int(customer_id) if customer_id is not None else None,
            "company_id": int(company_id),
            **totals,
            "notes": notes,
            **seller,
            **buyer,
        }
        try:
            issued = self.repository.issue_invoice(int(invoice_id), header_changes, lines)
        except StaleCashierInvoiceError as exc:
            raise CashierConcurrencyError(str(exc)) from exc
        return self.ensure_cashier_zatca_data(int(invoice_id)) or issued

    def ensure_cashier_zatca_data(self, invoice_id: int) -> dict[str, Any] | None:
        """Generate the local Phase-2 QR data for an issued invoice, if missing.

        Idempotent and safe to call before every print: an already-signed invoice
        keeps its original signature (and therefore its original QR), and a
        draft/cancelled one is left untouched. Never raises — a signing problem
        must not block issuing or previewing, so the invoice simply stays
        unsigned and the receipt says the QR is unavailable.
        """
        try:
            return self._signer.ensure_signed(int(invoice_id))
        except Exception:  # noqa: BLE001 - signing is best-effort, never fatal
            return self.repository.load_invoice(int(invoice_id))

    def unissue_cashier_invoice(
        self, invoice_id: int, *, user_id: int | None = None
    ) -> dict[str, Any]:
        """Revert an ISSUED invoice to DRAFT so it can be edited again.

        Requested behaviour for this experimental phase: an "إلغاء الاعتماد"
        action. Guarded so only an ISSUED invoice can be reverted, and atomic in
        the repository so two windows can't both flip it.
        """
        self._require("unpost")
        existing = self.repository.load_invoice(int(invoice_id))
        if existing is None:
            raise CashierValidationError("الفاتورة غير موجودة.")
        if existing["header"]["invoice_status"] != STATUS_ISSUED:
            raise CashierValidationError("لا يمكن إلغاء اعتماد فاتورة غير مُصدرة.")
        try:
            return self.repository.unissue_invoice(int(invoice_id))
        except StaleCashierInvoiceError as exc:
            raise CashierConcurrencyError(str(exc)) from exc

    def delete_cashier_draft(self, invoice_id: int, *, user_id: int | None = None) -> bool:
        """Delete a single DRAFT invoice (cascade removes only its own lines)."""
        self._require("delete")
        existing = self.repository.load_invoice(int(invoice_id))
        if existing is None:
            raise CashierValidationError("الفاتورة غير موجودة.")
        if existing["header"]["invoice_status"] != STATUS_DRAFT:
            raise CashierValidationError("لا يمكن حذف فاتورة تم إتمامها.")
        return self.repository.delete_draft(int(invoice_id))

    def count_cashier_invoices(self) -> dict[str, int]:
        """``{status: count}`` — lets the screen say what a bulk delete will take."""
        return self.repository.count_invoices_by_status()

    def delete_all_cashier_invoices(
        self, *, include_issued: bool = False, user_id: int | None = None  # noqa: ARG002
    ) -> dict[str, int]:
        """Delete every cashier invoice; returns counts of deleted vs protected.

        By default issued invoices are spared. ``include_issued=True`` deletes
        completed sales too, so it additionally demands the "unpost" permission:
        wiping an issued invoice is at least as strong an act as un-issuing one,
        and must not come free with the right to delete drafts.
        """
        self._require("delete")
        if include_issued:
            self._require("unpost")
        return self.repository.delete_all_invoices(include_issued=include_issued)


__all__ = [
    "CashierService",
    "CashierError",
    "CashierValidationError",
    "CashierPermissionError",
    "CashierConcurrencyError",
    "Decimal",
]
