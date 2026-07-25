"""Current-session context used to enforce permissions across the UI.

Holds the logged-in user and their resolved (effective) permission codes. A
single module-level instance, :data:`SESSION`, is read by screens to decide what
to show/enable. When no user is logged in (e.g. unit tests or legacy launch
paths) it is *permissive* so existing screens keep working unchanged.
"""

from __future__ import annotations

from typing import Any


class SessionContext:
    def __init__(self) -> None:
        self.user: dict[str, Any] | None = None
        self._allowed: set[str] = set()
        self._loaded = False

    def set_session(self, user: dict[str, Any] | None, allowed_codes: set[str] | None) -> None:
        self.user = user
        self._allowed = set(allowed_codes or set())
        self._loaded = user is not None

    def clear(self) -> None:
        self.user = None
        self._allowed = set()
        self._loaded = False

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_admin(self) -> bool:
        return bool(self.user and (self.user.get("is_admin") or self.user.get("full_access")))

    def can(self, permission_code: str) -> bool:
        """Return True if the current user may perform ``permission_code``.

        Permissive fallbacks: if no session has been loaded (legacy/standalone
        launch) or the user has full access, everything is allowed.
        """
        if not self._loaded:
            return True
        if self.is_admin:
            return True
        return permission_code in self._allowed

    def can_any(self, permission_codes: list[str]) -> bool:
        return any(self.can(code) for code in permission_codes)


# Shared application session.
SESSION = SessionContext()
