"""Application shell for the CRM app.

Thin shell: owns the right-side sidebar/main menu and opens each screen as its
own maximized top-level window. It contains no form layout, no business logic,
and no database access — screens live under ``app.ui.screens`` and all data
access stays in the services. Opening any screen is gated by the current
session's ``view`` permission, and menu items the user cannot view are hidden.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.repositories.security_repository import SecurityRepository
from app.security.session_context import SESSION
from app.services.auth_service import AuthService
from app.services.permission_service import PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.review_data_service import ReviewDataService, TABLE_SPECS
from app.ui.common.theme import GREEN, GREEN_DARK
from app.ui.screens.attachments_page import AttachmentsPage
from app.ui.screens.backup_screen import BackupManagementScreen
from app.ui.screens.connection_screen import ConnectionSettingsScreen
from app.ui.screens.crm_dashboard_page import CrmDashboardPage
from app.ui.screens.customer_statement_report_screen import CustomerStatementReportScreen
from app.ui.screens.dashboard_page import DashboardPage
from app.ui.screens.case_statuses_screen import CaseStatusesScreen
from app.ui.screens.companies_screen import CompaniesScreen
from app.ui.screens.customers_screen import CustomersScreen
from app.ui.screens.daily_followup_smart_report_screen import DailyFollowupSmartReportScreen
from app.ui.screens.status_analysis_report_screen import StatusAnalysisReportScreen
from app.ui.screens.daily_followups_screen import DailyFollowupsScreen
from app.ui.screens.employees_screen import EmployeesScreen
from app.ui.screens.places_screen import PlacesScreen
from app.ui.screens.products_screen import ProductsScreen
from app.ui.screens.receipt_vouchers_screen import ReceiptVouchersScreen
from app.ui.screens.cashier_page import CashierPage
from app.ui.screens.roles_page import RolesPage
from app.ui.screens.saudi_sales_invoice_page import SaudiSalesInvoicePage
from app.ui.screens.user_permissions_page import UserPermissionsPage
from app.ui.screens.users_page import UsersPage


# CRM data modules: (spec key, sidebar label, screen class).
MODULES = (
    ("customers", "العملاء", CustomersScreen),
    ("products", "الأصناف", ProductsScreen),
    ("companies", "الشركات", CompaniesScreen),
    ("receipt_vouchers", "سندات القبض", ReceiptVouchersScreen),
    ("employees", "الموظفين", EmployeesScreen),
    ("daily_followups", "المتابعة اليومية", DailyFollowupsScreen),
    ("places", "المناطق", PlacesScreen),
    ("case_statuses", "الحالات", CaseStatusesScreen),
)
SCREEN_BY_KEY = {key: screen_cls for key, _label, screen_cls in MODULES}

# Screens intentionally hidden from the sidebar (per user request). The screens,
# their TableSpecs and their permissions stay fully registered and functional —
# only their navigation buttons are not shown, and they are skipped by prewarm so
# no time is spent building a screen the sidebar can't open.
HIDDEN_NAV_KEYS: frozenset[str] = frozenset(
    {"employees", "daily_followups", "places", "case_statuses", "attachments"}
)

# Sidebar-only icons for the CRM data screens. The screens' TableSpec.icon_text
# (the single letters C / ص / ش / س) still drives each screen's own header; the
# sidebar uses these emoji instead so every menu row shares one consistent icon
# style. Keys without an override fall back to the spec's icon_text.
NAV_ICONS: dict[str, str] = {
    "customers": "👥",
    "products": "📦",
    "companies": "🏢",
    "receipt_vouchers": "💵",
}

# Reports: (report key, sidebar label, screen class).
REPORTS = (
    # Hidden from the Reports menu (kept for possible re-enable):
    # ("daily_followup_smart_report", "Daily Follow-up Smart Report", DailyFollowupSmartReportScreen),
    # ("status_analysis_report", "تقرير تحليل الحالات", StatusAnalysisReportScreen),
    ("customer_statement", "كشف حساب العميل", CustomerStatementReportScreen),
)
REPORT_SCREEN_BY_KEY = {key: screen_cls for key, _label, screen_cls in REPORTS}

# Collapsible "Users & Permissions" group: (child key, sidebar label).
SECURITY_CHILDREN = (
    ("users", "المستخدمين"),
    ("roles", "مجموعات الصلاحيات"),
    ("user_permissions", "صلاحيات المستخدم"),
)


class ReviewMainWindow(QMainWindow):
    """Main shell: sidebar menu that opens each screen in its own window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KSA")
        self.resize(1500, 850)
        self.setMinimumSize(1000, 640)
        self.setLayoutDirection(Qt.RightToLeft)

        # Services (single instances shared by all screens).
        self.service = ReviewDataService()
        self.security_repo = SecurityRepository()
        self.auth_service = AuthService(self.security_repo)
        self.permission_service = PermissionService(self.security_repo)
        self.sync_service = PermissionsSyncService(self.security_repo)

        self.open_windows: dict[str, QWidget] = {}
        self.nav_buttons: list[QPushButton] = []
        self._nav_by_key: dict[str, QPushButton] = {}
        self._button_styles: dict[QPushButton, tuple[str, str]] = {}
        self._security_expanded = False
        self._reports_expanded = False
        self._factories = self._build_factories()
        self._build_ui()

    # --- factory / permissions ---------------------------------------------
    def _build_factories(self) -> dict[str, dict]:
        factories: dict[str, dict] = {}
        for key, _label, cls in MODULES:
            factories[key] = {
                "title": TABLE_SPECS[key].title,
                "view": f"crm.{key}.view",
                "make": (lambda c=cls: c(self.service)),
            }
        factories["users"] = {
            "title": "إدارة المستخدمين", "view": "security.users.view",
            "make": lambda: UsersPage(self.auth_service, self.permission_service),
        }
        factories["roles"] = {
            "title": "مجموعات الصلاحيات", "view": "security.roles.view",
            "make": lambda: RolesPage(self.permission_service, self.sync_service),
        }
        factories["user_permissions"] = {
            "title": "صلاحيات المستخدم", "view": "security.user_permissions.view",
            "make": lambda: UserPermissionsPage(self.auth_service, self.permission_service),
        }
        factories["crm_dashboard"] = {
            "title": "داشبورد",
            "view": "dashboard.crm.view",
            "make": lambda: CrmDashboardPage(),
        }
        factories["saudi_sales_invoices"] = {
            "title": "فاتورة المبيعات السعودية",
            "view": "sales.saudi_sales_invoices.view",
            "make": lambda: SaudiSalesInvoicePage(),
        }
        factories["cashier"] = {
            "title": "الكاشير / نقطة البيع",
            "view": "sales.cashier.view",
            "make": lambda: CashierPage(),
        }
        factories["attachments"] = {
            "title": "المرفقات",
            "view": "crm.attachments.view",
            "make": lambda: AttachmentsPage(),
        }
        factories["backup"] = {
            "title": "إدارة النسخ الاحتياطي",
            "view": "backup.view",
            "make": lambda: BackupManagementScreen(),
        }
        factories["connection_settings"] = {
            "title": "إعدادات الاتصال",
            "view": "connection.settings.view",
            "make": lambda: ConnectionSettingsScreen(),
        }
        for key, label, cls in REPORTS:
            factories[key] = {
                "title": label,
                "view": f"reports.{key}.view",
                "make": (lambda c=cls: c()),
            }
        return factories

    def _can_view(self, key: str) -> bool:
        # The home dashboard, backup, and connection settings are always available.
        if key in ("crm_dashboard", "backup", "connection_settings"):
            return True
        return SESSION.can(self._factories[key]["view"])

    # --- layout -------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())  # right side under RTL
        layout.addWidget(self._build_content_area(), 1)
        self.setCentralWidget(central)
        self._highlight("dashboard")  # home dashboard is selected by default

    PREWARM_ORDER = ("daily_followups", "customers", "products", "companies", "receipt_vouchers", "employees", "places", "case_statuses")

    def prewarm_all(self, progress: Callable[[int, int, str], None] | None = None) -> None:
        """Pre-build the CRM data screens the user may view so they open fast."""
        keys = [
            k for k in self.PREWARM_ORDER
            if self._can_view(k) and k not in HIDDEN_NAV_KEYS
        ]
        total = len(keys)
        for index, key in enumerate(keys, start=1):
            if progress is not None:
                progress(index, total, TABLE_SPECS[key].title)
                QApplication.processEvents()
            if key not in self.open_windows:
                try:
                    self._build_window(key)
                except Exception:
                    pass
        if progress is not None:
            progress(total or 1, total or 1, "")
            QApplication.processEvents()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setFixedWidth(245)
        sidebar.setStyleSheet(f"QFrame {{ background:{GREEN_DARK}; }}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(6)

        brand = QLabel("إدارة الفواتير الإلكترونية")
        brand.setAlignment(Qt.AlignCenter)
        brand.setStyleSheet(
            "color:#FFFFFF; font-size:18px; font-weight:900; background:transparent; padding:6px 0 14px 0;"
        )
        layout.addWidget(brand)

        # Home dashboard button (always visible — it is the central home page).
        dashboard_button = self._make_home_button("لوحة التحكم", "🏠")
        dashboard_button.clicked.connect(self.show_dashboard)
        self.nav_buttons.append(dashboard_button)
        self._nav_by_key["dashboard"] = dashboard_button
        layout.addWidget(dashboard_button)

        crm_dashboard_button = self._make_home_button("داشبورد", "📊")
        crm_dashboard_button.clicked.connect(lambda _c=False: self.open_screen("crm_dashboard"))
        self.nav_buttons.append(crm_dashboard_button)
        self._nav_by_key["crm_dashboard"] = crm_dashboard_button
        layout.addWidget(crm_dashboard_button)

        # CRM data screens (hidden when the user lacks view permission, or when
        # the screen is in HIDDEN_NAV_KEYS — those are removed from the sidebar
        # entirely rather than just made invisible).
        for key, label, _cls in MODULES:
            if key in HIDDEN_NAV_KEYS:
                continue
            button = self._make_nav_button(key, label)
            button.clicked.connect(lambda _c=False, k=key: self.open_screen(k))
            button.setVisible(self._can_view(key))
            self.nav_buttons.append(button)
            self._nav_by_key[key] = button
            layout.addWidget(button)

        # Saudi Phase-2 sales invoice (custom page on the sales_invoice* tables).
        saudi_button = self._make_icon_nav_button("🧾", "فاتورة مبيعات سعودية")
        saudi_button.clicked.connect(lambda _c=False: self.open_screen("saudi_sales_invoices"))
        saudi_button.setVisible(self._can_view("saudi_sales_invoices"))
        self.nav_buttons.append(saudi_button)
        self._nav_by_key["saudi_sales_invoices"] = saudi_button
        layout.addWidget(saudi_button)

        # Experimental Cashier / POS (custom page on the cashier_invoice* tables).
        cashier_button = self._make_icon_nav_button("🛒", "الكاشير")
        cashier_button.clicked.connect(lambda _c=False: self.open_screen("cashier"))
        cashier_button.setVisible(self._can_view("cashier"))
        self.nav_buttons.append(cashier_button)
        self._nav_by_key["cashier"] = cashier_button
        layout.addWidget(cashier_button)

        # Attachments screen (custom page, not a TableSpec CRUD screen). Hidden
        # from the sidebar per user request (still registered/permissioned).
        if "attachments" not in HIDDEN_NAV_KEYS:
            attachments_button = self._make_icon_nav_button("📎", "المرفقات")
            attachments_button.clicked.connect(lambda _c=False: self.open_screen("attachments"))
            attachments_button.setVisible(self._can_view("attachments"))
            self.nav_buttons.append(attachments_button)
            self._nav_by_key["attachments"] = attachments_button
            layout.addWidget(attachments_button)

        # Backup Management screen (always available).
        backup_button = self._make_icon_nav_button("🗄", "النسخ الاحتياطي")
        backup_button.clicked.connect(lambda _c=False: self.open_screen("backup"))
        backup_button.setVisible(self._can_view("backup"))
        self.nav_buttons.append(backup_button)
        self._nav_by_key["backup"] = backup_button
        layout.addWidget(backup_button)

        # Connection settings (switch LAN/Cloud, change host/port later).
        connection_button = self._make_icon_nav_button("🌐", "إعدادات الاتصال")
        connection_button.clicked.connect(lambda _c=False: self.open_screen("connection_settings"))
        connection_button.setVisible(self._can_view("connection_settings"))
        self.nav_buttons.append(connection_button)
        self._nav_by_key["connection_settings"] = connection_button
        layout.addWidget(connection_button)

        # Collapsible Reports group.
        self._build_reports_group(layout)

        # Collapsible "Users & Permissions" group.
        self._build_security_group(layout)

        layout.addStretch(1)
        version = QLabel("")
        version.setStyleSheet("color:#BBF7D0; font-size:11px; background:transparent;")
        layout.addWidget(version)
        return sidebar

    def _build_security_group(self, layout: QVBoxLayout) -> None:
        visible_children = [(k, lbl) for k, lbl in SECURITY_CHILDREN if self._can_view(k)]
        if not visible_children:
            self.security_parent = None
            return

        self.security_parent = self._make_parent_button("المستخدمين والصلاحيات")
        self.security_parent.clicked.connect(self._toggle_security_group)
        layout.addWidget(self.security_parent)

        self.security_child_buttons: list[QPushButton] = []
        for key, label in visible_children:
            child = self._make_child_button(label)
            child.clicked.connect(lambda _c=False, k=key: self.open_screen(k))
            child.setVisible(self._security_expanded)
            self.security_child_buttons.append(child)
            self._nav_by_key[key] = child
            layout.addWidget(child)

    def _build_reports_group(self, layout: QVBoxLayout) -> None:
        visible_reports = [(k, lbl) for k, lbl, _cls in REPORTS if self._can_view(k)]
        if not visible_reports:
            self.reports_parent = None
            return

        self.reports_parent = self._make_parent_button("Reports")
        self.reports_parent.clicked.connect(self._toggle_reports_group)
        layout.addWidget(self.reports_parent)

        self.report_child_buttons: list[QPushButton] = []
        for key, label in visible_reports:
            child = self._make_child_button(label)
            child.clicked.connect(lambda _c=False, k=key: self.open_screen(k))
            child.setVisible(self._reports_expanded)
            self.report_child_buttons.append(child)
            self._nav_by_key[key] = child
            layout.addWidget(child)

    def _toggle_security_group(self) -> None:
        self._security_expanded = not self._security_expanded
        for child in getattr(self, "security_child_buttons", []):
            child.setVisible(self._security_expanded)
        self._update_parent_arrow()

    def _toggle_reports_group(self) -> None:
        self._reports_expanded = not self._reports_expanded
        for child in getattr(self, "report_child_buttons", []):
            child.setVisible(self._reports_expanded)
        self._update_reports_parent_arrow()

    def _update_parent_arrow(self) -> None:
        if self.security_parent is None:
            return
        arrow = "▾" if self._security_expanded else "▸"
        self.security_parent.setText(f"{arrow}   المستخدمين والصلاحيات")

    def _update_reports_parent_arrow(self) -> None:
        if self.reports_parent is None:
            return
        arrow = "▾" if self._reports_expanded else "▸"
        self.reports_parent.setText(f"{arrow}   Reports")

    # --- button factories ---------------------------------------------------
    def _register_style(self, button: QPushButton, base: str, active: str) -> None:
        self._button_styles[button] = (base, active)
        button.setStyleSheet(base)

    def _make_nav_button(self, key: str, label: str) -> QPushButton:
        spec = TABLE_SPECS[key]
        icon = NAV_ICONS.get(key, spec.icon_text)
        button = QPushButton(f"{icon}   {label}")
        button.setLayoutDirection(Qt.LeftToRight)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(44)
        base = (
            "QPushButton { text-align:left; color:#E8FBEF; background:transparent; border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:800; }"
            "QPushButton:hover { background:rgba(255,255,255,0.10); }"
        )
        active = (
            "QPushButton { text-align:left; color:#0F6B30; background:#FFFFFF; border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:900; }"
        )
        self._register_style(button, base, active)
        return button

    def _make_icon_nav_button(self, icon_text: str, label: str) -> QPushButton:
        """Sidebar nav button for custom pages without a TableSpec entry."""
        button = QPushButton(f"{icon_text}   {label}")
        button.setLayoutDirection(Qt.LeftToRight)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(44)
        base = (
            "QPushButton { text-align:left; color:#E8FBEF; background:transparent; border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:800; }"
            "QPushButton:hover { background:rgba(255,255,255,0.10); }"
        )
        active = (
            "QPushButton { text-align:left; color:#0F6B30; background:#FFFFFF; border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:900; }"
        )
        self._register_style(button, base, active)
        return button

    def _make_home_button(self, label: str, icon: str = "🏠") -> QPushButton:
        button = QPushButton(f"{icon}   {label}")
        button.setLayoutDirection(Qt.LeftToRight)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(44)
        base = (
            "QPushButton { text-align:left; color:#E8FBEF; background:transparent; border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:800; }"
            "QPushButton:hover { background:rgba(255,255,255,0.10); }"
        )
        active = (
            "QPushButton { text-align:left; color:#0F6B30; background:#FFFFFF; border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:900; }"
        )
        self._register_style(button, base, active)
        return button

    def _make_parent_button(self, label: str) -> QPushButton:
        button = QPushButton(f"▸   {label}")
        button.setLayoutDirection(Qt.LeftToRight)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(46)
        button.setStyleSheet(
            "QPushButton { text-align:left; color:#FFFFFF; background:rgba(255,255,255,0.06); border:none; "
            "border-radius:8px; padding:8px 14px; font-size:15px; font-weight:900; }"
            "QPushButton:hover { background:rgba(255,255,255,0.14); }"
        )
        return button

    def _make_child_button(self, label: str) -> QPushButton:
        button = QPushButton(f"•   {label}")
        button.setLayoutDirection(Qt.LeftToRight)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(40)
        base = (
            "QPushButton { text-align:left; color:#D6F5E0; background:transparent; border:none; "
            "border-radius:8px; padding:6px 14px 6px 34px; font-size:14px; font-weight:700; }"
            "QPushButton:hover { background:rgba(255,255,255,0.10); }"
        )
        active = (
            "QPushButton { text-align:left; color:#0F6B30; background:#FFFFFF; border:none; "
            "border-radius:8px; padding:6px 14px 6px 34px; font-size:14px; font-weight:900; }"
        )
        self._register_style(button, base, active)
        return button

    def _build_content_area(self) -> QWidget:
        """Central content area: hosts the home dashboard by default.

        The dashboard lives embedded here (left of the sidebar under RTL); the
        CRM data screens still open in their own maximized windows, which is the
        app's existing intentional behaviour.
        """
        area = QWidget()
        area.setStyleSheet("background:#F1F5F9;")
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        try:
            self.dashboard_page = DashboardPage()
            layout.addWidget(self.dashboard_page)
        except Exception as exc:  # never block the app if the dashboard fails
            self.dashboard_page = None
            hint = QLabel(f"تعذّر تحميل لوحة التحكم:\n{exc}")
            hint.setAlignment(Qt.AlignCenter)
            hint.setStyleSheet(
                "color:#94A3B8; font-size:15px; font-weight:700; background:transparent;"
            )
            layout.addStretch(1)
            layout.addWidget(hint)
            layout.addStretch(1)
        return area

    def show_dashboard(self) -> None:
        """Bring the home dashboard to the foreground and refresh its data."""
        if getattr(self, "dashboard_page", None) is not None:
            self.dashboard_page.refresh_dashboard()
        self._highlight("dashboard")

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() == QEvent.WindowActivate:
            dashboard = getattr(self, "dashboard_page", None)
            if dashboard is not None:
                dashboard.refresh_dashboard()
        return result

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt close event
        """Run the shutdown backup (if enabled) before the window closes.

        Fully guarded: a backup failure — or the backup module being unavailable
        — must never prevent the application from closing.
        """
        try:
            from app.services.backup_service import BackupService

            user = SESSION.user or {}
            created_by = user.get("id")
            try:
                created_by = int(created_by) if created_by is not None else None
            except (TypeError, ValueError):
                created_by = None
            BackupService().run_shutdown_backup(created_by=created_by)
        except Exception:
            pass
        super().closeEvent(event)

    # --- open / highlight ---------------------------------------------------
    def _build_window(self, key: str) -> QWidget:
        spec = self._factories[key]
        window = spec["make"]()
        window.setWindowTitle(f"KSA - {spec['title']}")
        window.setLayoutDirection(Qt.RightToLeft)
        window.destroyed.connect(lambda *_a, k=key: self.open_windows.pop(k, None))
        self.open_windows[key] = window
        return window

    def open_screen(self, key: str) -> None:
        if not self._can_view(key):
            QMessageBox.warning(self, "غير مسموح", "ليس لديك صلاحية لفتح هذه الشاشة.")
            return
        try:
            window = self.open_windows.get(key)
            is_existing_window = window is not None
            if window is None:
                window = self._build_window(key)
        except Exception as exc:
            QMessageBox.critical(self, "تعذر فتح الشاشة", str(exc))
            return
        if is_existing_window:
            refresh = getattr(window, "refresh_dashboard", None)
            if callable(refresh):
                refresh()
        window.showMaximized()
        window.raise_()
        window.activateWindow()
        self._highlight(key)

    def _highlight(self, key: str) -> None:
        for button, (base, active) in self._button_styles.items():
            button.setStyleSheet(base)
        button = self._nav_by_key.get(key)
        if button is not None and button in self._button_styles:
            button.setStyleSheet(self._button_styles[button][1])
