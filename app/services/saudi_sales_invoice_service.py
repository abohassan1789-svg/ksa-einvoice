"""Business logic for the Saudi Phase-2 sales-invoice screen.

Sits between the UI (``SaudiSalesInvoicePage``) and
``SaudiSalesInvoiceRepository``. It owns:

* server-side snapshot building (seller / customer / product), so the stored
  invoice never depends on values the UI *claims*;
* all monetary maths in :class:`decimal.Decimal` (never ``float``) with the
  amounts recomputed here and never trusted from the UI;
* validation with Arabic, user-facing messages;
* the draft-only edit rule and the protected-delete rules;
* optimistic-concurrency handling and audit metadata;
* permission enforcement in the action layer (not only the UI).

What it deliberately does **not** do (out of scope for this task / Phase-2 work
that belongs to a dedicated approval service): generate UUID, ICV, PIH, invoice
hash, XML, QR, signatures or cryptographic stamps; call ZATCA; post accounting
journals; move inventory; touch customer balances or COGS.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from app.services import saudi_egs_test_signer
from app.services import saudi_zatca_generator as zatca_gen
from app.models.sales_invoice import (
    CURRENCY_CODE,
    DEFAULT_UNIT_CODE,
    DEFAULT_VAT_CATEGORY,
    DEFAULT_VAT_RATE,
    MAX_PRODUCT_CODE_LEN,
    MAX_PRODUCT_NAME_LEN,
    MONEY_QUANT,
    PAYMENT_CASH,
    PAYMENT_CREDIT,
    QTY_PRICE_QUANT,
    STATUS_APPROVED,
    STATUS_DRAFT,
)
from app.repositories.saudi_sales_invoice_repository import (
    SaudiSalesInvoiceRepository,
    StaleInvoiceError,
)

# Permission codes (see permission_registry: module "sales", target
# "saudi_sales_invoices"). Enforced here in addition to the UI.
PERM_MODULE = "sales"
PERM_TARGET = "saudi_sales_invoices"


def _perm(action: str) -> str:
    return f"{PERM_MODULE}.{PERM_TARGET}.{action}"


VAT_NUMBER_MAX_LEN = 15  # matches the *_vat_number_snapshot column width


class SaudiSalesInvoiceError(Exception):
    """Base for service errors; ``message`` is Arabic and safe to show as-is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SaudiSalesInvoiceValidationError(SaudiSalesInvoiceError):
    """A validation / business-rule failure."""


class SaudiSalesInvoicePermissionError(SaudiSalesInvoiceError):
    """The current user lacks permission for the attempted action."""


class SaudiSalesInvoiceConcurrencyError(SaudiSalesInvoiceError):
    """The draft was changed by someone else (stale ``row_version``)."""


def _to_decimal(value: Any, field: str) -> Decimal:
    """Parse ``value`` into a finite Decimal or raise a validation error.

    Rejects ``None``/blank, thousands separators, and nan/inf — mirroring the
    products module's numeric rules.
    """
    if isinstance(value, Decimal):
        parsed = value
    else:
        if value is None:
            raise SaudiSalesInvoiceValidationError(f"{field} مطلوب.")
        text = str(value).strip()
        if text == "":
            raise SaudiSalesInvoiceValidationError(f"{field} مطلوب.")
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise SaudiSalesInvoiceValidationError(f"{field}: قيمة رقمية غير صالحة.") from exc
    if not parsed.is_finite():
        raise SaudiSalesInvoiceValidationError(f"{field}: قيمة رقمية غير صالحة.")
    return parsed


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _qty_price(value: Decimal) -> Decimal:
    return value.quantize(QTY_PRICE_QUANT, rounding=ROUND_HALF_UP)


class SaudiSalesInvoiceService:
    def __init__(
        self,
        repository: SaudiSalesInvoiceRepository | None = None,
        permission_check: Callable[[str], bool] | None = None,
    ) -> None:
        self.repository = repository or SaudiSalesInvoiceRepository()
        # Permissive by default (unit tests / standalone launch); the screen
        # wires this to SESSION.can so the action layer enforces permissions too.
        self._can = permission_check or (lambda _code: True)

    # -- permissions ---------------------------------------------------------
    def _require(self, action: str) -> None:
        if not self._can(_perm(action)):
            raise SaudiSalesInvoicePermissionError(
                "ليس لديك صلاحية لتنفيذ هذا الإجراء."
            )

    # ======================================================================
    # Master-data search (thin pass-throughs; searches the whole table)
    # ======================================================================
    def list_companies(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_companies(limit)

    def search_companies(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.search_companies(keyword, limit)

    def get_company(self, company_id: Any) -> dict[str, Any] | None:
        if company_id in (None, ""):
            return None
        return self.repository.get_company(int(company_id))

    def get_customer(self, customer_id: Any) -> dict[str, Any] | None:
        if customer_id in (None, ""):
            return None
        return self.repository.get_customer(int(customer_id))

    def list_customers(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_customers(limit)

    def search_customers(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.search_customers(keyword, limit)

    def list_products(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_products(limit)

    def search_products(self, keyword: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.search_products(keyword, limit)

    # ======================================================================
    # Snapshots (rebuilt from the database — never trusted from the UI)
    # ======================================================================
    def build_seller_snapshot(self, seller_company_id: Any) -> dict[str, Any]:
        if seller_company_id in (None, ""):
            raise SaudiSalesInvoiceValidationError("يجب اختيار الشركة البائعة.")
        company = self.repository.get_company(int(seller_company_id))
        if company is None:
            raise SaudiSalesInvoiceValidationError("الشركة البائعة غير موجودة.")
        vat = (company.get("vat_number") or "").strip()
        if vat == "":
            raise SaudiSalesInvoiceValidationError(
                "الشركة البائعة بدون رقم ضريبي صالح."
            )
        if len(vat) > VAT_NUMBER_MAX_LEN:
            raise SaudiSalesInvoiceValidationError(
                "الرقم الضريبي للبائع أطول من الحد المسموح."
            )
        return {
            "seller_company_id": int(company["id"]),
            "seller_name_ar_snapshot": (company.get("name_ar") or "").strip(),
            "seller_name_en_snapshot": (company.get("name_en") or None),
            "seller_vat_number_snapshot": vat,
        }

    def build_customer_snapshot(self, customer_id: Any) -> dict[str, Any]:
        if customer_id in (None, ""):
            raise SaudiSalesInvoiceValidationError("يجب اختيار العميل.")
        customer = self.repository.get_customer(int(customer_id))
        if customer is None:
            raise SaudiSalesInvoiceValidationError("العميل غير موجود.")
        name = (customer.get("customer_name") or "").strip()
        if name == "":
            raise SaudiSalesInvoiceValidationError("العميل المحدد بدون اسم.")
        vat = (customer.get("vat_number") or "").strip()
        if len(vat) > VAT_NUMBER_MAX_LEN:
            raise SaudiSalesInvoiceValidationError(
                "الرقم الضريبي للعميل أطول من الحد المسموح."
            )
        return {
            "customer_id": int(customer["customer_id"]),
            "customer_name_snapshot": name,
            "customer_vat_number_snapshot": vat or None,
            "customer_address_snapshot": (customer.get("address") or None),
        }

    # ======================================================================
    # Line + total calculations (Decimal only)
    # ======================================================================
    def compute_line_amounts(
        self, quantity: Decimal, unit_price: Decimal, vat_rate: Decimal
    ) -> dict[str, Decimal]:
        before = _money(quantity * unit_price)
        vat_amount = _money(before * vat_rate / Decimal(100))
        total = _money(before + vat_amount)
        return {
            "line_amount_before_vat": before,
            "vat_amount": vat_amount,
            "line_total_including_vat": total,
        }

    @staticmethod
    def _free_item_identity(raw: dict[str, Any], label: str) -> tuple[str, str]:
        """Code + name for a line with no product, validated against the columns.

        **The code is optional** (user's call): a one-off item is often billed
        with a name and nothing else, so an empty code is stored as an empty
        string. That is not a database workaround — ``product_code_snapshot`` is
        NOT NULL, which forbids NULL, not ``''``; the column carries no CHECK on
        its content. Two codeless lines still never merge, because merging keys
        on ``product_id`` and an unregistered line has none.

        The name stays required: it is all that identifies the line on the
        invoice and in the print templates once the code is gone.

        Both columns are bounded (varchar 100 / 255), so an over-long value is
        rejected here with an Arabic message instead of a database error on save.
        """
        code = str(raw.get("product_code") or "").strip()
        name = str(raw.get("product_name") or "").strip()
        if not name:
            raise SaudiSalesInvoiceValidationError(f"{label}: أدخل اسم الصنف.")
        if len(code) > MAX_PRODUCT_CODE_LEN:
            raise SaudiSalesInvoiceValidationError(
                f"{label}: رقم الصنف يجب ألا يزيد عن {MAX_PRODUCT_CODE_LEN} حرفًا."
            )
        if len(name) > MAX_PRODUCT_NAME_LEN:
            raise SaudiSalesInvoiceValidationError(
                f"{label}: اسم الصنف يجب ألا يزيد عن {MAX_PRODUCT_NAME_LEN} حرفًا."
            )
        return code, name

    def _build_line(self, raw: dict[str, Any], index: int) -> dict[str, Any]:
        label = f"السطر {index}"
        product_id = raw.get("product_id")
        if product_id in (None, ""):
            # An unregistered item: the line carries only what the user typed.
            # It is stored with product_id NULL, so it never claims to be a
            # product it is not, and the code is kept verbatim even when it
            # happens to match a registered one.
            product = None
            code, name = self._free_item_identity(raw, label)
        else:
            product = self.repository.get_product(int(product_id))
            if product is None:
                raise SaudiSalesInvoiceValidationError(f"{label}: الصنف غير موجود.")
            code = str(product.get("item_code"))
            name = (product.get("item_name") or "").strip()

        quantity = _qty_price(_to_decimal(raw.get("quantity"), f"{label} - الكمية"))
        if quantity <= 0:
            raise SaudiSalesInvoiceValidationError(
                f"{label}: الكمية يجب أن تكون أكبر من صفر."
            )

        unit_price_raw = raw.get("unit_price")
        if unit_price_raw in (None, "") and product is not None:
            unit_price_raw = product.get("price")
        unit_price = _qty_price(_to_decimal(unit_price_raw, f"{label} - السعر"))
        if unit_price < 0:
            raise SaudiSalesInvoiceValidationError(
                f"{label}: السعر لا يمكن أن يكون سالبًا."
            )

        vat_rate_raw = raw.get("vat_rate")
        vat_rate = (
            DEFAULT_VAT_RATE
            if vat_rate_raw in (None, "")
            else _to_decimal(vat_rate_raw, f"{label} - نسبة الضريبة")
        )
        if vat_rate < 0:
            raise SaudiSalesInvoiceValidationError(
                f"{label}: نسبة الضريبة لا يمكن أن تكون سالبة."
            )
        vat_rate = vat_rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        amounts = self.compute_line_amounts(quantity, unit_price, vat_rate)
        return {
            "product_id": int(product["id"]) if product is not None else None,
            "product_code_snapshot": code,
            "product_name_snapshot": name,
            "unit_code": raw.get("unit_code") or DEFAULT_UNIT_CODE,
            "quantity": quantity,
            "unit_price": unit_price,
            "vat_category_code": DEFAULT_VAT_CATEGORY,
            "vat_rate": vat_rate,
            **amounts,
        }

    def build_lines(self, raw_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not raw_lines:
            raise SaudiSalesInvoiceValidationError(
                "يجب إضافة صنف واحد على الأقل إلى الفاتورة."
            )
        return [self._build_line(raw, i) for i, raw in enumerate(raw_lines, start=1)]

    def compute_totals(self, lines: list[dict[str, Any]]) -> dict[str, Decimal]:
        subtotal = sum(
            (line["line_amount_before_vat"] for line in lines), start=Decimal("0")
        )
        vat_total = sum((line["vat_amount"] for line in lines), start=Decimal("0"))
        return {
            "subtotal_before_vat": _money(subtotal),
            "vat_total": _money(vat_total),
            "total_including_vat": _money(subtotal + vat_total),
        }

    # ======================================================================
    # Header validation
    # ======================================================================
    @staticmethod
    def _clean_invoice_number(value: Any) -> str:
        number = ("" if value is None else str(value)).strip()
        if number == "":
            raise SaudiSalesInvoiceValidationError("رقم الفاتورة مطلوب.")
        return number

    @staticmethod
    def _clean_payment_type(value: Any) -> str:
        if value not in (PAYMENT_CASH, PAYMENT_CREDIT):
            raise SaudiSalesInvoiceValidationError("نوع الدفع غير صالح.")
        return value

    def _prepare_header(
        self, form: dict[str, Any], *, exclude_id: int | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Decimal]]:
        """Validate + rebuild a header from raw UI ``form`` input.

        Returns ``(header_persistable, lines, totals)``. Snapshots and totals are
        derived server-side; nothing monetary is taken from the UI verbatim.
        """
        invoice_number = self._clean_invoice_number(form.get("invoice_number"))
        payment_type = self._clean_payment_type(form.get("payment_type"))

        issue_datetime = form.get("issue_datetime")
        if issue_datetime in (None, ""):
            raise SaudiSalesInvoiceValidationError("تاريخ ووقت الإصدار مطلوب.")

        seller = self.build_seller_snapshot(form.get("seller_company_id"))
        customer = self.build_customer_snapshot(form.get("customer_id"))

        if self.repository.invoice_number_exists(
            seller["seller_company_id"], invoice_number, exclude_id=exclude_id
        ):
            raise SaudiSalesInvoiceValidationError(
                "رقم الفاتورة مستخدم مسبقًا لنفس الشركة البائعة."
            )

        lines = self.build_lines(form.get("lines") or [])
        totals = self.compute_totals(lines)

        notes = form.get("notes")
        notes = notes.strip() if isinstance(notes, str) else notes

        header = {
            "invoice_number": invoice_number,
            "issue_datetime": issue_datetime,
            **seller,
            **customer,
            "payment_type": payment_type,
            "notes": notes or None,
            "invoice_currency_code": CURRENCY_CODE,
            "tax_currency_code": CURRENCY_CODE,
            **{key: value for key, value in totals.items()},
            "document_status": STATUS_DRAFT,
        }
        return header, lines, totals

    # ======================================================================
    # Phase-2 (ZATCA) generation — non-cryptographic only
    # ======================================================================
    def build_zatca_data(
        self,
        header: dict[str, Any],
        lines: list[dict[str, Any]],
        totals: dict[str, Any],
        *,
        reuse: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate the non-cryptographic Phase-2 data for an invoice.

        Produces UUID, ICV, PIH, the UBL XML, its SHA-256 hash and a QR. It
        never produces a cryptographic stamp, digital signature, public key or
        any certificate material (those need the EGS private key, which this app
        does not hold or handle). On update, ``reuse`` keeps the invoice's stable
        identity (UUID / ICV / PIH) while the XML, hash and QR are regenerated.
        """
        seller_id = header["seller_company_id"]
        if reuse and reuse.get("uuid"):
            # psycopg returns a ``uuid`` column as a uuid.UUID object; the XML
            # builder (and its ``escape``) needs a str, so coerce to match the
            # fresh path where ``new_uuid()`` already yields a string.
            uuid = str(reuse["uuid"])
            counter = reuse.get("invoice_counter_value")
            icv = int(counter) if counter is not None else self.repository.next_icv(seller_id)
            pih = reuse.get("previous_invoice_hash") or zatca_gen.GENESIS_PIH
        else:
            uuid = zatca_gen.new_uuid()
            icv = self.repository.next_icv(seller_id)
            pih = self.repository.previous_invoice_hash(seller_id) or zatca_gen.GENESIS_PIH

        issue_dt = header.get("issue_datetime")
        if not isinstance(issue_dt, datetime.datetime):
            issue_dt = datetime.datetime.now()

        xml = zatca_gen.build_invoice_xml(
            invoice_number=header["invoice_number"],
            uuid=uuid,
            issue_datetime=issue_dt,
            icv=icv,
            pih=pih,
            currency=CURRENCY_CODE,
            payment_type=header["payment_type"],
            seller=header,
            customer=header,
            lines=lines,
            totals=totals,
        )
        invoice_hash = zatca_gen.compute_invoice_hash(xml)
        # Local, test-only ECDSA stamp -> QR tags 7/8/9 (NOT a ZATCA CSID; see
        # saudi_egs_test_signer). The dedicated crypto columns stay untouched.
        sig = saudi_egs_test_signer.sign_invoice_hash(invoice_hash) or {}
        qr = zatca_gen.build_qr(
            seller_name=header.get("seller_name_ar_snapshot") or "",
            vat_number=header.get("seller_vat_number_snapshot") or "",
            timestamp=issue_dt,
            total_with_vat=totals["total_including_vat"],
            vat_total=totals["vat_total"],
            invoice_hash=invoice_hash,  # tag 6
            signature=sig.get("signature"),   # tag 7
            public_key=sig.get("public_key"),  # tag 8
            stamp=sig.get("stamp"),            # tag 9
        )
        return {
            "uuid": uuid,
            "invoice_counter_value": icv,
            "previous_invoice_hash": pih,
            "invoice_hash": invoice_hash,
            "generated_xml": xml,
            "qr_code_base64": qr,
            "xml_file_name": f"{header['invoice_number']}_{str(uuid)[:8]}.xml",
            "integration_status": zatca_gen.STATUS_GENERATED,
            "xml_generated_at": datetime.datetime.now().astimezone(),
        }

    def build_preview_qr(self, preview: dict[str, Any]) -> str | None:
        """Build a Phase-2-recognisable QR (tags 1–6) for an *unsaved* preview.

        Mirrors :meth:`build_zatca_data` but works straight off the UI's collected
        values with no database access: it builds a representative XML (placeholder
        ICV/PIH), hashes it, and embeds that hash as tag 6 so the on-screen preview
        of a draft scans as Phase-2-compatible too. Never produces any signature /
        stamp / key. Returns ``None`` (caller falls back to a tags 1–5 QR) on any
        problem.
        """
        try:
            seller = preview.get("seller") or {}
            customer = preview.get("customer") or {}
            totals_in = preview.get("totals") or {}
            issued = preview.get("issue_datetime")
            if not isinstance(issued, datetime.datetime):
                issued = datetime.datetime.now()

            lines: list[dict[str, Any]] = []
            for raw in preview.get("lines") or []:
                lines.append({
                    "quantity": _to_decimal(raw.get("qty"), "الكمية"),
                    "unit_price": _to_decimal(raw.get("price"), "السعر"),
                    "vat_rate": _to_decimal(raw.get("vat_rate"), "نسبة الضريبة"),
                    "unit_code": DEFAULT_UNIT_CODE,
                    "vat_category_code": DEFAULT_VAT_CATEGORY,
                    "product_name_snapshot": raw.get("name") or "",
                    "line_amount_before_vat": _to_decimal(raw.get("before"), "الإجمالي"),
                    "vat_amount": _to_decimal(raw.get("vat_amount"), "الضريبة"),
                    "line_total_including_vat": _to_decimal(raw.get("total"), "المجموع"),
                })

            totals = {
                "subtotal_before_vat": _to_decimal(totals_in.get("subtotal", 0), "الإجمالي"),
                "vat_total": _to_decimal(totals_in.get("vat", 0), "الضريبة"),
                "total_including_vat": _to_decimal(totals_in.get("total", 0), "المجموع"),
            }
            header = {
                "seller_name_ar_snapshot": seller.get("name") or "",
                "seller_vat_number_snapshot": seller.get("vat") or "",
                "customer_name_snapshot": customer.get("name") or "",
                "customer_vat_number_snapshot": customer.get("vat") or "",
            }
            xml = zatca_gen.build_invoice_xml(
                invoice_number=str(preview.get("invoice_number") or ""),
                uuid=zatca_gen.new_uuid(),
                issue_datetime=issued,
                icv=0,
                pih=zatca_gen.GENESIS_PIH,
                currency=CURRENCY_CODE,
                payment_type=PAYMENT_CASH,
                seller=header,
                customer=header,
                lines=lines,
                totals=totals,
            )
            invoice_hash = zatca_gen.compute_invoice_hash(xml)
            sig = saudi_egs_test_signer.sign_invoice_hash(invoice_hash) or {}
            return zatca_gen.build_qr(
                seller_name=header["seller_name_ar_snapshot"],
                vat_number=header["seller_vat_number_snapshot"],
                timestamp=issued,
                total_with_vat=totals["total_including_vat"],
                vat_total=totals["vat_total"],
                invoice_hash=invoice_hash,
                signature=sig.get("signature"),
                public_key=sig.get("public_key"),
                stamp=sig.get("stamp"),
            )
        except Exception:  # noqa: BLE001 - preview QR is best-effort, never fatal
            return None

    # ======================================================================
    # Reads
    # ======================================================================
    def load(self, invoice_id: int) -> dict[str, Any] | None:
        return self.repository.load_invoice(invoice_id)

    def search_invoices(
        self, keyword: str = "", status: str | None = None, limit: int = 300
    ) -> list[dict[str, Any]]:
        return self.repository.search_invoices(keyword, status, limit)

    # ======================================================================
    # Create / update / delete
    # ======================================================================
    def create_draft(
        self, form: dict[str, Any], *, user_id: int | None = None
    ) -> dict[str, Any]:
        """Validate + persist a new draft (header + lines) in one transaction.

        Never writes any Phase-2 cryptographic material or ZATCA row.
        """
        self._require("save")
        header, lines, totals = self._prepare_header(form, exclude_id=None)
        header["created_by"] = user_id
        header["updated_by"] = user_id
        # Generate the non-cryptographic Phase-2 data (UUID / ICV / PIH / XML /
        # hash / QR). No stamp, signature, key or ZATCA call.
        zatca = self.build_zatca_data(header, lines, totals)
        audit = {
            "action": "save",
            "old_status": None,
            "new_status": STATUS_DRAFT,
            "performed_by": user_id,
            "invoice_number_snapshot": header["invoice_number"],
            "seller_company_id_snapshot": header["seller_company_id"],
            "details": {"lines": len(lines), "zatca": "generated"},
        }
        return self.repository.insert_invoice(header, lines, audit, zatca=zatca)

    def update_draft(
        self,
        invoice_id: int,
        expected_row_version: int,
        form: dict[str, Any],
        *,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """Validate + update an existing draft (header + replace lines)."""
        self._require("edit")
        existing = self.repository.load_invoice(invoice_id)
        if existing is None:
            raise SaudiSalesInvoiceValidationError("الفاتورة غير موجودة.")
        if existing["header"]["document_status"] != STATUS_DRAFT:
            raise SaudiSalesInvoiceValidationError(
                "لا يمكن تعديل فاتورة معتمدة."
            )

        header, lines, totals = self._prepare_header(form, exclude_id=int(invoice_id))
        # Only header columns that may legitimately change on a draft update.
        header_changes = {
            key: header[key]
            for key in (
                "invoice_number",
                "issue_datetime",
                "seller_company_id",
                "seller_name_ar_snapshot",
                "seller_name_en_snapshot",
                "seller_vat_number_snapshot",
                "customer_id",
                "customer_name_snapshot",
                "customer_vat_number_snapshot",
                "customer_address_snapshot",
                "payment_type",
                "notes",
                "subtotal_before_vat",
                "vat_total",
                "total_including_vat",
            )
        }
        header_changes["updated_by"] = user_id
        # Regenerate Phase-2 data for the new content, keeping the invoice's
        # stable identity (UUID / ICV / PIH) from the existing record.
        zatca = self.build_zatca_data(header, lines, totals, reuse=existing.get("zatca"))
        audit = {
            "action": "update",
            "old_status": STATUS_DRAFT,
            "new_status": STATUS_DRAFT,
            "performed_by": user_id,
            "invoice_number_snapshot": header["invoice_number"],
            "seller_company_id_snapshot": header["seller_company_id"],
            "details": {"lines": len(lines), "zatca": "regenerated"},
        }
        try:
            return self.repository.update_invoice(
                int(invoice_id), int(expected_row_version), header_changes, lines, audit,
                zatca=zatca,
            )
        except StaleInvoiceError as exc:
            raise SaudiSalesInvoiceConcurrencyError(str(exc)) from exc

    def can_delete(self, invoice: dict[str, Any]) -> tuple[bool, str]:
        """Return ``(deletable, reason)`` for a loaded invoice dict."""
        header = invoice["header"]
        if header["document_status"] != STATUS_DRAFT:
            return False, "لا يمكن حذف فاتورة معتمدة."
        if self.repository.has_zatca_material(header["id"]):
            return False, "لا يمكن حذف فاتورة تحتوي بيانات المرحلة الثانية."
        return True, ""

    def delete_draft(self, invoice_id: int, *, user_id: int | None = None) -> bool:
        """Delete a single eligible draft. Raises on approved/protected."""
        self._require("delete")
        existing = self.repository.load_invoice(invoice_id)
        if existing is None:
            raise SaudiSalesInvoiceValidationError("الفاتورة غير موجودة.")
        deletable, reason = self.can_delete(existing)
        if not deletable:
            raise SaudiSalesInvoiceValidationError(reason)
        return self.repository.delete_draft(int(invoice_id), performed_by=user_id)

    def delete_all_drafts(
        self, *, seller_company_id: int | None = None, user_id: int | None = None
    ) -> dict[str, int]:
        """Delete every eligible draft; returns counts of deleted vs protected."""
        self._require("delete")
        return self.repository.delete_all_eligible_drafts(
            seller_company_id=seller_company_id, performed_by=user_id
        )

    # ======================================================================
    # Approval — intentionally NOT available in this task
    # ======================================================================
    def is_approval_available(self) -> bool:
        """A complete Phase-2 approval service does not exist yet.

        The screen keeps ``approve_button`` disabled while this returns False.
        Approval is never faked by merely flipping ``document_status``.
        """
        return False

    def approve(self, invoice_id: int, *, user_id: int | None = None) -> None:  # noqa: ARG002
        raise SaudiSalesInvoiceValidationError(
            "سيتم تفعيل الاعتماد بعد تنفيذ خدمة الاعتماد ومتطلبات المرحلة الثانية."
        )


__all__ = [
    "SaudiSalesInvoiceService",
    "SaudiSalesInvoiceError",
    "SaudiSalesInvoiceValidationError",
    "SaudiSalesInvoicePermissionError",
    "SaudiSalesInvoiceConcurrencyError",
    "Decimal",
]
