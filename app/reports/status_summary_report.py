"""Access report `RepOverTIME` -> status summary report."""


class StatusSummaryReport:
    selector = "O6"
    report_code = 6
    access_report_name = "RepOverTIME"

    # Discovered automatically by the dynamic permissions registry.
    PERMISSION_TARGET = {
        "module_code": "reports",
        "module_name_ar": "التقارير",
        "module_name_en": "Reports",
        "target_code": "status_summary_report",
        "target_name_ar": "تقرير ملخص الحالات",
        "target_name_en": "Status Summary Report",
    }
