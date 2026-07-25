"""Unit tests for the dynamic permissions system (no database needed)."""

from app.security.password_hasher import PasswordHasher
from app.services.permission_registry import (
    REPORT_ACTIONS,
    SCREEN_ACTIONS,
    build_permission_rows,
    collect_targets,
    make_permission_code,
)
from app.services.permission_service import resolve_effective
from app.services.permissions_sync_service import PermissionsSyncService


def test_permission_code_format():
    assert make_permission_code("crm", "customers", "view") == "crm.customers.view"


def test_registry_includes_screens_reports_and_security():
    codes = {row["permission_code"] for row in build_permission_rows()}
    # screens (full screen actions)
    assert "crm.customers.view" in codes
    assert "crm.receipt_vouchers.delete" in codes
    assert "crm.companies.create" in codes
    assert "crm.products.export" in codes
    assert "sales.saudi_sales_invoices.view" in codes
    # security pages registered as permissions
    assert "security.users.view" in codes
    assert "security.roles.view" in codes
    assert "security.user_permissions.view" in codes
    # reports discovered dynamically with report actions
    assert "reports.customer_statement.preview" in codes
    assert "reports.customer_statement.export" in codes


def test_hidden_targets_are_excluded_from_the_rows_but_stay_registered():
    """Hidden ≠ unregistered — the distinction the sync depends on.

    The rows drive the sync, so a hidden target must be absent from them (that
    is what flips it to is_active=false and makes it disappear from the matrix).
    Its entry must nonetheless survive in the registry, or re-enabling it later
    would mean re-typing the whole target rather than deleting one line.
    See tests/unit/test_permission_ui_parity.py for the full rule.
    """
    codes = {row["permission_code"] for row in build_permission_rows()}
    for hidden in ("crm.daily_followups.delete", "crm.attachments.view",
                   "crm.employees.view", "crm.places.view", "crm.case_statuses.view",
                   "reports.sales_report.preview", "reports.daily_followup_report.export",
                   "reports.daily_followup_smart_report.export",
                   "reports.status_summary_report.view",
                   "reports.status_analysis_report.view"):
        assert hidden not in codes, f"{hidden} is hidden and must not sync as active"

    registered = {t["target_code"] for t in collect_targets(include_hidden=True)}
    assert {"daily_followups", "attachments", "employees", "places", "case_statuses",
            "sales_report", "daily_followup_report", "daily_followup_smart_report",
            "status_summary_report", "status_analysis_report"} <= registered


def test_every_screen_has_all_screen_actions():
    targets = [t for t in collect_targets() if t["permission_type"] == "screen"]
    expected = {code for code, _ar, _en in SCREEN_ACTIONS}
    for target in targets:
        assert set(target["actions"]) == expected


def test_every_report_has_all_report_actions():
    targets = [t for t in collect_targets() if t["permission_type"] == "report"]
    expected = {code for code, _ar, _en in REPORT_ACTIONS}
    assert targets, "expected at least one report target"
    for target in targets:
        assert set(target["actions"]) == expected


def test_resolver_role_base_then_override():
    perms = [
        {"id": 1, "permission_code": "a"},
        {"id": 2, "permission_code": "b"},
        {"id": 3, "permission_code": "c"},
        {"id": 4, "permission_code": "d"},
    ]
    role_map = {1: True, 2: False, 4: True}
    user_map = {2: True, 4: False}  # allow b, deny d
    resolved = resolve_effective(perms, role_map, user_map, is_full_access=False)
    assert resolved[1]["allowed"] is True and resolved[1]["source"] == "role"
    assert resolved[2]["allowed"] is True and resolved[2]["source"] == "override"
    assert resolved[3]["allowed"] is False and resolved[3]["source"] == "role"
    assert resolved[4]["allowed"] is False and resolved[4]["source"] == "override"


def test_resolver_full_access_allows_everything():
    perms = [{"id": 1, "permission_code": "a"}, {"id": 2, "permission_code": "b"}]
    resolved = resolve_effective(perms, {}, {}, is_full_access=True)
    assert all(info["allowed"] for info in resolved.values())
    assert all(info["source"] == "full_access" for info in resolved.values())


def test_password_hash_roundtrip_and_format():
    hasher = PasswordHasher(iterations=1000)
    stored = hasher.hash("secret")
    assert stored.startswith("pbkdf2_sha256$")
    assert stored.count("$") == 3
    assert hasher.verify("secret", stored) is True
    assert hasher.verify("wrong", stored) is False
    assert hasher.verify("secret", None) is False
    assert hasher.verify("secret", "garbage") is False


class _FakeSecurityRepo:
    """In-memory stand-in for SecurityRepository used by the sync test."""

    def __init__(self, existing_codes=None):
        self.perms = {}  # code -> row
        self.deactivated = []
        for code in existing_codes or []:
            self.perms[code] = {"permission_code": code, "is_active": True}

    def ensure_schema(self):
        pass

    def list_permissions(self, active_only=True):
        rows = list(self.perms.values())
        if active_only:
            rows = [r for r in rows if r.get("is_active", True)]
        return rows

    def upsert_permission(self, row):
        self.perms[row["permission_code"]] = {**row, "is_active": True}

    def deactivate_permissions_not_in(self, active_codes):
        active = set(active_codes)
        count = 0
        for code, row in self.perms.items():
            if code not in active and row.get("is_active", True):
                row["is_active"] = False
                self.deactivated.append(code)
                count += 1
        return count


def test_sync_inserts_new_keeps_existing_and_deactivates_removed():
    repo = _FakeSecurityRepo(existing_codes=["crm.customers.view", "crm.OLD.view"])
    summary = PermissionsSyncService(repo).sync()

    # The stale code not in the registry is deactivated, never deleted.
    assert "crm.OLD.view" in repo.deactivated
    assert "crm.OLD.view" in repo.perms
    assert repo.perms["crm.OLD.view"]["is_active"] is False

    # Existing registered code stays; new codes were added.
    assert repo.perms["crm.customers.view"]["is_active"] is True
    assert summary["added"] >= 1
    assert summary["total"] == len(build_permission_rows())
    assert summary["deactivated"] >= 1
