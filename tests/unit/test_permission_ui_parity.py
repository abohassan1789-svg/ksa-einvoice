"""The permissions matrix must list exactly what the UI can actually open.

The user's rule (2026-07-15): «البنود الموجودة في الواجهة حالياً والظاهرة
للمستخدم هي نفسها التي يتم التحكم بها وتظهر في الصلاحيات». Ten targets had
drifted out of step — five screens hidden from the sidebar (employees,
daily_followups, places, case_statuses, attachments) and five reports with no
live entry in ``main_window.REPORTS`` — all still offering a full row of
checkboxes in مجموعات الصلاحيات that could never affect anything.

Keeping them in step by hand is exactly the shape of bug this repo keeps paying
for (the invoice template dispatch, the voucher builder map): two lists, no
link, silent when they diverge. So it is a test instead.

Both directions matter, and the second is the sharp one: ``can()`` resolves
against ACTIVE permissions only, so a target in ``HIDDEN_TARGET_CODES`` is
denied to every non-admin. Un-hiding a sidebar button without un-hiding its
permission gives you a button that is invisible to everyone except Admin — and
looks like a permissions bug, not a registry one.

Pure registry/constant reads: no database, no Chromium, no QApplication.
"""

from __future__ import annotations

import pytest

from app.services.permission_registry import (
    HIDDEN_TARGET_CODES,
    collect_targets,
)


def _ui_visible_codes() -> set[str]:
    """Every target_code a user can actually reach from the sidebar."""
    from app.ui.main_window import HIDDEN_NAV_KEYS, MODULES, REPORTS

    codes = {key for key, _label, _cls in MODULES} - HIDDEN_NAV_KEYS
    codes |= {key for key, _label, _cls in REPORTS}
    # Nav buttons built outside MODULES/REPORTS. Their target_codes match their
    # runtime keys by the registry's own contract (see SCREEN_TARGETS).
    codes |= {"saudi_sales_invoices", "cashier", "users", "roles", "user_permissions"}
    return codes


def test_every_permission_target_is_reachable_in_the_ui() -> None:
    """No permission row for a screen/report nobody can open."""
    orphans = {t["target_code"] for t in collect_targets()} - _ui_visible_codes()
    assert not orphans, (
        f"{len(orphans)} permission target(s) are not reachable from the sidebar: "
        f"{sorted(orphans)}. Either add them to HIDDEN_TARGET_CODES or give them "
        f"a nav entry — a permission nobody can exercise is noise that invites "
        f"granting access which does nothing."
    )


def test_every_visible_ui_screen_has_a_permission_target() -> None:
    """The sharp direction: a visible button whose permission was hidden.

    ``can()`` only resolves ACTIVE permissions, so such a button is invisible to
    every non-admin while looking perfectly fine to whoever ships it.
    """
    missing = _ui_visible_codes() - {t["target_code"] for t in collect_targets()}
    assert not missing, (
        f"{len(missing)} screen(s) are in the sidebar but have no active "
        f"permission target: {sorted(missing)}. Non-admins will silently never "
        f"see them. Remove them from HIDDEN_TARGET_CODES, or from the sidebar."
    )


def test_hidden_codes_are_really_registered() -> None:
    """A typo'd code in HIDDEN_TARGET_CODES hides nothing and says nothing."""
    all_codes = {t["target_code"] for t in collect_targets(include_hidden=True)}
    unknown = HIDDEN_TARGET_CODES - all_codes
    assert not unknown, (
        f"HIDDEN_TARGET_CODES names {sorted(unknown)}, which no screen or report "
        f"registers. Nothing is being hidden by those entries."
    )


def test_hiding_is_subtractive_only() -> None:
    """collect_targets() must filter, never reshape. Guards the sync contract.

    PermissionsSyncService deactivates whatever is absent from collect_targets(),
    so if hiding ever changed a *kept* target's code the sync would deactivate
    the real row and add a stranger — silently orphaning live grants.
    """
    shown = collect_targets()
    every = collect_targets(include_hidden=True)
    assert shown == [t for t in every if t["target_code"] not in HIDDEN_TARGET_CODES]
    assert len(shown) == len(every) - len(HIDDEN_TARGET_CODES)


@pytest.mark.parametrize(
    "code",
    ["employees", "daily_followups", "places", "case_statuses", "attachments",
     "daily_followup_report", "daily_followup_smart_report",
     "status_summary_report", "status_analysis_report", "sales_report"],
)
def test_the_ten_requested_targets_stay_hidden(code: str) -> None:
    """Pin the exact list the user approved, so a re-add is a deliberate act."""
    assert code not in {t["target_code"] for t in collect_targets()}
