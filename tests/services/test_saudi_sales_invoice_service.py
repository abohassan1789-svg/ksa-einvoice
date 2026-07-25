"""Unit tests for :class:`SaudiSalesInvoiceService` (no real database).

An in-memory fake repository stands in for ``SaudiSalesInvoiceRepository`` so all
business rules are verified deterministically: Decimal-only maths, server-side
snapshots + recalculation (UI totals never trusted), VAT default of 15%,
invoice-number rules + per-seller uniqueness, draft-only edits, protected /
approved delete rules, delete-all counts, optimistic concurrency, permission
enforcement, and the guarantee that a draft save writes **no** UUID / ICV / hash
/ XML / QR / ZATCA material.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.repositories.saudi_sales_invoice_repository import StaleInvoiceError
from app.services.saudi_sales_invoice_service import (
    SaudiSalesInvoiceConcurrencyError,
    SaudiSalesInvoicePermissionError,
    SaudiSalesInvoiceService,
    SaudiSalesInvoiceValidationError,
)

# --- seed master data -------------------------------------------------------
COMPANIES = [
    {"id": 10, "name_ar": "شركة أ", "name_en": "Company A", "vat_number": "311111111111113", "address_ar": "الرياض"},
    {"id": 11, "name_ar": "شركة ب", "name_en": "Company B", "vat_number": "311222222222223", "address_ar": "جدة"},
]
CUSTOMERS = [
    {"customer_id": 100, "customer_name": "عميل مميز", "phone_number": "0500000000",
     "vat_number": "300000000000003", "address": "عنوان العميل"},
]
# 150 products so "search finds record #150" proves search is not capped at 100.
PRODUCTS = [
    {"id": i, "item_code": 1000 + i, "item_name": f"صنف {i}", "price": Decimal("10.00")}
    for i in range(1, 151)
]

_INV_EXTRA_COLS = (
    "approved_at", "approved_by", "created_at", "updated_at",
)


class FakeSaudiInvoiceRepo:
    """In-memory stand-in for SaudiSalesInvoiceRepository."""

    def __init__(self, companies=None, customers=None, products=None):
        self.companies = {c["id"]: c for c in (companies or COMPANIES)}
        self.customers = {c["customer_id"]: c for c in (customers or CUSTOMERS)}
        self.products = {p["id"]: p for p in (products or PRODUCTS)}
        self.invoices: dict[int, dict] = {}
        self._next_id = 1
        self.protected_ids: set[int] = set()
        # instrumentation
        self.inserted_headers: list[dict] = []
        self.inserted_line_batches: list[list[dict]] = []
        self.inserted_zatca: list[dict] = []
        self.audit_actions: list[str] = []

    # -- master data --
    def get_company(self, i):
        return self.companies.get(int(i))

    def list_companies(self, limit=100):
        return list(self.companies.values())[:limit]

    def search_companies(self, kw, limit=100):
        kw = (kw or "").strip()
        rows = [c for c in self.companies.values()
                if kw in (c["name_ar"] or "") or kw in (c["vat_number"] or "") or kw == str(c["id"])]
        return (rows or list(self.companies.values()))[:limit]

    def get_customer(self, i):
        return self.customers.get(int(i))

    def list_customers(self, limit=100):
        return list(self.customers.values())[:limit]

    def search_customers(self, kw, limit=100):
        kw = (kw or "").strip()
        rows = [c for c in self.customers.values()
                if kw in (c["customer_name"] or "") or str(c["customer_id"]).startswith(kw)]
        return (rows or list(self.customers.values()))[:limit]

    def get_product(self, i):
        return self.products.get(int(i))

    def list_products(self, limit=100):
        return sorted(self.products.values(), key=lambda p: p["item_code"])[:limit]

    def search_products(self, kw, limit=100):
        kw = (kw or "").strip()
        # Search the WHOLE set, then apply limit — not just the first 100.
        rows = [p for p in sorted(self.products.values(), key=lambda p: p["item_code"])
                if kw in p["item_name"] or str(p["item_code"]).startswith(kw)]
        return rows[:limit]

    # -- numbering --
    def invoice_number_exists(self, seller, number, exclude_id=None):
        for inv in self.invoices.values():
            h = inv["header"]
            if h["seller_company_id"] == int(seller) and h["invoice_number"] == number \
                    and h["id"] != exclude_id:
                return True
        return False

    # -- Phase-2 chain reads --
    def next_icv(self, seller_company_id):
        icvs = [
            inv["zatca"]["invoice_counter_value"]
            for inv in self.invoices.values()
            if inv["zatca"] and inv["header"]["seller_company_id"] == int(seller_company_id)
            and inv["zatca"].get("invoice_counter_value") is not None
        ]
        return (max(icvs) + 1) if icvs else 1

    def previous_invoice_hash(self, seller_company_id):
        rows = [
            inv["zatca"]
            for inv in self.invoices.values()
            if inv["zatca"] and inv["header"]["seller_company_id"] == int(seller_company_id)
            and inv["zatca"].get("invoice_hash")
        ]
        if not rows:
            return None
        rows.sort(key=lambda z: z.get("invoice_counter_value") or 0)
        return rows[-1]["invoice_hash"]

    # -- writes --
    def _materialize(self, header, lines, invoice_id, zatca=None):
        h = dict(header)
        h["id"] = invoice_id
        h.setdefault("row_version", 1)
        for col in _INV_EXTRA_COLS:
            h.setdefault(col, None)
        stored_lines = [
            {**line, "id": idx, "invoice_id": invoice_id, "line_number": idx}
            for idx, line in enumerate(lines, start=1)
        ]
        return {"header": h, "lines": stored_lines, "zatca": dict(zatca) if zatca else None}

    def insert_invoice(self, header, lines, audit=None, zatca=None):
        self.inserted_headers.append(dict(header))
        self.inserted_line_batches.append([dict(x) for x in lines])
        if zatca is not None:
            self.inserted_zatca.append(dict(zatca))
        if audit:
            self.audit_actions.append(audit["action"])
        invoice_id = self._next_id
        self._next_id += 1
        self.invoices[invoice_id] = self._materialize(header, lines, invoice_id, zatca)
        return self.invoices[invoice_id]

    def update_invoice(self, invoice_id, expected_row_version, header_changes, lines,
                       audit=None, zatca=None):
        inv = self.invoices.get(int(invoice_id))
        if inv is None or inv["header"]["row_version"] != expected_row_version \
                or inv["header"]["document_status"] != "draft":
            raise StaleInvoiceError("stale")
        inv["header"].update(header_changes)
        inv["header"]["row_version"] += 1
        inv["lines"] = [
            {**line, "id": idx, "invoice_id": int(invoice_id), "line_number": idx}
            for idx, line in enumerate(lines, start=1)
        ]
        if zatca is not None:
            inv["zatca"] = dict(zatca)
        if audit:
            self.audit_actions.append(audit["action"])
        return inv

    def load_invoice(self, invoice_id):
        return self.invoices.get(int(invoice_id))

    def has_zatca_material(self, invoice_id):
        return int(invoice_id) in self.protected_ids

    def delete_draft(self, invoice_id, performed_by=None):
        inv = self.invoices.get(int(invoice_id))
        if inv is None or inv["header"]["document_status"] != "draft" \
                or int(invoice_id) in self.protected_ids:
            return False
        del self.invoices[int(invoice_id)]
        self.audit_actions.append("delete_draft")
        return True

    def delete_all_eligible_drafts(self, seller_company_id=None, performed_by=None):
        drafts = [
            i for i, inv in self.invoices.items()
            if inv["header"]["document_status"] == "draft"
            and (seller_company_id is None or inv["header"]["seller_company_id"] == seller_company_id)
        ]
        eligible = [i for i in drafts if i not in self.protected_ids]
        for i in eligible:
            del self.invoices[i]
        return {"deleted": len(eligible), "protected": len(drafts) - len(eligible)}

    def search_invoices(self, keyword="", status=None, limit=300):
        rows = []
        for inv in self.invoices.values():
            h = inv["header"]
            if status and h["document_status"] != status:
                continue
            rows.append(h)
        return rows[:limit]


def make_service(repo=None, permission_check=None):
    return SaudiSalesInvoiceService(repository=repo or FakeSaudiInvoiceRepo(),
                                    permission_check=permission_check)


def valid_form(**overrides):
    form = {
        "invoice_number": "INV-1001",
        "issue_datetime": datetime.datetime(2026, 7, 12, 10, 30),
        "seller_company_id": 10,
        "customer_id": 100,
        "payment_type": "cash",
        "notes": "ملاحظة",
        "lines": [{"product_id": 1, "quantity": "3", "unit_price": "150.00"}],
    }
    form.update(overrides)
    return form


# --- calculations (Decimal) -------------------------------------------------

def test_line_amounts_are_decimal_and_correct():
    svc = make_service()
    amounts = svc.compute_line_amounts(Decimal("3"), Decimal("150.00"), Decimal("15.00"))
    assert amounts["line_amount_before_vat"] == Decimal("450.00")
    assert amounts["vat_amount"] == Decimal("67.50")
    assert amounts["line_total_including_vat"] == Decimal("517.50")
    assert all(isinstance(v, Decimal) for v in amounts.values())


def test_totals_are_decimal_and_sum_lines():
    svc = make_service()
    totals = svc.compute_totals([
        {"line_amount_before_vat": Decimal("450.00"), "vat_amount": Decimal("67.50")},
        {"line_amount_before_vat": Decimal("100.00"), "vat_amount": Decimal("15.00")},
    ])
    assert totals["subtotal_before_vat"] == Decimal("550.00")
    assert totals["vat_total"] == Decimal("82.50")
    assert totals["total_including_vat"] == Decimal("632.50")
    assert all(isinstance(v, Decimal) for v in totals.values())


def test_vat_defaults_to_15_when_absent():
    svc = make_service()
    lines = svc.build_lines([{"product_id": 1, "quantity": "2", "unit_price": "100"}])
    assert lines[0]["vat_rate"] == Decimal("15.00")
    assert lines[0]["vat_amount"] == Decimal("30.00")


# --- unregistered items (no product_id) -------------------------------------

def test_unregistered_line_keeps_typed_code_and_name_without_a_product():
    svc = make_service()
    lines = svc.build_lines([{
        "product_id": None, "product_code": " TMP-9 ", "product_name": " صنف حر ",
        "quantity": "3", "unit_price": "10",
    }])
    assert lines[0]["product_id"] is None          # never claims to be a product
    assert lines[0]["product_code_snapshot"] == "TMP-9"
    assert lines[0]["product_name_snapshot"] == "صنف حر"
    assert lines[0]["line_total_including_vat"] == Decimal("34.50")


def test_unregistered_line_code_is_kept_even_when_it_matches_a_product():
    # Deliberate: a free line stays free; it is not silently linked to product 1.
    svc = make_service()
    registered = svc.build_lines([{"product_id": 1, "quantity": "1", "unit_price": "5"}])[0]
    lines = svc.build_lines([{
        "product_id": None, "product_code": registered["product_code_snapshot"],
        "product_name": "اسم مختلف", "quantity": "1", "unit_price": "5",
    }])
    assert lines[0]["product_id"] is None
    assert lines[0]["product_name_snapshot"] == "اسم مختلف"


@pytest.mark.parametrize("raw, expected", [
    ({"product_code": "K1", "product_name": "   "}, "اسم الصنف"),
    ({"product_code": "x" * 101, "product_name": "اسم"}, "رقم الصنف"),
    ({"product_code": "K1", "product_name": "ن" * 256}, "اسم الصنف"),
])
def test_unregistered_line_rejects_missing_or_oversized_identity(raw, expected):
    # These columns are NOT NULL / bounded, so this must fail as an Arabic
    # validation error rather than as a database error on save.
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError) as exc:
        svc.build_lines([{"product_id": None, "quantity": "1", "unit_price": "1", **raw}])
    assert expected in exc.value.message


@pytest.mark.parametrize("code", ["", "   ", None])
def test_unregistered_line_bills_fine_without_a_code(code):
    """A one-off item is often billed by name alone (user's explicit call)."""
    svc = make_service()
    lines = svc.build_lines([{
        "product_id": None, "product_code": code, "product_name": "صنف بلا رقم",
        "quantity": "2", "unit_price": "50",
    }])
    # Stored as '' — NOT NULL forbids NULL, not an empty string.
    assert lines[0]["product_code_snapshot"] == ""
    assert lines[0]["product_name_snapshot"] == "صنف بلا رقم"
    assert lines[0]["product_id"] is None
    # The line still prices and taxes exactly like any other.
    assert lines[0]["line_total_including_vat"] == Decimal("115.00")


def test_registered_line_still_snapshots_from_the_database():
    svc = make_service()
    lines = svc.build_lines([{
        "product_id": 1, "product_code": "مزيف", "product_name": "مزيف",
        "quantity": "1", "unit_price": "10",
    }])
    # UI-supplied code/name are ignored for a real product: the DB is the source.
    assert lines[0]["product_id"] == 1
    assert lines[0]["product_code_snapshot"] != "مزيف"
    assert lines[0]["product_name_snapshot"] != "مزيف"


# --- snapshots (server-side) ------------------------------------------------

def test_seller_snapshot_fills_vat_and_names():
    svc = make_service()
    snap = svc.build_seller_snapshot(10)
    assert snap["seller_vat_number_snapshot"] == "311111111111113"
    assert snap["seller_name_ar_snapshot"] == "شركة أ"
    assert snap["seller_name_en_snapshot"] == "Company A"


def test_customer_snapshot_fills_vat_name_address():
    svc = make_service()
    snap = svc.build_customer_snapshot(100)
    assert snap["customer_name_snapshot"] == "عميل مميز"
    assert snap["customer_vat_number_snapshot"] == "300000000000003"
    assert snap["customer_address_snapshot"] == "عنوان العميل"


def test_missing_seller_rejected():
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.build_seller_snapshot(None)


# --- search beyond the initial 100 -----------------------------------------

def test_product_search_finds_record_beyond_first_100():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    # item #150 (code 1150) is NOT in the pre-loaded first 100 (codes 1001..1100)
    preloaded_codes = {p["item_code"] for p in svc.list_products(100)}
    assert 1150 not in preloaded_codes
    hits = svc.search_products("1150", 100)
    assert any(p["item_code"] == 1150 for p in hits)


def test_customer_search_hits_full_table():
    repo = FakeSaudiInvoiceRepo(customers=[
        {"customer_id": 500, "customer_name": "بعيد", "phone_number": "", "vat_number": "", "address": ""},
        *CUSTOMERS,
    ])
    svc = make_service(repo)
    hits = svc.search_customers("بعيد", 100)
    assert any(c["customer_id"] == 500 for c in hits)


# --- create draft -----------------------------------------------------------

def test_create_draft_persists_and_recalculates_totals():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    result = svc.create_draft(valid_form())
    header = result["header"]
    assert header["document_status"] == "draft"
    assert header["subtotal_before_vat"] == Decimal("450.00")
    assert header["vat_total"] == Decimal("67.50")
    assert header["total_including_vat"] == Decimal("517.50")
    assert header["invoice_currency_code"] == "SAR"
    assert header["tax_currency_code"] == "SAR"
    assert len(result["lines"]) == 1


def test_create_draft_ignores_ui_supplied_totals():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    form = valid_form(subtotal_before_vat="999999", total_including_vat="1")
    result = svc.create_draft(form)
    # Server recomputed, UI numbers discarded.
    assert result["header"]["subtotal_before_vat"] == Decimal("450.00")
    assert result["header"]["total_including_vat"] == Decimal("517.50")


def test_draft_save_generates_phase2_identity():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    result = svc.create_draft(valid_form())
    zatca = result["zatca"]
    assert zatca is not None
    # Non-cryptographic identity + document are generated.
    assert zatca["uuid"]
    assert zatca["invoice_counter_value"] == 1  # first invoice for the seller
    assert zatca["previous_invoice_hash"]       # genesis PIH for the first
    assert zatca["invoice_hash"]
    assert zatca["generated_xml"]
    assert zatca["qr_code_base64"]
    assert zatca["integration_status"] == "generated"
    # The invoice header itself never carries crypto columns.
    forbidden = {"uuid", "invoice_hash", "cryptographic_stamp", "digital_signature"}
    assert forbidden.isdisjoint(repo.inserted_headers[0].keys())


def test_draft_save_never_generates_stamp_signature_or_key():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    svc.create_draft(valid_form())
    zatca = repo.inserted_zatca[0]
    # These require the EGS private key and must NEVER be produced here.
    for forbidden_key in ("cryptographic_stamp", "digital_signature",
                          "public_key_snapshot", "certificate", "private_key"):
        assert forbidden_key not in zatca
    # No submission row / no ZATCA request id either.
    assert "zatca_request_id" not in zatca
    assert "submitted_at" not in zatca


def _decode_tlv(b64_payload: str) -> dict[int, bytes]:
    """Decode a base64 TLV blob into ``{tag: value_bytes}``."""
    import base64 as _b64

    raw = _b64.b64decode(b64_payload)
    out: dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(raw):
        tag = raw[i]
        length = raw[i + 1]
        value = raw[i + 2:i + 2 + length]
        out[tag] = value
        i += 2 + length
    return out


def _verify_qr_signature(tags: dict[int, bytes]) -> bool:
    """tag 7 (signature) must verify against tag 8 (public key) over tag 6.

    Tag 6 is base64 *text* in the current (reverted) encoding, so it is decoded;
    tags 7/8 are raw DER **bytes** and go to ``cryptography`` directly. This helper
    used to b64decode 7 and 8 as well, which only worked because the generator was
    wrongly putting base64 text in those too — see test_qr_tlv_encoding.py.
    """
    import base64 as _b64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    digest = _b64.b64decode(tags[6])
    pub = serialization.load_der_public_key(tags[8])
    try:
        pub.verify(tags[7], digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return True
    except Exception:  # noqa: BLE001
        return False


def test_saved_qr_has_all_phase2_tags_and_self_consistent_signature():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    result = svc.create_draft(valid_form())
    tags = _decode_tlv(result["zatca"]["qr_code_base64"])
    # Full Phase-2 TLV: facts (1-5), hash (6) and the (test) stamp (7-9).
    assert set(tags).issuperset({1, 2, 3, 4, 5, 6, 7, 8, 9})
    # Tag 6 is base64 text in the current (reverted) encoding.
    assert tags[6].decode("ascii") == result["zatca"]["invoice_hash"]
    # The QR stamp is self-consistent (tag 7 verifies vs tag 8 over tag 6).
    assert _verify_qr_signature(tags)
    # The dedicated cryptographic columns are still never populated by save.
    forbidden = {"cryptographic_stamp", "digital_signature", "public_key_snapshot"}
    assert forbidden.isdisjoint(repo.inserted_zatca[0].keys())


def test_build_preview_qr_includes_full_phase2_tags_without_db():
    svc = make_service()
    preview = {
        "invoice_number": "PREVIEW-1",
        "issue_datetime": datetime.datetime(2026, 4, 5, 15, 57, 55),
        "seller": {"name": "شركة أ", "vat": "311111111111113"},
        "customer": {"name": "عميل", "vat": "300000000000003"},
        "lines": [{
            "name": "صنف", "qty": Decimal("2"), "price": Decimal("100"),
            "before": Decimal("200"), "vat_amount": Decimal("30"),
            "vat_rate": Decimal("15"), "total": Decimal("230"),
        }],
        "totals": {"subtotal": Decimal("200"), "vat": Decimal("30"), "total": Decimal("230")},
    }
    payload = svc.build_preview_qr(preview)
    assert payload
    tags = _decode_tlv(payload)
    assert set(tags).issuperset({1, 2, 3, 4, 5, 6, 7, 8, 9})
    assert tags[1].decode("utf-8") == "شركة أ"
    assert tags[4].decode("ascii") == "230.00"
    assert _verify_qr_signature(tags)


def test_second_invoice_icv_increments_and_pih_chains():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    first = svc.create_draft(valid_form(invoice_number="A"))
    second = svc.create_draft(valid_form(invoice_number="B"))
    assert second["zatca"]["invoice_counter_value"] == 2
    # The second invoice's PIH is the first invoice's hash (chain).
    assert second["zatca"]["previous_invoice_hash"] == first["zatca"]["invoice_hash"]


def test_update_keeps_uuid_and_icv_regenerates_hash():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    created = svc.create_draft(valid_form())
    original_uuid = created["zatca"]["uuid"]
    original_icv = created["zatca"]["invoice_counter_value"]
    original_hash = created["zatca"]["invoice_hash"]
    updated = svc.update_draft(
        created["header"]["id"], created["header"]["row_version"],
        valid_form(lines=[{"product_id": 1, "quantity": "9", "unit_price": "999"}]),
    )
    assert updated["zatca"]["uuid"] == original_uuid       # stable identity
    assert updated["zatca"]["invoice_counter_value"] == original_icv
    assert updated["zatca"]["invoice_hash"] != original_hash  # content changed


def test_update_reuses_uuid_returned_as_uuid_object():
    # psycopg returns a ``uuid`` column as a uuid.UUID object, not a str. The
    # reuse path must coerce it before it reaches the XML builder's escape(),
    # otherwise updating a saved invoice raised "'UUID' object has no attribute
    # 'replace'".
    import uuid as _uuid

    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    created = svc.create_draft(valid_form())
    invoice_id = created["header"]["id"]
    original_uuid = created["zatca"]["uuid"]

    # Simulate the live DB: replace the stored str uuid with a UUID object.
    repo.invoices[invoice_id]["zatca"]["uuid"] = _uuid.UUID(original_uuid)

    updated = svc.update_draft(
        invoice_id, created["header"]["row_version"],
        valid_form(lines=[{"product_id": 1, "quantity": "2", "unit_price": "80"}]),
    )
    assert updated["zatca"]["uuid"] == original_uuid   # coerced back to the str
    assert isinstance(updated["zatca"]["uuid"], str)


def test_snapshots_built_from_db_not_ui():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    # UI cannot smuggle a fake seller name; the service rebuilds from master data.
    form = valid_form(seller_name_ar_snapshot="اسم مزيف")
    result = svc.create_draft(form)
    assert result["header"]["seller_name_ar_snapshot"] == "شركة أ"


# --- validation -------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_blank_invoice_number_rejected(bad):
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(invoice_number=bad))


def test_duplicate_invoice_number_per_seller_rejected():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    svc.create_draft(valid_form(invoice_number="DUP-1"))
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(invoice_number="DUP-1"))


def test_same_number_allowed_for_different_seller():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    svc.create_draft(valid_form(invoice_number="SHARED", seller_company_id=10))
    # Different seller -> allowed.
    result = svc.create_draft(valid_form(invoice_number="SHARED", seller_company_id=11))
    assert result["header"]["seller_company_id"] == 11


def test_invalid_payment_type_rejected():
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(payment_type="نقدي"))  # Arabic label, not a code


def test_zero_quantity_rejected():
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(lines=[{"product_id": 1, "quantity": "0", "unit_price": "10"}]))


def test_negative_price_rejected():
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(lines=[{"product_id": 1, "quantity": "1", "unit_price": "-1"}]))


def test_at_least_one_line_required():
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(lines=[]))


@pytest.mark.parametrize("bad", ["abc", "nan", "inf", "1,5"])
def test_invalid_numeric_quantity_rejected(bad):
    svc = make_service()
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.create_draft(valid_form(lines=[{"product_id": 1, "quantity": bad, "unit_price": "10"}]))


# --- update / draft-only ----------------------------------------------------

def test_update_modifies_only_selected_draft():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form(invoice_number="A"))
    b = svc.create_draft(valid_form(invoice_number="B"))
    updated = svc.update_draft(a["header"]["id"], a["header"]["row_version"],
                               valid_form(invoice_number="A", notes="محدثة"))
    assert updated["header"]["notes"] == "محدثة"
    # B untouched.
    assert repo.load_invoice(b["header"]["id"])["header"]["notes"] == "ملاحظة"


def test_update_increments_row_version():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form())
    original_version = a["header"]["row_version"]
    updated = svc.update_draft(a["header"]["id"], original_version, valid_form())
    assert updated["header"]["row_version"] == original_version + 1


def test_stale_row_version_raises_concurrency():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form())
    with pytest.raises(SaudiSalesInvoiceConcurrencyError):
        svc.update_draft(a["header"]["id"], a["header"]["row_version"] + 5, valid_form())


def test_cannot_edit_approved_invoice():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form())
    repo.invoices[a["header"]["id"]]["header"]["document_status"] = "approved"
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.update_draft(a["header"]["id"], a["header"]["row_version"], valid_form())


# --- delete rules -----------------------------------------------------------

def test_delete_eligible_draft():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form())
    assert svc.delete_draft(a["header"]["id"]) is True
    assert repo.load_invoice(a["header"]["id"]) is None


def test_cannot_delete_approved_invoice():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form())
    repo.invoices[a["header"]["id"]]["header"]["document_status"] = "approved"
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.delete_draft(a["header"]["id"])


def test_cannot_delete_draft_with_zatca_material():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    a = svc.create_draft(valid_form())
    repo.protected_ids.add(a["header"]["id"])
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.delete_draft(a["header"]["id"])


def test_delete_all_reports_deleted_and_protected():
    repo = FakeSaudiInvoiceRepo()
    svc = make_service(repo)
    d1 = svc.create_draft(valid_form(invoice_number="D1"))
    svc.create_draft(valid_form(invoice_number="D2"))
    protected = svc.create_draft(valid_form(invoice_number="P1"))
    approved = svc.create_draft(valid_form(invoice_number="AP"))
    repo.protected_ids.add(protected["header"]["id"])
    repo.invoices[approved["header"]["id"]]["header"]["document_status"] = "approved"
    result = svc.delete_all_drafts()
    assert result["deleted"] == 2          # D1, D2
    assert result["protected"] == 1        # P1 skipped
    assert repo.load_invoice(d1["header"]["id"]) is None
    assert repo.load_invoice(approved["header"]["id"]) is not None  # approved untouched


# --- approval intentionally disabled ---------------------------------------

def test_approval_not_available_and_never_faked():
    svc = make_service()
    assert svc.is_approval_available() is False
    with pytest.raises(SaudiSalesInvoiceValidationError):
        svc.approve(1)


# --- permissions enforced in the service layer ------------------------------

def test_permission_denied_blocks_save():
    svc = make_service(permission_check=lambda code: False)
    with pytest.raises(SaudiSalesInvoicePermissionError):
        svc.create_draft(valid_form())


def test_permission_denied_blocks_delete():
    repo = FakeSaudiInvoiceRepo()
    allow_all = {"n": True}
    svc = SaudiSalesInvoiceService(repository=repo, permission_check=lambda code: allow_all["n"])
    a = svc.create_draft(valid_form())
    allow_all["n"] = False
    with pytest.raises(SaudiSalesInvoicePermissionError):
        svc.delete_draft(a["header"]["id"])
