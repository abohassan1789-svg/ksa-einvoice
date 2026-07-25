"""Permission business logic: effective resolution, matrices, grant editing.

Effective permission rules (most specific wins):
    1. Admin / full-access users        -> everything allowed.
    2. Otherwise start from the role     -> role_permissions.allowed.
    3. A user override (allow/deny)       -> replaces the role value.
       No user override row              -> inherit the role value.
"""

from __future__ import annotations

from typing import Any

from app.repositories.security_repository import SecurityRepository
from app.services.permission_registry import (
    REPORT_ACTIONS,
    SCREEN_ACTIONS,
)

INHERIT, ALLOW, DENY = "inherit", "allow", "deny"


def resolve_effective(
    permissions: list[dict[str, Any]],
    role_map: dict[int, bool],
    user_map: dict[int, bool],
    is_full_access: bool,
) -> dict[int, dict[str, Any]]:
    """Pure resolver (no DB) — returns {permission_id: {allowed, source}}."""
    result: dict[int, dict[str, Any]] = {}
    for perm in permissions:
        pid = int(perm["id"])
        if is_full_access:
            result[pid] = {"allowed": True, "source": "full_access"}
            continue
        base = bool(role_map.get(pid, False))
        if pid in user_map:
            result[pid] = {"allowed": bool(user_map[pid]), "source": "override"}
        else:
            result[pid] = {"allowed": base, "source": "role"}
    return result


class PermissionService:
    def __init__(self, repository: SecurityRepository | None = None) -> None:
        self.repository = repository or SecurityRepository()

    # --- resolution ---------------------------------------------------------
    def resolve_user(self, user: dict[str, Any]) -> dict[int, dict[str, Any]]:
        permissions = self.repository.list_permissions(active_only=True)
        is_full = bool(user.get("is_admin") or user.get("full_access"))
        role_map = self.repository.get_role_permission_map(user["role_id"]) if user.get("role_id") else {}
        user_map = self.repository.get_user_permission_map(user["id"])
        return resolve_effective(permissions, role_map, user_map, is_full)

    def effective_codes(self, user: dict[str, Any]) -> set[str]:
        permissions = self.repository.list_permissions(active_only=True)
        by_id = {int(p["id"]): p for p in permissions}
        resolved = self.resolve_user(user)
        return {by_id[pid]["permission_code"] for pid, info in resolved.items() if info["allowed"]}

    def can(self, user: dict[str, Any] | None, permission_code: str) -> bool:
        if not user:
            return False
        if user.get("is_admin") or user.get("full_access"):
            return True
        return permission_code in self.effective_codes(user)

    # --- matrix for UI ------------------------------------------------------
    def actions_for_type(self, permission_type: str) -> list[tuple[str, str, str]]:
        return list(REPORT_ACTIONS if permission_type == "report" else SCREEN_ACTIONS)

    def build_matrix(self, scope: str = "all") -> list[dict[str, Any]]:
        """Group active permissions into matrix rows (one per screen/report).

        scope: 'all' | 'screen' | 'report'. Each row carries the permission id
        for every action so the UI can map a cell to a permission.
        """
        permissions = self.repository.list_permissions(active_only=True)
        rows: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for perm in permissions:
            ptype = perm["permission_type"]
            if scope in ("screen", "report") and ptype != scope:
                continue
            key = f"{perm['module_code']}.{perm['target_code']}"
            if key not in rows:
                rows[key] = {
                    "key": key,
                    "permission_type": ptype,
                    "module_code": perm["module_code"],
                    "module_name_ar": perm["module_name_ar"],
                    "target_code": perm["target_code"],
                    "target_name_ar": perm["target_name_ar"],
                    "target_name_en": perm["target_name_en"],
                    "actions": {},  # action_code -> permission_id
                }
                order.append(key)
            rows[key]["actions"][perm["action_code"]] = int(perm["id"])
        return [rows[k] for k in order]

    # --- roles --------------------------------------------------------------
    def list_roles(self, keyword: str = "", active_only: bool = False) -> list[dict[str, Any]]:
        return self.repository.list_roles(keyword, active_only)

    def get_role(self, role_id: int) -> dict[str, Any] | None:
        return self.repository.get_role(role_id)

    def create_role(self, data: dict[str, Any]) -> int:
        if not (data.get("role_code") or "").strip():
            raise ValueError("كود المجموعة مطلوب")
        return self.repository.create_role(data)

    def update_role(self, role_id: int, data: dict[str, Any]) -> None:
        self.repository.update_role(role_id, data)

    def set_role_active(self, role_id: int, active: bool) -> None:
        self.repository.set_role_active(role_id, active)

    # --- grant editing (delegates to repository) ----------------------------
    def get_role_permission_map(self, role_id: int) -> dict[int, bool]:
        return self.repository.get_role_permission_map(role_id)

    def save_role_permissions(self, role_id: int, allowed_map: dict[int, bool]) -> None:
        self.repository.save_role_permissions(role_id, allowed_map)

    def get_user_permission_map(self, user_id: int) -> dict[int, bool]:
        return self.repository.get_user_permission_map(user_id)

    def set_user_override(self, user_id: int, permission_id: int, state: str) -> None:
        """state: 'inherit' | 'allow' | 'deny'."""
        mapping = {INHERIT: None, ALLOW: True, DENY: False}
        self.repository.set_user_permission(user_id, permission_id, mapping[state])

    def reset_user_overrides(self, user_id: int) -> None:
        self.repository.reset_user_overrides(user_id)
