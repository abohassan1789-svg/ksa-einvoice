"""Customer Statement report metadata (كشف حساب العميل).

Discovered automatically by ``permission_registry._discover_report_targets`` via
its ``PERMISSION_TARGET`` dict, so the report joins the permissions matrix on the
next sync. Metadata only — no logic here.
"""


class CustomerStatementReport:
    PERMISSION_TARGET = {
        "module_code": "reports",
        "module_name_ar": "التقارير",
        "module_name_en": "Reports",
        "target_code": "customer_statement",
        "target_name_ar": "كشف حساب العميل",
        "target_name_en": "Customer Statement",
    }
