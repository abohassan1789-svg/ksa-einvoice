"""Database access for the security schema (users/roles/permissions/logs).

All SQL for the Users & Permissions system lives here; services and UI never
touch the database directly. Built on the shared psycopg ``Database`` helper.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database
from app.models.security_models import SECURITY_SCHEMA_SQL


class SecurityRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the security tables if they do not exist (safe to repeat)."""
        self.db.execute_script(SECURITY_SCHEMA_SQL)

    # --- roles --------------------------------------------------------------
    def list_roles(self, keyword: str = "", active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM roles"
        clauses, params = [], []
        if active_only:
            clauses.append("is_active = true")
        if keyword.strip():
            clauses.append("(role_code ILIKE %s OR role_name_ar ILIKE %s OR role_name_en ILIKE %s)")
            like = f"%{keyword.strip()}%"
            params += [like, like, like]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY role_name_ar NULLS LAST, id"
        return self.db.fetch_all(sql, params)

    def get_role(self, role_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM roles WHERE id = %s", [role_id])

    def create_role(self, data: dict[str, Any]) -> int:
        row = self.db.fetch_one(
            "INSERT INTO roles (role_code, role_name_ar, role_name_en, description, is_active) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            [data.get("role_code"), data.get("role_name_ar"), data.get("role_name_en"),
             data.get("description"), data.get("is_active", True)],
        )
        return int(row["id"])

    def update_role(self, role_id: int, data: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE roles SET role_code=%s, role_name_ar=%s, role_name_en=%s, description=%s, "
            "is_active=%s, updated_at=now() WHERE id=%s",
            [data.get("role_code"), data.get("role_name_ar"), data.get("role_name_en"),
             data.get("description"), data.get("is_active", True), role_id],
        )

    def set_role_active(self, role_id: int, active: bool) -> None:
        self.db.execute("UPDATE roles SET is_active=%s, updated_at=now() WHERE id=%s", [active, role_id])

    # --- users --------------------------------------------------------------
    def list_users(self, keyword: str = "") -> list[dict[str, Any]]:
        sql = (
            "SELECT u.*, r.role_name_ar AS role_name_ar, r.role_name_en AS role_name_en "
            "FROM app_users u LEFT JOIN roles r ON r.id = u.role_id"
        )
        params: list[Any] = []
        if keyword.strip():
            sql += " WHERE (u.username ILIKE %s OR u.full_name ILIKE %s OR u.email ILIKE %s OR u.phone ILIKE %s)"
            like = f"%{keyword.strip()}%"
            params += [like, like, like, like]
        sql += " ORDER BY u.username"
        return self.db.fetch_all(sql, params)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM app_users WHERE id = %s", [user_id])

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM app_users WHERE lower(username) = lower(%s)", [username])

    def create_user(self, data: dict[str, Any]) -> int:
        row = self.db.fetch_one(
            "INSERT INTO app_users (username, password_hash, full_name, email, phone, role_id, "
            "language, is_active, is_admin, full_access, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            [data.get("username"), data.get("password_hash"), data.get("full_name"),
             data.get("email"), data.get("phone"), data.get("role_id"),
             data.get("language", "ar"), data.get("is_active", True),
             data.get("is_admin", False), data.get("full_access", False), data.get("notes")],
        )
        return int(row["id"])

    def update_user(self, user_id: int, data: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE app_users SET full_name=%s, email=%s, phone=%s, role_id=%s, language=%s, "
            "is_active=%s, is_admin=%s, full_access=%s, notes=%s, updated_at=now() WHERE id=%s",
            [data.get("full_name"), data.get("email"), data.get("phone"), data.get("role_id"),
             data.get("language", "ar"), data.get("is_active", True), data.get("is_admin", False),
             data.get("full_access", False), data.get("notes"), user_id],
        )

    def update_password(self, user_id: int, password_hash: str) -> None:
        self.db.execute(
            "UPDATE app_users SET password_hash=%s, updated_at=now() WHERE id=%s",
            [password_hash, user_id],
        )

    def set_user_active(self, user_id: int, active: bool) -> None:
        self.db.execute("UPDATE app_users SET is_active=%s, updated_at=now() WHERE id=%s", [active, user_id])

    def update_last_login(self, user_id: int) -> None:
        self.db.execute("UPDATE app_users SET last_login_at=now() WHERE id=%s", [user_id])

    def count_active_admins(self, exclude_user_id: int | None = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM app_users WHERE is_active = true AND (is_admin = true OR full_access = true)"
        params: list[Any] = []
        if exclude_user_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_user_id)
        return int(self.db.fetch_one(sql, params)["c"])

    # --- permissions --------------------------------------------------------
    def list_permissions(self, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM permissions"
        if active_only:
            sql += " WHERE is_active = true"
        sql += " ORDER BY permission_type, module_code, target_code, id"
        return self.db.fetch_all(sql)

    def upsert_permission(self, row: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO permissions (permission_code, permission_type, module_code, module_name_ar, "
            "module_name_en, category_ar, category_en, target_code, target_name_ar, target_name_en, "
            "action_code, action_name_ar, action_name_en, is_active) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) "
            "ON CONFLICT (permission_code) DO UPDATE SET "
            "permission_type=EXCLUDED.permission_type, module_code=EXCLUDED.module_code, "
            "module_name_ar=EXCLUDED.module_name_ar, module_name_en=EXCLUDED.module_name_en, "
            "category_ar=EXCLUDED.category_ar, category_en=EXCLUDED.category_en, "
            "target_code=EXCLUDED.target_code, target_name_ar=EXCLUDED.target_name_ar, "
            "target_name_en=EXCLUDED.target_name_en, action_code=EXCLUDED.action_code, "
            "action_name_ar=EXCLUDED.action_name_ar, action_name_en=EXCLUDED.action_name_en, "
            "is_active=true, updated_at=now()",
            [row["permission_code"], row["permission_type"], row["module_code"], row["module_name_ar"],
             row["module_name_en"], row["category_ar"], row["category_en"], row["target_code"],
             row["target_name_ar"], row["target_name_en"], row["action_code"], row["action_name_ar"],
             row["action_name_en"]],
        )

    def deactivate_permissions_not_in(self, active_codes: list[str]) -> int:
        """Mark permissions whose code is no longer registered as inactive.

        Never deletes — only flips is_active to false.
        """
        if active_codes:
            cur = self.db.fetch_all(
                "UPDATE permissions SET is_active=false, updated_at=now() "
                "WHERE is_active=true AND permission_code <> ALL(%s) RETURNING id",
                [active_codes],
            )
        else:
            cur = self.db.fetch_all(
                "UPDATE permissions SET is_active=false, updated_at=now() WHERE is_active=true RETURNING id",
            )
        return len(cur)

    # --- role_permissions ---------------------------------------------------
    def get_role_permission_map(self, role_id: int) -> dict[int, bool]:
        rows = self.db.fetch_all(
            "SELECT permission_id, allowed FROM role_permissions WHERE role_id = %s", [role_id]
        )
        return {int(r["permission_id"]): bool(r["allowed"]) for r in rows}

    def set_role_permission(self, role_id: int, permission_id: int, allowed: bool) -> None:
        self.db.execute(
            "INSERT INTO role_permissions (role_id, permission_id, allowed) VALUES (%s,%s,%s) "
            "ON CONFLICT (role_id, permission_id) DO UPDATE SET allowed=EXCLUDED.allowed, updated_at=now()",
            [role_id, permission_id, allowed],
        )

    def save_role_permissions(self, role_id: int, allowed_map: dict[int, bool]) -> None:
        for permission_id, allowed in allowed_map.items():
            self.set_role_permission(role_id, int(permission_id), bool(allowed))

    # --- user_permissions ---------------------------------------------------
    def get_user_permission_map(self, user_id: int) -> dict[int, bool]:
        rows = self.db.fetch_all(
            "SELECT permission_id, allowed FROM user_permissions WHERE user_id = %s", [user_id]
        )
        return {int(r["permission_id"]): bool(r["allowed"]) for r in rows}

    def set_user_permission(self, user_id: int, permission_id: int, allowed: bool | None) -> None:
        """allowed True/False = override; None = inherit (row removed)."""
        if allowed is None:
            self.db.execute(
                "DELETE FROM user_permissions WHERE user_id=%s AND permission_id=%s",
                [user_id, permission_id],
            )
            return
        self.db.execute(
            "INSERT INTO user_permissions (user_id, permission_id, allowed) VALUES (%s,%s,%s) "
            "ON CONFLICT (user_id, permission_id) DO UPDATE SET allowed=EXCLUDED.allowed, updated_at=now()",
            [user_id, permission_id, allowed],
        )

    def reset_user_overrides(self, user_id: int) -> None:
        self.db.execute("DELETE FROM user_permissions WHERE user_id=%s", [user_id])

    # --- login logs ---------------------------------------------------------
    def insert_login_log(self, user_id: int | None, username: str, status: str,
                         ip_address: str | None = None, device_name: str | None = None,
                         notes: str | None = None) -> int:
        row = self.db.fetch_one(
            "INSERT INTO user_login_logs (user_id, username, status, ip_address, device_name, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            [user_id, username, status, ip_address, device_name, notes],
        )
        return int(row["id"])

    def set_logout(self, log_id: int) -> None:
        self.db.execute("UPDATE user_login_logs SET logout_time=now() WHERE id=%s", [log_id])

    def list_user_logins(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM user_login_logs WHERE user_id=%s ORDER BY login_time DESC LIMIT %s",
            [user_id, limit],
        )
