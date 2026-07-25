"""Authentication and user-administration business logic.

Keeps all rules (duplicate username, last-active-admin protection, password
hashing, seeding the default Admin) out of the UI and out of the database layer.
"""

from __future__ import annotations

from typing import Any

from app.repositories.security_repository import SecurityRepository
from app.security.password_hasher import PasswordHasher

# Default administrator seeded on first run.
DEFAULT_ADMIN_USERNAME = "Admin"
DEFAULT_ADMIN_PASSWORD = "1"


class AuthError(Exception):
    """Raised for user-facing authentication/administration errors."""


class AuthService:
    def __init__(
        self,
        repository: SecurityRepository | None = None,
        hasher: PasswordHasher | None = None,
    ) -> None:
        self.repository = repository or SecurityRepository()
        self.hasher = hasher or PasswordHasher()

    # --- bootstrap ----------------------------------------------------------
    def seed_admin(self) -> None:
        """Ensure a default full-access Admin user exists (Admin / 1)."""
        self.repository.ensure_schema()
        existing = self.repository.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        if existing is not None:
            return
        self.repository.create_user({
            "username": DEFAULT_ADMIN_USERNAME,
            "password_hash": self.hasher.hash(DEFAULT_ADMIN_PASSWORD),
            "full_name": "مدير النظام",
            "language": "ar",
            "is_active": True,
            "is_admin": True,
            "full_access": True,
            "notes": "حساب المدير الافتراضي",
        })

    # --- authentication -----------------------------------------------------
    def authenticate(self, username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
        user = self.repository.get_user_by_username(username or "")
        if user is None:
            return None, "اسم المستخدم غير موجود"
        if not user.get("is_active"):
            return None, "هذا المستخدم موقوف، تواصل مع المدير"
        if not self.hasher.verify(password or "", user.get("password_hash")):
            return None, "كلمة المرور غير صحيحة"
        return user, None

    def record_login(self, user: dict[str, Any], device_name: str | None = None) -> int:
        self.repository.update_last_login(user["id"])
        return self.repository.insert_login_log(
            user_id=user["id"], username=user["username"], status="success", device_name=device_name
        )

    def record_failed_login(self, username: str, reason: str) -> None:
        self.repository.insert_login_log(user_id=None, username=username, status="failed", notes=reason)

    def record_logout(self, log_id: int | None) -> None:
        if log_id is not None:
            self.repository.set_logout(log_id)

    # --- user administration ------------------------------------------------
    def list_users(self, keyword: str = "") -> list[dict[str, Any]]:
        return self.repository.list_users(keyword)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.repository.get_user(user_id)

    def create_user(self, data: dict[str, Any], password: str) -> int:
        username = (data.get("username") or "").strip()
        if not username:
            raise AuthError("اسم المستخدم مطلوب")
        if not password:
            raise AuthError("كلمة المرور مطلوبة")
        if self.repository.get_user_by_username(username) is not None:
            raise AuthError("اسم المستخدم مستخدم بالفعل")
        payload = dict(data)
        payload["username"] = username
        payload["password_hash"] = self.hasher.hash(password)
        return self.repository.create_user(payload)

    def update_user(self, user_id: int, data: dict[str, Any], password: str | None = None) -> None:
        current = self.repository.get_user(user_id)
        if current is None:
            raise AuthError("المستخدم غير موجود")
        # Protect the last active admin from losing admin/active status.
        was_admin = current.get("is_admin") or current.get("full_access")
        will_be_admin = data.get("is_admin") or data.get("full_access")
        will_be_active = data.get("is_active", True)
        if was_admin and current.get("is_active") and (not will_be_admin or not will_be_active):
            if self.repository.count_active_admins(exclude_user_id=user_id) == 0:
                raise AuthError("لا يمكن إلغاء آخر مستخدم مدير فعّال")
        self.repository.update_user(user_id, data)
        if password:
            self.repository.update_password(user_id, self.hasher.hash(password))

    def set_user_active(self, user_id: int, active: bool) -> None:
        current = self.repository.get_user(user_id)
        if current is None:
            raise AuthError("المستخدم غير موجود")
        if not active and (current.get("is_admin") or current.get("full_access")):
            if self.repository.count_active_admins(exclude_user_id=user_id) == 0:
                raise AuthError("لا يمكن إيقاف آخر مستخدم مدير فعّال")
        self.repository.set_user_active(user_id, active)

    def list_user_logins(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.list_user_logins(user_id, limit)
