"""Access report `RepYaomia` -> daily follow-up report."""


class DailyFollowupReport:
    selector = "O7"
    report_code = 7
    access_report_name = "RepYaomia"

    # Discovered automatically by the dynamic permissions registry.
    PERMISSION_TARGET = {
        "module_code": "reports",
        "module_name_ar": "التقارير",
        "module_name_en": "Reports",
        "target_code": "daily_followup_report",
        "target_name_ar": "تقرير المتابعة اليومية",
        "target_name_en": "Daily Follow-up Report",
    }
