"""Central registry of CRM screens, reports and their permission actions.

This is the single source of truth the dynamic permissions system scans. A
permission code is always ``module_code.target_code.action_code``.

To expose a NEW screen in the permissions system: add an entry to
``SCREEN_TARGETS`` (and wire its widget in the navigation/main window).

To expose a NEW report: give its report class a ``PERMISSION_TARGET`` dict in
``app/reports`` — it is discovered automatically — or add it to
``STATIC_REPORT_TARGETS`` below. Either way it appears after the next sync.

To HIDE a screen/report from the permissions matrix without unregistering it,
add its target_code to ``HIDDEN_TARGET_CODES``. Read the note there first — it
also denies the code to every non-admin, so it must move in step with the
sidebar.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

# (action_code, name_ar, name_en) — order is the matrix column order.
SCREEN_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("view", "عرض", "View"),
    ("create", "إضافة", "Create"),
    ("edit", "تعديل", "Edit"),
    ("save", "حفظ", "Save"),
    ("delete", "حذف", "Delete"),
    ("approve", "اعتماد", "Approve"),
    ("print", "طباعة", "Print"),
    ("export", "تصدير", "Export"),
    ("import", "استيراد", "Import"),
    ("post", "ترحيل", "Post"),
    ("unpost", "إلغاء الترحيل", "Unpost"),
)

REPORT_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("view", "عرض", "View"),
    ("preview", "معاينة", "Preview"),
    ("filter", "تصفية", "Filter"),
    ("print", "طباعة", "Print"),
    ("export", "تصدير", "Export"),
)

ACTION_NAMES_AR = {code: ar for code, ar, _en in (*SCREEN_ACTIONS, *REPORT_ACTIONS)}
ACTION_NAMES_EN = {code: en for code, _ar, en in (*SCREEN_ACTIONS, *REPORT_ACTIONS)}

CATEGORY_SCREEN_AR, CATEGORY_SCREEN_EN = "الشاشات", "Screens"
CATEGORY_REPORT_AR, CATEGORY_REPORT_EN = "التقارير", "Reports"

# --- Screens -----------------------------------------------------------------
# target_code MUST match the screen's runtime key so enforcement lines up
# (CRUD screens use their TableSpec.key; security pages use these codes).
SCREEN_TARGETS: list[dict[str, str]] = [
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "customers", "target_name_ar": "العملاء", "target_name_en": "Customers"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "products", "target_name_ar": "الأصناف", "target_name_en": "Products"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "companies", "target_name_ar": "الشركات", "target_name_en": "Companies"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "receipt_vouchers", "target_name_ar": "سندات القبض", "target_name_en": "Receipt Vouchers"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "employees", "target_name_ar": "الموظفين", "target_name_en": "Employees"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "daily_followups", "target_name_ar": "المتابعة اليومية", "target_name_en": "Daily Follow-ups"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "places", "target_name_ar": "المناطق", "target_name_en": "Places"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "case_statuses", "target_name_ar": "الحالات", "target_name_en": "Case Statuses"},
    {"module_code": "crm", "module_name_ar": "إدارة بيانات CRM", "module_name_en": "CRM Data",
     "target_code": "attachments", "target_name_ar": "المرفقات", "target_name_en": "Attachments"},
    # Saudi Phase-2 sales invoice (module: sales) — target_code matches the
    # navigation runtime key so view enforcement lines up.
    {"module_code": "sales", "module_name_ar": "فواتير المبيعات", "module_name_en": "Sales Invoices",
     "target_code": "saudi_sales_invoices", "target_name_ar": "فاتورة المبيعات السعودية",
     "target_name_en": "Saudi Sales Invoice"},
    # Experimental Cashier / POS (module: sales) — target_code matches the
    # navigation runtime key ("cashier") and the service's PERM_TARGET.
    {"module_code": "sales", "module_name_ar": "فواتير المبيعات", "module_name_en": "Sales Invoices",
     "target_code": "cashier", "target_name_ar": "الكاشير / نقطة البيع",
     "target_name_en": "Cashier / POS"},
    # Security screens (module: security)
    {"module_code": "security", "module_name_ar": "المستخدمين والصلاحيات", "module_name_en": "Users & Permissions",
     "target_code": "users", "target_name_ar": "المستخدمين", "target_name_en": "Users Management"},
    {"module_code": "security", "module_name_ar": "المستخدمين والصلاحيات", "module_name_en": "Users & Permissions",
     "target_code": "roles", "target_name_ar": "مجموعات الصلاحيات", "target_name_en": "Roles / Permission Groups"},
    {"module_code": "security", "module_name_ar": "المستخدمين والصلاحيات", "module_name_en": "Users & Permissions",
     "target_code": "user_permissions", "target_name_ar": "صلاحيات المستخدم", "target_name_en": "User Permissions"},
]

# --- Reports -----------------------------------------------------------------
# Fallback list used if dynamic discovery from app/reports finds nothing.
STATIC_REPORT_TARGETS: list[dict[str, str]] = [
    {"module_code": "reports", "module_name_ar": "التقارير", "module_name_en": "Reports",
     "target_code": "daily_followup_report", "target_name_ar": "تقرير المتابعة اليومية",
     "target_name_en": "Daily Follow-up Report"},
    {"module_code": "reports", "module_name_ar": "التقارير", "module_name_en": "Reports",
     "target_code": "sales_report", "target_name_ar": "تقرير المبيعات", "target_name_en": "Sales Report"},
    {"module_code": "reports", "module_name_ar": "التقارير", "module_name_en": "Reports",
     "target_code": "status_summary_report", "target_name_ar": "تقرير ملخص الحالات",
     "target_name_en": "Status Summary Report"},
]


# --- Hidden targets ----------------------------------------------------------
# Targets that stay fully registered above but are kept OUT of the permissions
# matrix, because the user cannot reach them in the UI (per user request,
# 2026-07-15). The rule the user asked for: **what the sidebar shows is exactly
# what the permissions screen controls** — a permission for a screen nobody can
# open is noise that invites granting access that does nothing.
#
# These are NOT deleted: `PermissionsSyncService` flips them to `is_active=false`
# on the next sync, so every existing role/user grant survives untouched and
# re-listing a code here brings the row (and its grants) straight back.
#
# ⚠️ Two-way rule. `can()` resolves against ACTIVE permissions only, so a code
# listed here is denied to every non-admin. Un-hiding a screen therefore means
# removing it from BOTH this set and `main_window.HIDDEN_NAV_KEYS` / `REPORTS`;
# doing only the latter puts a nav button on screen that silently stays
# invisible to everyone but Admin. `tests/unit/test_permission_ui_parity.py`
# fails on either half being forgotten.
HIDDEN_TARGET_CODES: frozenset[str] = frozenset({
    # Screens — mirror of main_window.HIDDEN_NAV_KEYS.
    "employees",           # الموظفين
    "daily_followups",     # المتابعة اليومية
    "places",              # المناطق
    "case_statuses",       # الحالات
    "attachments",         # المرفقات
    # Reports — none of these has a live entry in main_window.REPORTS
    # (daily_followup_smart_report / status_analysis_report are commented out
    # there; sales_report has no screen at all). Only كشف حساب العميل is live.
    "daily_followup_report",        # تقرير المتابعة اليومية
    "daily_followup_smart_report",  # تقرير المتابعة اليومية الذكي
    "status_summary_report",        # تقرير ملخص الحالات
    "status_analysis_report",       # تقرير تحليل الحالات
    "sales_report",                 # تقرير المبيعات
})


def make_permission_code(module_code: str, target_code: str, action_code: str) -> str:
    return f"{module_code}.{target_code}.{action_code}"


def _discover_report_targets() -> list[dict[str, str]]:
    """Scan app/reports for classes exposing a ``PERMISSION_TARGET`` dict.

    Import failures (e.g. optional reportlab/openpyxl deps) are ignored so the
    scan never breaks the app.
    """
    found: dict[str, dict[str, str]] = {}
    try:
        import app.reports as reports_pkg
    except Exception:
        return []
    for mod_info in pkgutil.iter_modules(reports_pkg.__path__):
        try:
            module = importlib.import_module(f"app.reports.{mod_info.name}")
        except Exception:
            continue
        for attr in vars(module).values():
            target = getattr(attr, "PERMISSION_TARGET", None)
            if isinstance(target, dict) and target.get("target_code"):
                entry = {
                    "module_code": target.get("module_code", "reports"),
                    "module_name_ar": target.get("module_name_ar", "التقارير"),
                    "module_name_en": target.get("module_name_en", "Reports"),
                    "target_code": target["target_code"],
                    "target_name_ar": target.get("target_name_ar", target["target_code"]),
                    "target_name_en": target.get("target_name_en", target["target_code"]),
                }
                found[entry["target_code"]] = entry
    return list(found.values())


def report_targets() -> list[dict[str, str]]:
    discovered = _discover_report_targets()
    merged: dict[str, dict[str, str]] = {t["target_code"]: t for t in STATIC_REPORT_TARGETS}
    for entry in discovered:
        merged[entry["target_code"]] = entry  # discovered overrides static
    return list(merged.values())


def collect_targets(include_hidden: bool = False) -> list[dict[str, Any]]:
    """Return every registered target with its type, category and actions.

    ``HIDDEN_TARGET_CODES`` are filtered out unless ``include_hidden`` — that
    flag exists for tooling that needs the full registry (and for the parity
    test); the sync and the UI both want the filtered view.
    """
    targets: list[dict[str, Any]] = []
    for screen in SCREEN_TARGETS:
        targets.append({
            **screen,
            "permission_type": "screen",
            "category_ar": CATEGORY_SCREEN_AR,
            "category_en": CATEGORY_SCREEN_EN,
            "actions": [code for code, _ar, _en in SCREEN_ACTIONS],
        })
    for report in report_targets():
        targets.append({
            **report,
            "permission_type": "report",
            "category_ar": CATEGORY_REPORT_AR,
            "category_en": CATEGORY_REPORT_EN,
            "actions": [code for code, _ar, _en in REPORT_ACTIONS],
        })
    if include_hidden:
        return targets
    return [t for t in targets if t["target_code"] not in HIDDEN_TARGET_CODES]


def build_permission_rows() -> list[dict[str, Any]]:
    """Flatten all targets × actions into permission row dicts for sync/upsert."""
    rows: list[dict[str, Any]] = []
    for target in collect_targets():
        for action_code in target["actions"]:
            rows.append({
                "permission_code": make_permission_code(
                    target["module_code"], target["target_code"], action_code
                ),
                "permission_type": target["permission_type"],
                "module_code": target["module_code"],
                "module_name_ar": target["module_name_ar"],
                "module_name_en": target["module_name_en"],
                "category_ar": target["category_ar"],
                "category_en": target["category_en"],
                "target_code": target["target_code"],
                "target_name_ar": target["target_name_ar"],
                "target_name_en": target["target_name_en"],
                "action_code": action_code,
                "action_name_ar": ACTION_NAMES_AR.get(action_code, action_code),
                "action_name_en": ACTION_NAMES_EN.get(action_code, action_code),
            })
    return rows
