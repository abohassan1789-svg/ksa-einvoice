# CRM Review Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PySide6 review application for the three validated CRM tables: places, case statuses, and customers.

**Architecture:** Use a small psycopg data service for direct CRUD against the existing `crm` PostgreSQL database. Use one reusable PySide6 RTL review page that matches the supplied customer UI style: green header, toolbar, right-side data form, and left-side search/list panel.

**Tech Stack:** Python 3.12, PySide6, psycopg 3, PostgreSQL 18.

---

### Task 1: Local Launch Configuration

**Files:**
- Create: `D:/CRM/CRM_PYTHON_APP_STRUCTURE/.env`
- Create: `D:/CRM/CRM_PYTHON_APP_STRUCTURE/launch_crm_review.bat`

- [x] Add local database connection values for `crm`.
- [x] Add a launcher that runs `py -3.12 -m app.main`.

### Task 2: Data Service

**Files:**
- Create: `D:/CRM/CRM_PYTHON_APP_STRUCTURE/app/services/review_data_service.py`

- [x] Implement table metadata for `places`, `case_statuses`, and `customers`.
- [x] Implement list, get, create, update, delete, and dependency checks.

### Task 3: PySide6 Review UI

**Files:**
- Create: `D:/CRM/CRM_PYTHON_APP_STRUCTURE/app/ui/review_window.py`
- Modify: `D:/CRM/CRM_PYTHON_APP_STRUCTURE/app/main.py`

- [x] Build a green RTL header and toolbar matching the supplied screenshot.
- [x] Build tabs for customers, places, and case statuses.
- [x] Wire CRUD actions to PostgreSQL through the data service.

### Task 4: Desktop Shortcut

**Files:**
- Create shortcut on current user's Desktop: `CRM مراجعة البيانات.lnk`

- [x] Point the shortcut to `launch_crm_review.bat`.

### Task 5: Verification

- [x] Run Python compile check.
- [x] Run app startup smoke test in offscreen mode.
- [x] Query PostgreSQL counts for the three reviewed tables.
