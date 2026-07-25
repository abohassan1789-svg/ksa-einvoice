"""Status Analysis Report metadata (تقرير تحليل الحالات).

Picked up automatically by ``app/services/permission_registry.py`` via the
``PERMISSION_TARGET`` attribute, so the report appears in the permissions
matrix and the navigation Reports group after the next sync.
"""


class StatusAnalysisReport:
    PERMISSION_TARGET = {
        "module_code": "reports",
        "module_name_ar": "التقارير",
        "module_name_en": "Reports",
        "target_code": "status_analysis_report",
        "target_name_ar": "تقرير تحليل الحالات",
        "target_name_en": "Status Analysis Report",
    }
