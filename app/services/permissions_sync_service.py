"""Dynamic permission synchronisation.

Scans the central registry (screens + reports + actions) and reconciles it with
the ``permissions`` table:

* inserts permissions that don't exist yet,
* updates the bilingual names if they changed,
* keeps every existing role/user permission value untouched,
* marks permissions that are no longer registered as ``is_active = false``
  (deprecated) instead of deleting them.

It never drops tables, deletes permissions, or resets role/user grants.
"""

from __future__ import annotations

from typing import Any

from app.repositories.security_repository import SecurityRepository
from app.services.permission_registry import build_permission_rows


class PermissionsSyncService:
    def __init__(self, repository: SecurityRepository | None = None) -> None:
        self.repository = repository or SecurityRepository()

    def sync(self) -> dict[str, Any]:
        """Reconcile registry → permissions table. Returns a summary."""
        self.repository.ensure_schema()

        existing = {p["permission_code"] for p in self.repository.list_permissions(active_only=False)}
        rows = build_permission_rows()
        active_codes = [r["permission_code"] for r in rows]

        added = 0
        for row in rows:
            if row["permission_code"] not in existing:
                added += 1
            self.repository.upsert_permission(row)

        deactivated = self.repository.deactivate_permissions_not_in(active_codes)

        return {
            "added": added,
            "updated": len(rows) - added,
            "total": len(rows),
            "deactivated": deactivated,
        }
