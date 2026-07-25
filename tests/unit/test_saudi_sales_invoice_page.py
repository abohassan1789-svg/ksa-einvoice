"""Widget tests for the Saudi sales-invoice screen (headless / offscreen Qt).

The real :class:`SaudiSalesInvoiceService` is driven by an in-memory fake
repository, so no database is touched. These verify the UI contract: RTL, the
required controls, a blank + editable invoice number with no auto-generation in
New mode, VAT defaulting to 15%, seller/customer VAT auto-fill, Decimal totals,
and that an approved invoice loads fully locked.
"""

from __future__ import annotations

import datetime
import os
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

from app.services.saudi_sales_invoice_service import SaudiSalesInvoiceService
from app.ui.screens import saudi_sales_invoice_page as sales_page
from app.ui.screens.saudi_sales_invoice_page import (
    COL_CODE,
    COL_NAME,
    SaudiSalesInvoicePage,
)


class FakeRepo:
    def __init__(self):
        self.companies = {
            10: {"id": 10, "name_ar": "شركة أ", "name_en": "A",
                 "commercial_registration": "1010101010",
                 "vat_number": "311111111111113", "address_ar": "الرياض"},
        }
        self.customers = {
            100: {"customer_id": 100, "customer_name": "عميل", "phone_number": "05",
                  "vat_number": "300000000000003", "cr": "7070707070", "address": "العنوان"},
        }
        self.products = {
            1: {"id": 1, "item_code": 1001, "item_name": "صنف تجريبي", "price": Decimal("50")},
            2: {"id": 2, "item_code": 1002, "item_name": "صنف ثانٍ", "price": Decimal("30")},
        }
        self.loaded = None

    def list_companies(self, limit=100):
        return list(self.companies.values())

    def list_customers(self, limit=100):
        return list(self.customers.values())

    def list_products(self, limit=100):
        return list(self.products.values())

    def get_company(self, i):
        return self.companies.get(int(i))

    def get_customer(self, i):
        return self.customers.get(int(i))

    def get_product(self, i):
        return self.products.get(int(i))

    def search_companies(self, kw, limit=100):
        return self.list_companies()

    def search_customers(self, kw, limit=100):
        return self.list_customers()

    def search_products(self, kw, limit=100):
        return self.list_products()

    def invoice_number_exists(self, *a, **k):
        return False

    def load_invoice(self, i):
        return self.loaded

    def search_invoices(self, *a, **k):
        return []

    def has_zatca_material(self, i):
        return False


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def page(qapp):
    service = SaudiSalesInvoiceService(repository=FakeRepo())
    return SaudiSalesInvoicePage(service=service)


REQUIRED_CONTROLS = [
    "search_button", "new_button", "duplicate_button", "save_button", "edit_button", "update_button", "approve_button",
    "delete_button", "delete_all_button", "cancel_button", "back_button",
    "invoice_number_input", "issue_datetime_input", "seller_combo", "customer_combo",
    "payment_combo", "seller_vat_input", "customer_vat_input", "doc_status_input",
    "lines_table", "items_total_value", "subtotal_value", "vat_value", "total_value",
    "item_combo", "qty_input", "price_input", "add_line_button", "remove_line_button",
    "notes_input", "status_badge", "phase2_toggle",
]


def test_screen_is_rtl(page):
    assert page.layoutDirection() == Qt.RightToLeft


def test_required_controls_exist(page):
    for name in REQUIRED_CONTROLS:
        assert hasattr(page, name), f"missing control: {name}"
    # object names on the toolbar buttons match the spec
    assert page.new_button.objectName() == "new_button"
    assert page.approve_button.objectName() == "approve_button"


def test_search_icon_and_f1_open_the_same_dialog(page, monkeypatch):
    """The icon is a twin of F1: both must reach SaudiInvoiceSearchDialog."""
    opened = []

    class FakeSearchDialog:
        selected_id = None

        def __init__(self, service, parent=None):
            opened.append(service)

        def exec(self):
            return 0  # user closed it without picking

    monkeypatch.setattr(sales_page, "SaudiInvoiceSearchDialog", FakeSearchDialog)
    page.search_button.click()
    assert len(opened) == 1, "search icon did not open the invoice search dialog"
    page.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F1, Qt.NoModifier))
    assert len(opened) == 2, "F1 did not open the invoice search dialog"
    assert opened[0] is opened[1] is page.service


def test_search_icon_stays_live_in_every_mode(page):
    # The icon is F1's twin, so it must never be gated by the mode the way the
    # save/edit/delete buttons are.
    for mode in ("view", "new", "edit"):
        page._set_mode(mode)
        assert page.search_button.isEnabled() is True, mode


def test_open_state_is_locked_until_new(page):
    # On open the screen is idle: fields locked, only "New" is actionable.
    assert page.invoice_number_input.isReadOnly() is True
    assert page.new_button.isEnabled() is True
    assert page.save_button.isEnabled() is False
    # Pressing "New" unlocks a blank, editable invoice number, enables "Save"
    # and disables "New".
    page.on_new()
    assert page.invoice_number_input.text() == ""
    assert page.invoice_number_input.isReadOnly() is False
    assert page.new_button.isEnabled() is False
    assert page.save_button.isEnabled() is True


def test_no_auto_invoice_number_generated(page):
    # Opening, then New again, must never fabricate a number.
    page.on_new()
    assert page.invoice_number_input.text() == ""


def test_ready_mode_reopens_new_and_relocks(page):
    # Simulate the post-save return to idle: "New" re-opens, "Save" closes,
    # and every field is locked again.
    page.on_new()
    assert page.new_button.isEnabled() is False
    page.enter_ready_mode()
    assert page.new_button.isEnabled() is True
    assert page.save_button.isEnabled() is False
    assert page.invoice_number_input.isReadOnly() is True


def test_selecting_invoice_locks_new_and_keeps_save(page):
    inv = _approved_invoice()
    inv["header"]["document_status"] = "draft"
    inv["header"]["id"] = 88
    page.service.repository.loaded = inv
    page.load_invoice(88)
    # Selecting an invoice disables "New" and keeps "Save" available for edits,
    # while fields stay locked until "Edit".
    assert page.new_button.isEnabled() is False
    assert page.save_button.isEnabled() is True
    assert page.invoice_number_input.isReadOnly() is True
    page.on_edit()
    assert page.invoice_number_input.isReadOnly() is False
    assert page.save_button.isEnabled() is True


def _draft_invoice(invoice_id: int) -> dict:
    inv = _approved_invoice()
    inv["header"]["document_status"] = "draft"
    inv["header"]["id"] = invoice_id
    return inv


def test_save_new_stays_on_the_saved_invoice(page, monkeypatch):
    # Saving must NOT clear the form: the invoice just entered stays on screen so
    # it can be previewed or printed straight away, read-only until "Edit".
    monkeypatch.setattr(
        "app.ui.screens.saudi_sales_invoice_page.QMessageBox.information",
        lambda *a, **k: None,
    )
    saved = _draft_invoice(501)
    page.service.repository.loaded = saved
    monkeypatch.setattr(page.service, "create_draft", lambda *a, **k: saved)
    page.on_new()
    assert page.save_button.isEnabled() is True
    page.on_save()
    assert page._current is not None
    assert page._current["header"]["id"] == 501
    assert page.invoice_number_input.text() == saved["header"]["invoice_number"]
    # Loaded but locked, and the toolbar is back to its default shape: "New"
    # starts the next invoice, "Save"/"Edit" are closed until one is searched up.
    assert page.invoice_number_input.isReadOnly() is True
    assert page.new_button.isEnabled() is True
    assert page.save_button.isEnabled() is False
    assert page.edit_button.isEnabled() is False
    assert page._dirty is False


def test_save_new_without_an_id_falls_back_to_the_idle_state(page, monkeypatch):
    # Nothing to stay on: never leave a stale form claiming to be saved.
    monkeypatch.setattr(
        "app.ui.screens.saudi_sales_invoice_page.QMessageBox.information",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(page.service, "create_draft", lambda *a, **k: None)
    page.on_new()
    page.on_save()
    assert page._current is None
    assert page.new_button.isEnabled() is True


def test_save_after_edit_stays_on_the_invoice_and_refreshes_row_version(page, monkeypatch):
    # Re-saving an edited draft keeps it loaded too — and re-reads it, without
    # which a second Edit → Update would fail the optimistic-concurrency check.
    monkeypatch.setattr(
        "app.ui.screens.saudi_sales_invoice_page.QMessageBox.information",
        lambda *a, **k: None,
    )
    inv = _draft_invoice(88)
    inv["header"]["row_version"] = 1
    page.service.repository.loaded = inv
    page.load_invoice(88)
    page.on_edit()
    assert page.invoice_number_input.isReadOnly() is False

    bumped = _draft_invoice(88)
    bumped["header"]["row_version"] = 2
    monkeypatch.setattr(page.service, "update_draft", lambda *a, **k: None)
    page.service.repository.loaded = bumped  # what the re-read returns
    page.on_save()

    assert page._current is not None
    assert page._current["header"]["id"] == 88
    assert page._current["header"]["row_version"] == 2
    assert page.invoice_number_input.isReadOnly() is True
    # Saving a searched-up invoice also returns the toolbar to its default shape.
    assert page.new_button.isEnabled() is True
    assert page.save_button.isEnabled() is False
    assert page.edit_button.isEnabled() is False


def test_save_search_save_cycles_back_to_the_default_toolbar(page, monkeypatch):
    # The full round trip the user walks: New -> Save -> search -> Save again,
    # and the toolbar must land back on its default shape each time it is saved.
    monkeypatch.setattr(
        "app.ui.screens.saudi_sales_invoice_page.QMessageBox.information",
        lambda *a, **k: None,
    )
    saved = _draft_invoice(77)
    page.service.repository.loaded = saved
    monkeypatch.setattr(page.service, "create_draft", lambda *a, **k: saved)
    page.on_new()
    page.on_save()
    assert (page.new_button.isEnabled(), page.save_button.isEnabled(),
            page.edit_button.isEnabled()) == (True, False, False)

    # Searching the same invoice back up is a different state to having saved it:
    # "New" closes, "Edit"/"Save" open.
    page.load_invoice(77)
    assert (page.new_button.isEnabled(), page.save_button.isEnabled(),
            page.edit_button.isEnabled()) == (False, True, True)

    monkeypatch.setattr(page.service, "update_draft", lambda *a, **k: None)
    page.on_save()
    assert (page.new_button.isEnabled(), page.save_button.isEnabled(),
            page.edit_button.isEnabled()) == (True, False, False)


def test_cancel_is_always_live(page):
    # "Cancel" must never be greyed out — it is the way out of every state.
    assert page.cancel_button.isEnabled() is True
    page.on_new()
    assert page.cancel_button.isEnabled() is True
    page.service.repository.loaded = _draft_invoice(88)
    page.load_invoice(88)
    assert page.cancel_button.isEnabled() is True
    page.on_edit()
    assert page.cancel_button.isEnabled() is True


def test_cancel_clears_a_searched_invoice_back_to_idle(page):
    # Nothing was typed, so there is nothing to revert to: "Cancel" puts the
    # invoice down and hands the screen back in its default state.
    page.service.repository.loaded = _draft_invoice(88)
    page.load_invoice(88)
    page.on_cancel()
    assert page._current is None
    assert page.invoice_number_input.text() == ""
    assert (page.new_button.isEnabled(), page.save_button.isEnabled(),
            page.edit_button.isEnabled()) == (True, False, False)


def test_cancel_during_edit_reverts_to_the_stored_invoice(page, monkeypatch):
    # Mid-edit, "Cancel" throws away the typing and brings the invoice back as
    # stored — still selected, so "Edit"/"Save" stay open.
    monkeypatch.setattr(
        "app.ui.screens.saudi_sales_invoice_page.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )
    page.service.repository.loaded = _draft_invoice(88)
    page.load_invoice(88)
    page.on_edit()
    page.invoice_number_input.setText("TYPED-OVER")
    page.on_cancel()
    assert page.invoice_number_input.text() == _draft_invoice(88)["header"]["invoice_number"]
    assert (page.new_button.isEnabled(), page.save_button.isEnabled(),
            page.edit_button.isEnabled()) == (False, True, True)


def test_closing_screen_resets_to_default_state(page):
    # The main window caches and only hides this screen, so closing must reset
    # it — reopening should never show the previously loaded invoice.
    inv = _approved_invoice()
    inv["header"]["document_status"] = "draft"
    inv["header"]["id"] = 88
    page.service.repository.loaded = inv
    page.load_invoice(88)
    assert page._current is not None
    page.close()
    assert page._current is None
    assert page.new_button.isEnabled() is True
    assert page.save_button.isEnabled() is False
    assert page.edit_button.isEnabled() is False


def test_issue_datetime_defaults_to_now(page):
    dt = page.issue_datetime_input.dateTime().toPython()
    assert isinstance(dt, datetime.datetime)
    assert dt.year == datetime.datetime.now().year


def test_approve_disabled_with_tooltip(page):
    assert page.approve_button.isEnabled() is False
    assert "الاعتماد" in page.approve_button.toolTip()


def test_payment_combo_stores_codes_not_labels(page):
    codes = {page.payment_combo.itemData(i) for i in range(page.payment_combo.count())}
    assert {"cash", "credit"} <= codes


def test_selecting_seller_fills_seller_vat(page):
    # index 0 is the "choose" placeholder; index 1 is company 10.
    page.seller_combo.setCurrentIndex(1)
    assert page.seller_vat_input.text() == "311111111111113"


def test_selecting_customer_fills_customer_vat(page):
    page.customer_combo.setCurrentIndex(1)
    assert page.customer_vat_input.text() == "300000000000003"


def test_ensure_combo_record_matches_by_id_without_duplicating(page):
    combo = page.seller_combo
    before = combo.count()  # placeholder + company 10
    # A snapshot dict with different keys than the loaded row must NOT append.
    snap = {"id": 10, "name_ar": "شركة أ", "name_en": "A", "vat_number": "311111111111113"}
    page._ensure_combo_record(combo, snap, "شركة أ", "id")
    assert combo.count() == before
    assert combo.currentData().get("id") == 10
    page._ensure_combo_record(combo, snap, "شركة أ", "id")  # repeated -> still no dup
    assert combo.count() == before


def _labels(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_master_data_registered_after_the_screen_opened_reaches_the_dropdowns(page):
    # The screen is a long-lived window, so a customer/company/product registered
    # elsewhere while it stayed open must still reach its combos. Before the
    # refresh, only the search dialogs (which query live) knew about them.
    repo = page.service.repository
    repo.companies[11] = {"id": 11, "name_ar": "شركة جديدة", "name_en": "New",
                          "commercial_registration": "2020202020",
                          "vat_number": "311111111111114", "address_ar": "جدة"}
    repo.customers[101] = {"customer_id": 101, "customer_name": "عميل جديد",
                           "phone_number": "055", "vat_number": "300000000000004",
                           "cr": "8080808080", "address": "عنوان"}
    repo.products[3] = {"id": 3, "item_code": 1003, "item_name": "صنف جديد",
                        "price": Decimal("20")}

    page.refresh_master_data()

    assert "شركة جديدة" in _labels(page.seller_combo)
    assert any("عميل جديد" in text for text in _labels(page.customer_combo))
    assert "1003 - صنف جديد" in _labels(page.item_combo)


def test_refreshing_keeps_the_invoice_being_written_intact(page):
    # A refresh fires whenever the window regains focus — mid-invoice included.
    # It must not throw away the picks, nor dirty an invoice nobody edited.
    page.on_new()
    page.seller_combo.setCurrentIndex(1)
    page.customer_combo.setCurrentIndex(1)
    page.item_combo.setCurrentIndex(1)
    page._dirty = False

    page.service.repository.customers[101] = {
        "customer_id": 101, "customer_name": "عميل جديد", "phone_number": "055",
        "vat_number": "300000000000004", "cr": "8080808080", "address": "عنوان",
    }
    page.refresh_master_data()

    assert page.seller_combo.currentData().get("id") == 10
    assert page.customer_combo.currentData().get("customer_id") == 100
    assert page._selected_product().get("id") == 1
    assert page.item_code_input.text() == "1001"
    assert page.seller_vat_input.text() == "311111111111113"
    assert page.customer_vat_input.text() == "300000000000003"
    assert page._dirty is False


def test_refreshing_keeps_an_unregistered_item_typed_into_the_card(page):
    # The item combo is editable and its text *is* the free item's name; a refill
    # that cleared it would delete what the user typed.
    page.on_new()
    page.item_combo.setEditText("صنف حر لم يُسجّل")
    page.refresh_master_data()

    assert page.item_combo.currentText() == "صنف حر لم يُسجّل"
    assert page._selected_product() is None


def test_a_deleted_master_record_still_being_used_survives_the_refresh(page):
    # Deleting the customer elsewhere must not silently unpick it from an invoice
    # already naming it.
    page.on_new()
    page.customer_combo.setCurrentIndex(1)
    del page.service.repository.customers[100]

    page.refresh_master_data()

    assert page.customer_combo.currentData().get("customer_id") == 100


def test_regaining_focus_refreshes_the_dropdowns(page, monkeypatch):
    monkeypatch.setattr(page, "isActiveWindow", lambda: True)
    page.service.repository.products[3] = {"id": 3, "item_code": 1003,
                                           "item_name": "صنف جديد", "price": Decimal("20")}

    page.changeEvent(QEvent(QEvent.ActivationChange))

    assert "1003 - صنف جديد" in _labels(page.item_combo)


def test_losing_focus_does_not_refresh(page, monkeypatch):
    monkeypatch.setattr(page, "isActiveWindow", lambda: False)
    page.service.repository.products[3] = {"id": 3, "item_code": 1003,
                                           "item_name": "صنف جديد", "price": Decimal("20")}

    page.changeEvent(QEvent(QEvent.ActivationChange))

    assert "1003 - صنف جديد" not in _labels(page.item_combo)


def test_preview_data_autofills_cr_and_address_from_master(page):
    page.seller_combo.setCurrentIndex(1)
    page.customer_combo.setCurrentIndex(1)
    data = page._collect_preview_data()
    # CR + address are pulled from the company / customer master record.
    assert data["seller"]["cr"] == "1010101010"
    assert data["seller"]["address"] == "الرياض"
    assert data["seller"]["vat"] == "311111111111113"
    assert data["customer"]["cr"] == "7070707070"
    assert data["customer"]["address"] == "العنوان"
    assert data["customer"]["vat"] == "300000000000003"


def test_add_line_defaults_vat_15_and_decimal_totals(page):
    page.item_combo.setCurrentIndex(1)  # product 1
    page.qty_input.setText("2")
    page.price_input.setText("50")
    page._on_add_line()
    assert page.lines_table.rowCount() == 1
    # VAT rate column shows 15%
    assert "15" in page.lines_table.item(0, 5).text()
    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert meta["vat_rate"] == Decimal("15.00")
    assert isinstance(meta["before"], Decimal)
    # 2 x 50 = 100 before VAT; +15% = 115 total
    assert page.subtotal_value.text() == "100.00"
    assert page.vat_value.text() == "15.00"
    assert page.total_value.text() == "115.00"


def test_item_lookup_adds_every_picked_product(page, monkeypatch):
    picked = [
        {"id": 1, "item_code": 1001, "item_name": "صنف تجريبي", "price": Decimal("50")},
        {"id": 2, "item_code": 1002, "item_name": "صنف ثانٍ", "price": Decimal("30")},
    ]

    class FakeDialog:
        def __init__(self, *a, **k):
            self.kwargs = k
            self.selected_rows = picked
            self.selected = picked[0]
            FakeDialog.instance = self

        def exec(self):
            return 1

    monkeypatch.setattr(sales_page, "EntityPickerDialog", FakeDialog)
    page.on_new()
    page._search_item()

    assert FakeDialog.instance.kwargs["multi_select"] is True
    # Both products land on the invoice at quantity 1 and their list price.
    assert page.lines_table.rowCount() == 2
    metas = [page.lines_table.item(r, 0).data(Qt.UserRole) for r in range(2)]
    assert [m["product_id"] for m in metas] == [1, 2]
    assert [m["qty"] for m in metas] == [Decimal("1"), Decimal("1")]
    assert [m["price"] for m in metas] == [Decimal("50"), Decimal("30")]
    # 50 + 30 = 80 before VAT; +15% = 92
    assert page.subtotal_value.text() == "80.00"
    assert page.total_value.text() == "92.00"


def test_quantity_editable_after_multi_pick(page, monkeypatch):
    picked = [{"id": 2, "item_code": 1002, "item_name": "صنف ثانٍ", "price": Decimal("30")}]

    class FakeDialog:
        def __init__(self, *a, **k):
            self.selected_rows = picked
            self.selected = picked[0]

        def exec(self):
            return 1

    monkeypatch.setattr(sales_page, "EntityPickerDialog", FakeDialog)
    page.on_new()
    page._search_item()
    # Editing the qty cell recomputes the row and the totals.
    page.lines_table.item(0, 2).setText("3")
    page._on_cell_changed(0, 2)
    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert meta["qty"] == Decimal("3")
    assert page.subtotal_value.text() == "90.00"
    assert page.total_value.text() == "103.50"


def test_card_adds_unregistered_item_typed_by_hand(page):
    page.on_new()
    # Nothing picked from the dropdown: the typed text is the item's name.
    page.item_combo.setCurrentIndex(0)
    page.item_combo.setEditText("صنف غير مسجّل")
    page.item_code_input.setText("TMP-9")
    page.qty_input.setText("3")
    page.price_input.setText("10")
    page._on_add_line()

    assert page.lines_table.rowCount() == 1
    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert meta["product_id"] is None
    assert meta["product_code"] == "TMP-9"
    assert meta["product_name"] == "صنف غير مسجّل"
    assert page.total_value.text() == "34.50"
    # The line reaches the service carrying its typed identity.
    line = page._collect_lines()[0]
    assert (line["product_id"], line["product_code"], line["product_name"]) == (
        None, "TMP-9", "صنف غير مسجّل")
    # Nothing was added to the products dropdown.
    assert page.item_combo.findText("صنف غير مسجّل") == -1


def test_card_adds_unregistered_item_without_a_code(page, monkeypatch):
    # The code is optional (user's call): a one-off item is billed by name alone.
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    page.on_new()
    page.item_combo.setEditText("بلا رقم")
    page.item_code_input.clear()
    page.qty_input.setText("2")
    page.price_input.setText("50")
    page._on_add_line()

    assert warned == [], f"adding a codeless item must not warn: {warned}"
    assert page.lines_table.rowCount() == 1
    meta = page.lines_table.item(0, COL_CODE).data(Qt.UserRole)
    assert meta["product_code"] == ""
    assert meta["product_name"] == "بلا رقم"
    assert page.total_value.text() == "115.00"


def test_card_still_needs_a_name(page, monkeypatch):
    # The name is what identifies the line once the code is gone, so it stays
    # required — otherwise the invoice would carry a row naming nothing.
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    page.on_new()
    page.item_combo.setEditText("")
    page.item_code_input.setText("K1")
    page._on_add_line()
    assert page.lines_table.rowCount() == 0
    assert warned and "اسم صنف" in warned[0]


def test_typing_over_a_picked_product_bills_the_typed_item(page):
    # An editable QComboBox keeps reporting the picked item after its text is
    # typed over; the screen must follow the text, not the stale selection.
    page.on_new()
    page.item_combo.setCurrentIndex(1)          # product 1 ("صنف تجريبي", 50)
    page.item_combo.setEditText("صنف كتبته بنفسي")
    page.item_code_input.setText("Z-9")
    page.qty_input.setText("1")
    page.price_input.setText("7")
    page._on_add_line()

    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert meta["product_id"] is None           # not product 1
    assert meta["product_name"] == "صنف كتبته بنفسي"
    assert meta["price"] == Decimal("7")


def test_picked_products_code_does_not_leak_onto_a_typed_item(page):
    page.on_new()
    page.item_combo.setCurrentIndex(1)
    assert page.item_code_input.text() == "1001"   # auto-filled from the product
    page.item_combo.setEditText("صنف حر")          # now it is not that product
    assert page.item_code_input.text() == ""       # ...so its code must not stay


def test_new_line_button_appends_an_editable_empty_row(page):
    page.on_new()
    page._on_new_free_line()
    assert page.lines_table.rowCount() == 1
    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert meta["product_id"] is None
    # Code and name are typed straight into the row.
    for col in (COL_CODE, COL_NAME):
        assert page.lines_table.item(0, col).flags() & Qt.ItemIsEditable
    page.lines_table.item(0, COL_CODE).setText("H-1")
    page._on_cell_changed(0, COL_CODE)
    page.lines_table.item(0, COL_NAME).setText("صنف مكتوب في الجدول")
    page._on_cell_changed(0, COL_NAME)
    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert (meta["product_code"], meta["product_name"]) == ("H-1", "صنف مكتوب في الجدول")


def test_registered_line_identity_is_not_editable(page):
    page.on_new()
    page.item_combo.setCurrentIndex(1)  # product 1
    page.qty_input.setText("1")
    page.price_input.setText("50")
    page._on_add_line()
    for col in (COL_CODE, COL_NAME):
        assert not (page.lines_table.item(0, col).flags() & Qt.ItemIsEditable)


def test_two_unregistered_items_never_merge(page):
    page.on_new()
    for code, name in (("A-1", "صنف أول"), ("B-2", "صنف ثانٍ")):
        page.item_combo.setEditText(name)
        page.item_code_input.setText(code)
        page.qty_input.setText("1")
        page.price_input.setText("10")
        page._on_add_line()
    # Same price and VAT, but they are different items: two rows, not one.
    assert page.lines_table.rowCount() == 2


def test_remove_line(page):
    page.item_combo.setCurrentIndex(1)
    page.qty_input.setText("1")
    page.price_input.setText("10")
    page._on_add_line()
    assert page.lines_table.rowCount() == 1
    page.lines_table.setCurrentCell(0, 0)
    page._on_remove_line()
    assert page.lines_table.rowCount() == 0
    assert page.total_value.text() == "0.00"


def _approved_invoice():
    return {
        "header": {
            "id": 77,
            "invoice_number": "A-77",
            "issue_datetime": datetime.datetime(2026, 7, 1, 9, 0),
            "seller_company_id": 10,
            "seller_name_ar_snapshot": "شركة أ",
            "seller_name_en_snapshot": "A",
            "seller_vat_number_snapshot": "311111111111113",
            "customer_id": 100,
            "customer_name_snapshot": "عميل",
            "customer_vat_number_snapshot": "300000000000003",
            "customer_address_snapshot": "العنوان",
            "payment_type": "cash",
            "notes": "معتمدة",
            "document_status": "approved",
            "row_version": 3,
        },
        "lines": [],
        "zatca": None,
    }


def _free_line_row():
    return {
        "id": 5, "line_number": 1,
        "product_id": None,                      # unregistered: stored code+name only
        "product_code_snapshot": "TMP-9",
        "product_name_snapshot": "صنف غير مسجّل",
        "unit_code": "PCE", "quantity": Decimal("3"), "unit_price": Decimal("10"),
        "vat_rate": Decimal("15.00"),
    }


def test_saved_unregistered_line_reloads_and_stays_editable(page):
    inv = _approved_invoice()
    inv["header"]["document_status"] = "draft"
    inv["header"]["id"] = 89
    inv["lines"] = [_free_line_row()]
    page.service.repository.loaded = inv
    page.load_invoice(89)

    assert page.lines_table.rowCount() == 1
    meta = page.lines_table.item(0, 0).data(Qt.UserRole)
    assert meta["product_id"] is None
    assert (meta["product_code"], meta["product_name"]) == ("TMP-9", "صنف غير مسجّل")
    assert page.total_value.text() == "34.50"
    # Read-only until Edit, then the typed identity is editable again.
    assert not (page.lines_table.item(0, COL_NAME).flags() & Qt.ItemIsEditable)
    page.on_edit()
    assert page.lines_table.item(0, COL_NAME).flags() & Qt.ItemIsEditable


def test_phase2_section_never_opens_by_itself(page):
    # _stay_on_saved is the post-save path: it re-reads the invoice, now carrying
    # the Phase-2 data generated on save. That must not open the section.
    inv = _approved_invoice()
    inv["header"]["id"] = 90
    inv["zatca"] = {"uuid": "b7f4e1c2-0000-4000-8000-000000000001",
                    "invoice_counter_value": 1, "integration_status": "generated"}
    page.service.repository.loaded = inv
    page._stay_on_saved(90)

    assert page._phase2_expanded is False
    assert page.phase2_body.isVisible() is False
    assert page.phase2_toggle.text().startswith("▸")
    # The fields are still populated underneath, ready for when it is opened.
    assert page._zatca_fields["uuid"].text() == "b7f4e1c2-0000-4000-8000-000000000001"
    # Clicking is what opens it, and clicking again closes it.
    page._toggle_phase2()
    assert page._phase2_expanded is True
    assert page.phase2_toggle.text().startswith("▾")
    page._toggle_phase2()
    assert page._phase2_expanded is False


def _resize(page, w: int, h: int) -> None:
    page.resize(w, h)
    page.show()
    QApplication.processEvents()


def _meta_cells(page) -> list[tuple[int, int]]:
    """(row, column) of the user / date / time labels in the header meta grid."""
    cells = []
    for label in (page.user_label, page.date_label, page.time_label):
        row, col, _, _ = page.meta_grid.getItemPosition(page.meta_grid.indexOf(label))
        cells.append((row, col))
    return cells


def test_items_table_grows_into_the_space_a_taller_screen_gives(page):
    """The whole point of dropping the trailing stretch: spare height belongs to
    the invoice lines, not to empty space under the cards."""
    _resize(page, 1440, 800)
    short = page.lines_table.height()
    _resize(page, 1440, 1000)
    tall = page.lines_table.height()
    assert tall > short + 150, (
        f"table did not absorb the extra 200px (short={short}, tall={tall}) — "
        "something else is eating the slack again"
    )


def test_screen_compacts_itself_on_a_short_display(page):
    # Roomy above the threshold: comfortable rows, subtitle and stacked meta.
    _resize(page, 1440, 900)
    assert page._compact is False
    assert page.lines_table.verticalHeader().defaultSectionSize() == 44
    assert page.subtitle_label.isVisible() is True
    assert _meta_cells(page) == [(0, 0), (1, 0), (2, 0)]

    # Below it the chrome gives way to the lines.
    _resize(page, 1366, 680)
    assert page._compact is True
    assert page.lines_table.verticalHeader().defaultSectionSize() == 32
    assert page.subtitle_label.isVisible() is False
    # user/date/time move from three stacked rows onto one. (rowCount() is no
    # probe here: QGridLayout never shrinks its allocated rows.)
    assert _meta_cells(page) == [(0, 0), (0, 1), (0, 2)]


def test_density_flip_resizes_the_rows_already_on_screen(page):
    # setDefaultSectionSize only affects future rows, so a loaded invoice would
    # keep its tall rows and defeat the whole point of compacting.
    _resize(page, 1440, 900)
    page.service.repository.loaded = _invoice_with_lines(88)
    page.load_invoice(88)
    assert page.lines_table.rowHeight(0) == 44

    _resize(page, 1366, 680)
    assert page.lines_table.rowHeight(0) == 32


def test_page_never_demands_more_room_than_its_content_needs(page):
    # The old 1360x700 floor was 148px wider than the content's own minimum,
    # which kept the screen off smaller laptops for nothing.
    _resize(page, 1440, 900)
    body = page.findChildren(QScrollArea)[0].widget()
    assert page.minimumWidth() <= body.minimumSizeHint().width()
    assert page.minimumWidth() <= 1212
    assert page.minimumHeight() <= 560


def test_nothing_is_clipped_without_a_way_to_reach_it(page):
    # Narrower than the content floor: a horizontal bar must appear rather than
    # the left edge (the toolbar's last buttons) vanishing silently.
    scroll = page.findChildren(QScrollArea)[0]
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded


def _invoice_with_lines(invoice_id: int = 88) -> dict:
    inv = _draft_invoice(invoice_id)
    inv["lines"] = [_free_line_row()]
    return inv


def test_duplicate_copies_everything_except_the_invoice_number(page):
    page.service.repository.loaded = _invoice_with_lines(88)
    page.load_invoice(88)
    page.on_duplicate()

    # The number is the one thing that must be re-typed.
    assert page.invoice_number_input.text() == ""
    # Everything else carried over: seller, customer, VAT snapshots, payment,
    # notes and the lines with their quantities/prices.
    assert page.seller_combo.currentData()["id"] == 10
    assert page.customer_combo.currentData()["customer_id"] == 100
    assert page.seller_vat_input.text() == "311111111111113"
    assert page.customer_vat_input.text() == "300000000000003"
    assert page.payment_combo.currentData() == "cash"
    assert page.notes_input.toPlainText() == "معتمدة"
    assert page.lines_table.rowCount() == 1
    meta = page.lines_table.item(0, COL_CODE).data(Qt.UserRole)
    assert (meta["product_code"], meta["qty"], meta["price"]) == (
        "TMP-9", Decimal("3"), Decimal("10")
    )
    assert page.total_value.text() == "34.50"


def test_duplicate_opens_an_editable_new_draft_not_an_edit_of_the_source(page):
    page.service.repository.loaded = _invoice_with_lines(88)
    page.load_invoice(88)
    page.on_duplicate()

    # Nothing is loaded any more, so Save creates a second invoice rather than
    # overwriting the one it was copied from.
    assert page._current is None
    assert page._mode == "new"
    assert page._current_status == "draft"
    assert page.invoice_number_input.isReadOnly() is False
    assert page.save_button.isEnabled() is True
    assert page.update_button.isEnabled() is False
    # The copy is unsaved work, so leaving it must prompt.
    assert page._dirty is True
    # The source's line ids belong to the source, not the copy.
    assert page.lines_table.item(0, COL_CODE).data(Qt.UserRole)["line_id"] is None


def test_duplicate_of_an_approved_invoice_is_a_draft_and_drops_phase2(page):
    inv = _approved_invoice()
    inv["zatca"] = {"uuid": "b7f4e1c2-0000-4000-8000-000000000001",
                    "invoice_counter_value": 7, "integration_status": "generated"}
    page.service.repository.loaded = inv
    page.load_invoice(77)
    assert page._zatca_fields["uuid"].text() != ""  # the source carries Phase-2 data

    page.on_duplicate()
    # A copy of an approved invoice is a new draft — never born approved/locked.
    assert page._current_status == "draft"
    assert page.invoice_number_input.isReadOnly() is False
    # UUID/ICV identify one single invoice and are generated at save. Copying
    # them would give two invoices the same identity.
    assert page._zatca_fields["uuid"].text() == ""
    assert page._zatca_fields["invoice_counter_value"].text() == ""


def test_duplicate_issues_the_copy_now_not_at_the_source_datetime(page):
    page.service.repository.loaded = _invoice_with_lines(88)  # issued 2026-07-01
    page.load_invoice(88)
    assert page.issue_datetime_input.dateTime().toPython().year == 2026
    assert page.issue_datetime_input.dateTime().toPython().month == 7
    assert page.issue_datetime_input.dateTime().toPython().day == 1

    page.on_duplicate()
    stamped = page.issue_datetime_input.dateTime().toPython()
    assert abs((datetime.datetime.now() - stamped).total_seconds()) < 60


def test_duplicate_from_idle_copies_the_newest_invoice(page, monkeypatch):
    # Nothing on screen: the button falls back to the most recent invoice, which
    # is the first row search_invoices returns (it orders by id DESC).
    asked = []

    def fake_search(keyword="", status=None, limit=300):
        asked.append((keyword, status, limit))
        return [{"id": 88}, {"id": 12}]

    monkeypatch.setattr(page.service, "search_invoices", fake_search)
    page.service.repository.loaded = _invoice_with_lines(88)
    assert page._current is None
    page.on_duplicate()

    assert asked == [("", None, 1)]
    assert page._mode == "new"
    assert page.seller_combo.currentData()["id"] == 10


def test_duplicate_without_any_previous_invoice_says_so(page, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(page.service, "search_invoices", lambda *a, **k: [])
    page.on_duplicate()

    assert warned == ["لا توجد فاتورة سابقة لتكرارها."]
    assert page._mode == "view"  # still idle, nothing half-built on screen


def test_duplicate_then_save_creates_a_second_invoice_with_the_copied_data(page, monkeypatch):
    """The whole point of the button: copy, retype the number, save — and the
    source invoice is left untouched."""
    monkeypatch.setattr(
        "app.ui.screens.saudi_sales_invoice_page.QMessageBox.information",
        lambda *a, **k: None,
    )
    sent = {}

    def fake_create(form, *, user_id=None):
        sent.update(form)
        return _draft_invoice(502)

    def fail_update(*a, **k):
        raise AssertionError("duplicate must create a new invoice, not update the source")

    monkeypatch.setattr(page.service, "create_draft", fake_create)
    monkeypatch.setattr(page.service, "update_draft", fail_update)

    page.service.repository.loaded = _invoice_with_lines(88)
    page.load_invoice(88)
    page.on_duplicate()
    page.invoice_number_input.setText("A-89")  # the only field the user retypes
    page.on_save()

    assert sent["invoice_number"] == "A-89"
    assert sent["seller_company_id"] == 10
    assert sent["customer_id"] == 100
    assert sent["payment_type"] == "cash"
    assert sent["notes"] == "معتمدة"
    assert len(sent["lines"]) == 1
    assert sent["lines"][0]["quantity"] == Decimal("3")
    assert sent["lines"][0]["unit_price"] == Decimal("10")


def test_duplicate_is_off_while_typing_or_editing(page):
    # Live at rest so a saved/searched/idle screen can be copied...
    assert page.duplicate_button.isEnabled() is True
    # ...but never while entry is in progress, where it would wipe the form.
    page.on_new()
    assert page.duplicate_button.isEnabled() is False
    page.service.repository.loaded = _draft_invoice(88)
    page.load_invoice(88)
    assert page.duplicate_button.isEnabled() is True
    page.on_edit()
    assert page.duplicate_button.isEnabled() is False


def test_approved_invoice_loads_locked(page):
    page.service.repository.loaded = _approved_invoice()
    page.load_invoice(77)
    assert page._current_status == "approved"
    assert page.status_badge.text() == "معتمدة"
    # Locked: header fields read-only, edit/update/delete disabled.
    assert page.invoice_number_input.isReadOnly() is True
    assert page.edit_button.isEnabled() is False
    assert page.update_button.isEnabled() is False
    assert page.delete_button.isEnabled() is False


def test_draft_invoice_can_be_edited_after_load(page):
    inv = _approved_invoice()
    inv["header"]["document_status"] = "draft"
    inv["header"]["id"] = 88
    page.service.repository.loaded = inv
    page.load_invoice(88)
    assert page._current_status == "draft"
    assert page.edit_button.isEnabled() is True
    assert page.delete_button.isEnabled() is True
    # Entering edit mode unlocks the header.
    page.on_edit()
    assert page.invoice_number_input.isReadOnly() is False
    assert page.update_button.isEnabled() is True


# ----------------------------------------------------------------------
# Receipt-voucher serial: invoice number + 123 (never the same number)
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("invoice_number", "expected_serial"),
    [
        ("123", "246"),        # the confirmed example
        ("5", "128"),          # the confirmed example
        ("0", "123"),
        ("A-77", "A-200"),     # prefix kept, trailing run bumped
        ("005", "128"),        # zero-padding width preserved
        ("999", "1122"),       # width grows when it must
        ("2026/1", "2026/124"),  # only the LAST digit run moves
        ("", ""),              # nothing to derive from
        ("INV", "INV"),        # no digits -> returned unchanged
    ],
)
def test_voucher_serial_offsets_only_the_trailing_digits(invoice_number, expected_serial):
    assert (
        sales_page.voucher_serial_from_invoice_number(invoice_number)
        == expected_serial
    )
    # The offset is the value the user confirmed, not a magic literal in the test.
    assert sales_page.VOUCHER_NUMBER_OFFSET == 123


def test_print_voucher_data_offsets_serial_but_purpose_keeps_the_invoice_number(page):
    # A real invoice on screen (number "A-77"): the voucher's own serial is
    # offset (+123 on the "77" -> "A-200"), while «وذلك قيمة» still names the
    # actual invoice it is a receipt for ("A-77").
    page.service.repository.loaded = _invoice_with_lines(88)
    page.load_invoice(88)

    data = page._collect_voucher_data()

    assert page.invoice_number_input.text() == "A-77"
    assert data["number"] == "A-200"
    assert data["purpose"] == "قيمة فاتورة مبيعات رقم A-77"
