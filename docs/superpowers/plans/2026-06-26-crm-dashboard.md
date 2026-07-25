# CRM Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved in-app CRM dashboard with compact varied charts, RTL Arabic filters, and source-backed aggregates.

**Architecture:** Keep dashboard data access in `app/services/dashboard_service.py`, rendering in `app/ui/screens/dashboard_page.py`, and leave `app/ui/main_window.py` as the existing integration point. Use the project's current psycopg `Database` helper and defensive error handling.

**Tech Stack:** Python, PySide6, PostgreSQL through psycopg helper, pytest.

---

### Task 1: Service Contract And Tests

**Files:**
- Modify: `tests/unit/test_dashboard_interactions.py`
- Modify: `app/services/dashboard_service.py`

- [ ] Add tests for dashboard counts, latest customer status SQL, and distinct employee/customer counts.
- [ ] Run `pytest tests/unit/test_dashboard_interactions.py -q` and verify new tests fail before implementation.
- [ ] Implement filter-aware service methods using existing table/column names from SQL migrations.
- [ ] Re-run the same tests and confirm they pass.

### Task 2: Compact RTL Dashboard UI

**Files:**
- Modify: `app/ui/screens/dashboard_page.py`

- [ ] Add a compact RTL filter bar with date, area, employee, status, refresh, and reset controls.
- [ ] Replace identical bar panels with varied visuals: doughnut for status, horizontal bars for area, compact ranked bars for employees.
- [ ] Keep chart panels moderate in height and show Arabic no-data/friendly-error states.
- [ ] Wire filters to service methods without SQL in the UI.

### Task 3: Validation

**Files:**
- Validate: `app/services/dashboard_service.py`
- Validate: `app/ui/screens/dashboard_page.py`
- Validate: `tests/unit/test_dashboard_interactions.py`

- [ ] Run `python -m py_compile app/services/dashboard_service.py app/ui/screens/dashboard_page.py tests/unit/test_dashboard_interactions.py`.
- [ ] Run dashboard-related unit tests.
- [ ] Attempt an offscreen widget smoke import if the local environment supports PySide6.
