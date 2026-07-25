"""Access report `RepYaomia1` -> sales report."""


class SalesReport:
    selector = "o13"
    report_code = 13
    access_report_name = "RepYaomia1"

    # Discovered automatically by the dynamic permissions registry.
    PERMISSION_TARGET = {
        "module_code": "reports",
        "module_name_ar": "التقارير",
        "module_name_en": "Reports",
        "target_code": "sales_report",
        "target_name_ar": "تقرير المبيعات",
        "target_name_en": "Sales Report",
    }
