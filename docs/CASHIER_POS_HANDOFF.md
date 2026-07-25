# Cashier / POS Module — Handoff

Complete record of the experimental **Cashier / نقطة البيع** module built across
this conversation, so work can continue in a new session without re-deriving
context.

- **Project:** `D:\PHASEINV2\CRM_PYTHON_APP_STRUCTURE` (PySide6 desktop CRM, Arabic RTL)
- **Database:** PostgreSQL `InvPhase2` (localhost, user `postgres`, pass in `.env`)
- **UI framework:** PySide6; screens open as maximized windows from `app/ui/main_window.py`
- **Layers:** `models → repositories → services → ui{screens,dialogs,widgets,common}`
- **Run/tests (this machine):** Python 3.11 with explicit `PYTHONHOME` (py/python312 are broken)

```powershell
$p311="C:\Users\esraa\AppData\Local\Programs\Python\Python311"
$env:PYTHONHOME=$p311
$env:PYTHONPATH="D:\PHASEINV2\CRM_PYTHON_APP_STRUCTURE"
$env:PYTHONIOENCODING="utf-8"
$env:QT_QPA_PLATFORM="offscreen"   # only for headless/offscreen UI tests
Set-Location "D:\PHASEINV2\CRM_PYTHON_APP_STRUCTURE"
& "$p311\python.exe" -m pytest tests/services tests/repositories -q
```

DB tests self-skip if PostgreSQL is unreachable and clean up their own rows.

---

## Phase 1 — Database tables (migration 015)

Two new tables in `InvPhase2` via **migration 015** (SQL-file migration system in
`app/database/migrations/`, head is now **015**). Nothing else in the DB was
changed.

- Files: `app/database/migrations/015_create_cashier_invoices_schema.sql` (+ `015_downgrade_...sql`)
- `cashier_invoices` (header): PK `id` IDENTITY; `invoice_number` UNIQUE, auto
  `CINV-000001` from sequence `cashier_invoice_number_seq` (concurrency-safe
  `nextval`, never MAX+1); `invoice_uuid` (uuid, unique-when-present).
- `cashier_invoice_lines` (detail): FK `cashier_invoice_id` → `cashier_invoices(id)`
  **ON DELETE CASCADE**; FK `product_id` → `products(id)` RESTRICT; UNIQUE
  `(cashier_invoice_id, line_number)`.
- Reuses existing master tables via FK: `customers(customer_id)` RESTRICT,
  `companies(id)` RESTRICT, `app_users(id)` SET NULL.
- **KEY CONSTRAINT:** `cashier_invoices.company_id` is **NOT NULL**. There is **no
  `branches` table** in InvPhase2, so `branch_id` is a plain nullable integer with
  **no FK**.
- Money `numeric(18,2)`; qty/unit_price `numeric(18,6)`; vat_rate `numeric(5,2)`.
- ZATCA columns exist but are all nullable and were never populated (see Phase 4).

### Discovered real column names (used everywhere — do not assume others)
- `products`: `id` (PK), `item_code`, `item_name`, `price`
- `customers`: `customer_id` (PK), `customer_name`, `vat_number`, `cr`, `address`, `phone_number`
- `companies`: `id` (PK), `name_ar`, `name_en`, `commercial_registration`, `vat_number`, `address_ar`, `logo` (bytea), `logo_mime`
- `app_users`: `id` (PK), `username`, `full_name`

---

## Phase 2 — Service, repository, screen (Design A)

The user chose **Design A** (classic two-pane POS: product cards on the right,
invoice table + big total on the left). Files created:

- `app/models/cashier_invoice.py` — table/column/status constants, Decimal
  precisions, `INVOICE_INSERT_COLUMNS` / `INVOICE_SELECT_COLUMNS` /
  `INVOICE_UPDATE_COLUMNS` / `ISSUE_HEADER_COLUMNS` / `LINE_*`. Statuses:
  `DRAFT` / `ISSUED` / `CANCELLED`; type `SIMPLIFIED`; currency `SAR`;
  `zatca_status` default `NOT_GENERATED`; `DEFAULT_VAT_RATE = 15.00`.
- `app/repositories/cashier_repository.py` — psycopg `Database` (autocommit reads)
  + `_txn()` (transactional writes), mirroring `SaudiSalesInvoiceRepository`.
  Methods: master-data list/search/get (products/customers/companies),
  `reserve_invoice_number()` (sequence `nextval`), `insert_draft`, `update_draft`
  (guarded WHERE `invoice_status='DRAFT'`), `issue_invoice` (atomic DRAFT→ISSUED),
  `load_invoice`, `list_drafts`, `count_drafts`, `search_invoices`,
  `unissue_invoice`, `record_print`, `delete_draft`, `get_user_display_name`.
  `StaleCashierInvoiceError` for guarded-update misses (concurrency).
- `app/services/cashier_service.py` — Decimal maths (`compute_line_amounts`,
  `compute_totals`, ROUND_HALF_UP), pure in-memory line helpers
  (`add_cashier_line` dedups + increments same product, `update_cashier_line_quantity`,
  `remove_cashier_line`, `recalculate_cashier_invoice`), server-side seller/buyer
  snapshots, `create_cashier_draft` (reserves number, deferred insert),
  `save_cashier_draft`, `issue_cashier_invoice_locally`, `search_cashier_invoices`,
  `unissue_cashier_invoice`, `delete_cashier_draft`. Error hierarchy:
  `CashierError` / `CashierValidationError` / `CashierPermissionError` /
  `CashierConcurrencyError`. Permission target `sales.cashier`, actions
  save/edit/post/unpost/delete (permission_check=`SESSION.can`).
- `app/ui/screens/cashier_page.py` (`CashierPage`) — Design A, RTL, reuses
  `saudi_invoice_style` + `EntityPickerDialog` for company/customer search.
  Modes: `idle` / `draft` / `issued`. Unit price is always `products.price`
  (read-only); only quantity is editable inline. `showEvent` reloads products so
  newly-added items appear; manual 🔄 refresh button beside the product search.
- `app/ui/dialogs/cashier_dialogs.py` — `CashierDraftDialog` (drafts only) and
  `CashierInvoiceSearchDialog` (F1: all statuses, filters = invoice-number text +
  company drop-down + customer drop-down + status).

### Navigation + permissions (modified files)
- `app/ui/main_window.py`: import `CashierPage`, factory `cashier`
  (`view = sales.cashier.view`), sidebar button **🛒 الكاشير**.
- `app/services/permission_registry.py`: added `sales.cashier` screen target.

### KEY DESIGN DECISION (company_id NOT NULL vs. "draft without company")
Because `company_id` is NOT NULL and migration 015 must not change, a draft
**cannot be persisted without a company**. So **New** reserves a DB-generated
number in memory and **defers the row INSERT to Save**, which requires a company.
Documented deviation from the phase-2 spec's "draft may be saved without a
company".

### Later UX additions in Phase 2
- **F1 search dialog** over ALL cashier invoices; company/customer are drop-downs.
- **إلغاء الاعتماد (un-issue):** `unissue_cashier_invoice` reverts ISSUED→DRAFT
  (atomic, guarded) so an issued invoice can be edited again — **explicitly
  requested by the user**, overriding the original "issued invoices are
  immutable" rule.
- **Products screen bug fix** (`app/ui/screens/products_screen.py`): empty
  quantity/price now default to `0` before save, fixing
  `null value in column "quantity" ... violates not-null`.

---

## Phase 3 — Toolbar layout fix
11 actions were crowding one row. `cashier_page.py` now uses **two compact rows**
(font 11px, height 30px, tighter width), row 1 = document/edit actions, row 2 =
issue/print actions. Some labels shortened ("بحث (F1)", "طباعة الإيصال", "حذف السطر").

---

## Phase 4 — Thermal receipt printing + ZATCA QR (with honest gating)

Files created:
- `app/services/cashier_print_service.py` — pure logic (no Qt): load, validate
  (status/totals/seller), `build_cashier_receipt_model` (all display data from
  **snapshots** — reprint-stable), **binary-TLV → Base64 QR** encode
  (`build_zatca_qr_base64`) + `decode_and_validate_zatca_qr` (tags 1–9 present
  once + in order, UTF-8 byte lengths, no separators, deterministic, tag→source
  match), `check_zatca_prerequisites`, `record_successful_print`. Errors:
  `CashierPrintError` / `CashierPrintValidationError` / `CashierQrError` /
  `ZatcaPrerequisitesMissing`.
- `app/ui/screens/cashier_receipt_print.py` — thermal **80mm** HTML builder
  (dynamic height via `scrollHeight`, Arabic RTL via Chromium), `render_qr_data_uri`
  (qrcode+PIL: black-on-white, 4-module quiet zone, integer module scaling,
  ~38mm @ 203 DPI), `CashierReceiptPreviewDialog` (same renderer for
  preview/PDF/print). Reuses the existing HTML→`QWebEngineView`→`QPrinter`
  framework (NOT a second framework, NOT A4).
- Screen buttons **معاينة الطباعة** + **طباعة الإيصال** (issued only); draft →
  watermarked preview; print records `print_count`/`last_printed_at` only after
  `printFinished(success=True)`.

Modified: `cashier_repository.py` (`record_print` updates ONLY
`print_count`/`last_printed_at`/`zatca_qr_base64`, guarded to ISSUED;
`get_user_display_name`); `cashier_invoice.py` (`INVOICE_SELECT_COLUMNS` extended
read-only to include the ZATCA crypto columns so the print service can read them);
`cashier_page.py` (print buttons + handlers).

---

## Phase 5 — QR now prints (local test stamp)

The Phase-4 blocker below is **resolved for printing**: the receipt now always
carries a tags 1–9 QR. It is signed by the project's existing **local test-only**
key, reusing the same pipeline the Saudi sales-invoice screen already uses —
**not** an onboarded ZATCA CSID (see "What this is / is NOT").

New file `app/services/cashier_zatca_signing.py`:
- `build_cashier_zatca_fields(header, lines, *, icv, pih)` — pure; builds the UBL
  XML via `saudi_zatca_generator.build_invoice_xml`, hashes it
  (`base64(SHA-256(xml))` = tag 6) and signs via
  `saudi_egs_test_signer.sign_invoice_hash` (tags 7/8/9). Sets
  `zatca_status='READY'` (allowed by the migration-015 CHECK) — never
  `REPORTED`/`ACCEPTED`, because nothing is sent to ZATCA.
- `resolve_seller_for_qr()` — QR tags 1–2 must be non-empty, so a company with no
  `name_ar` / `vat_number` falls back to documented placeholders
  (`منشأة غير محددة` / `300000000000003`, ZATCA's own sample VAT). Placeholders
  are used **only** where the source value is genuinely absent, are written into
  the snapshot columns so the stored row / receipt / QR all agree, and trigger an
  extra on-receipt notice.
- `CashierZatcaSigner.ensure_signed(invoice_id)` — idempotent.

Modified:
- `cashier_repository.py`: **`sign_zatca_atomically()` is the single writer of the
  `zatca_*` columns.** One transaction: `SELECT ... FOR UPDATE` guarded to
  `ISSUED AND zatca_invoice_hash IS NULL` → `pg_advisory_xact_lock(8150, company_id)`
  → read ICV (`MAX+1` per company) + PIH (latest hash, else `GENESIS_PIH`) →
  build → guarded UPDATE. The `IS NULL` guard makes signing **idempotent**, so a
  reprint can never re-sign and change an issued invoice's QR.
- `cashier_service.py`: signs after `issue_invoice`; `ensure_cashier_zatca_data()`
  is best-effort and **never raises** — a signing failure leaves the invoice
  issued and correct, just unsigned, and the next print retries.
- `cashier_print_service.py`: `load_printable_cashier_invoice()` calls
  `ensure_signed` first, so invoices **issued before this feature get signed on
  their first print**. Model gains `qr.test_stamp` / `qr.used_defaults`.
- `cashier_receipt_print.py`: prints the notices under the QR.
- `cashier_page.py`: unchanged — its existing prerequisite gate now simply passes.

### Phase 5b — DRAFT preview QR (the "seller name missing" report)
A draft preview showed **"رمز QR للمرحلة الثانية غير متاح — الناقص: اسم البائع، الرقم
الضريبي للبائع، Tags 6–9"** even though a company *was* selected. Cause: the
`seller_*_snapshot` columns are NULL until issue, and the model only ever read
those columns, so a draft could never satisfy tags 1–2. (Not a data problem — the
company was fine.)

Fix, mirroring `saudi_sales_invoice_service.build_preview_qr`:
- `cashier_zatca_signing.seller_from_company()` / `build_preview_header()` — for a
  DRAFT, seller name + VAT come from the **selected company row** (the VAT always
  travels with the company; it is never typed on the Cashier screen and needs no
  field there), with the placeholders only for what the company itself lacks.
  Phase-2 fields are then generated with `icv=0` / `GENESIS_PIH`.
- `cashier_print_service.build_cashier_receipt_model()`: draft → preview QR +
  `_draft_seller()` fills the receipt's seller block (name/VAT/CR/address) from
  the live company. An ISSUED invoice is untouched: still 100% snapshot-sourced.

**The preview QR is never persisted** and is deliberately *not* the final code —
the invoice number, timestamp, ICV and PIH are only fixed at issue, so the QR is
regenerated then and the two differ (asserted by a test). The receipt therefore
labels it **«رمز QR للمعاينة فقط — يُعاد توليده عند إصدار الفاتورة»**, keeps the DRAFT
watermark, and official print stays ISSUED-only.

### ⚠️ What this is / is NOT (unchanged integrity position)
Nothing is faked: tag 6 is a real SHA-256 of the real XML, and tag 7 genuinely
verifies against tag 8 over tag 6 (asserted by a real ECDSA check in the tests).
The QR is therefore **structurally Phase-2 and self-consistent**, and an offline
reader parses it as compatible. But the key is **local and self-signed**, so:
- the invoice is **NOT** valid for ZATCA clearance/reporting, and the Fatoora
  portal / official validator **will reject** the stamp.

⚠️ **The receipt no longer says this.** It used to print
`ختم توقيع محلي للاختبار — غير معتمد من هيئة الزكاة والضريبة والجمارك` under the QR;
that line was **removed at the user's explicit request** (2026-07-15). Consequence
to be aware of: a printed receipt now looks like a compliant Phase-2 tax invoice
to a customer or auditor holding the paper, with nothing on it indicating the
stamp is local. The caveat now lives only in `zatca_status='READY'`, the
`qr["test_stamp"]` model flag, this doc and the module docstrings. Re-enable it in
`cashier_receipt_print.build_cashier_receipt_html` (constants
`TEST_STAMP_NOTICE_AR/EN` are still exported) if this ships to real merchants.

The honest gate still stands: if signing fails the receipt shows
**"رمز QR للمرحلة الثانية غير متاح"** — never a fake or truncated QR labelled Phase-2.

**To make it ZATCA-valid:** swap the signer inside `build_cashier_zatca_fields`
for a real onboarded EGS/CSID flow. Nothing else changes.

### Verified end-to-end (2026-07-15)
Issued a real invoice → rendered the actual 80mm receipt QR PNG → **scanned the
image back** with an independent decoder (`zxing-cpp`) → payload matched
byte-for-byte → decoded to tags 1–9 in order with real company data
(`شركه ركن سنيم للتجاره` / `311628287100003`, 230.00, VAT 30.00) → passed
`decode_and_validate_zatca_qr` → ECDSA verified → reprint produced an identical
QR. Test row cleaned up. (`zxing-cpp` was installed to a scratch dir only; it is
**not** a project dependency.)

### Phase-4 blocker (historical — see Phase 5 above)
Cashier invoices were previously issued with `zatca_invoice_hash`,
`zatca_signature`, `zatca_public_key`, `zatca_cryptographic_stamp` = NULL
(`NOT_GENERATED`), so official print was blocked. Production-grade (ZATCA-valid)
QR is still not possible without an onboarded CSID.

---

## Test suite (all Cashier tests green)
- `tests/services/test_cashier_service.py` — core create/save/issue/unissue/search, calc, dedup, snapshots, no side effects (DB rows cleaned up).
- `tests/repositories/test_cashier_repository.py` — reserve number, insert/load/cascade, guarded update/issue/un-issue, print tracking.
- `tests/services/test_cashier_print_service.py` — receipt model, draft/cancelled blocking, snapshot sourcing, totals match/mismatch, TLV encode/decode/validate, tag order/length/UTF-8, determinism, prereq gating, QR image square+quiet-zone, thermal width/dynamic height, REPRINT, DB print-count + no-side-effects.
- `tests/services/test_cashier_zatca_signing.py` (22 tests, pure) — draft preview QR
  (seller from the selected company, all 9 tags, placeholder chain, snapshots never
  overridden), seller defaults
  used only when absent + persisted, tag 6 == real SHA-256 of the XML, **real ECDSA
  verification of tag 7 against tag 8 over tag 6**, status never REPORTED/ACCEPTED,
  ICV/PIH land in the XML, distinct invoices → distinct hash/signature, stable
  public key, and signed fields → a tags 1–9 QR passing the project's validator.

Latest run: **all Cashier tests pass**; full `tests/services tests/repositories`
= 240 passed, 1 skipped, **1 pre-existing UNRELATED failure**
(`test_saudi_sales_invoice_repository::test_save_roundtrip_no_crypto_and_public_untouched`
asserts `invoice_counter_value == 1`, but seller already has a committed ICV in
this DB — data-dependent, not caused by Cashier work).

---

## Explicitly NOT done (out of scope / by design)
No accounting entries, receipt vouchers, customer-balance postings, inventory
movements, payment tables, returns/credit notes, discounts, multiple VAT
categories, posting to `sales_invoice*`, ZATCA reporting/clearance API, new
migrations/tables, A4 cashier printing, or any fake/placeholder QR.

## Suggested next steps
1. **Real onboarded EGS/CSID** (biggest item) — replace the local test signer
   inside `build_cashier_zatca_fields` so the stamp becomes ZATCA-valid and the
   invoice is acceptable for reporting/clearance. Everything downstream (QR
   encode/validate/render/print/reprint) already works and stays as-is.
2. Physical thermal-print scan test on 80mm paper (no printer was available here);
   record real QR mm size + printer DPI, verify sharpness/scan.
3. Optional: 58mm layout pass; persistent printer/paper config.

## Memory files (context that persists across sessions)
`C:\Users\esraa\.claude\projects\D--PHASEINV2\memory\`: `cashier-pos-tables.md`,
`cashier-pos-screen.md`, `cashier-thermal-printing.md` (+ `MEMORY.md` index).
