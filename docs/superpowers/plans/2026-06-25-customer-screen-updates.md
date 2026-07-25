# Customer Screen Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the active PySide6 customer review screen with hidden fields, area selection, F1 lookup, and customer IDs starting at 1001.

**Architecture:** Keep the existing generic `ReviewCrudPage` architecture, extending it only where the customer screen needs special behavior. Data rules stay in `ReviewDataService`; UI-only lookup/filtering stays in `review_window.py`.

**Tech Stack:** Python, PySide6, psycopg 3, PostgreSQL, pytest.

---

### Task 1: Data Metadata And ID Rules

**Files:**
- Modify: `app/services/review_data_service.py`
- Test: `tests/unit/test_review_data_service.py`

- [ ] Add tests for hidden customer fields and customer IDs starting at 1001.
- [ ] Extend `FieldSpec` with `hidden_on_form`.
- [ ] Mark the four requested installment/extra area fields as hidden on the customer form.
- [ ] Change `_next_id` so customers start at 1001 and other tables keep the existing `MAX + 1` behavior.

### Task 2: Customer Area Selection

**Files:**
- Modify: `app/services/review_data_service.py`
- Modify: `app/ui/review_window.py`

- [ ] Add `list_places_for_selection()` to load `place_id` and `place_number`.
- [ ] Render `place_area_feddan` as a customer-only combo box populated from `places`.
- [ ] Save the selected combo value back into `place_area_feddan`.
- [ ] Fill the combo correctly when an existing customer loads.

### Task 3: F1 Lookup Dialog

**Files:**
- Modify: `app/ui/review_window.py`
- Test: `tests/unit/test_customer_lookup_filter.py`

- [ ] Add pure helper functions for lookup filtering and visible-column projection.
- [ ] Add tests for searching any visible column.
- [ ] Create `CustomerLookupDialog` with a search box, column checkboxes, and a table.
- [ ] Bind F1 in the customer page to open the dialog.
- [ ] On double-click, load the selected customer into the main customer form.

### Task 4: Layout Adjustments

**Files:**
- Modify: `app/ui/review_window.py`

- [ ] Skip hidden fields when building the customer form.
- [ ] Reduce editor height slightly.
- [ ] Set explicit RTL label/editor alignment.
- [ ] Increase customer list width so the table is wider.

### Task 5: Existing Customer Renumber Migration

**Files:**
- Create: `app/database/migrations/003_renumber_customers_from_1001.sql`

- [ ] Add a transaction-safe SQL migration that maps old customer IDs to `row_number() + 1000`.
- [ ] Update `daily_followups.customer_id` using the mapping.
- [ ] Update `customers.customer_id` using the same mapping.
- [ ] Include verification queries at the end.

### Task 6: Verification

**Files:**
- No production files.

- [ ] Run `pytest`.
- [ ] Run Python compile checks for modified app files.
- [ ] Report any unavailable verification, especially live PostgreSQL UI verification.

