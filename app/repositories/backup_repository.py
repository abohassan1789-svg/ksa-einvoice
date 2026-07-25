"""Database access for the Backup module (النسخ الاحتياطي).

All SQL for ``backup_logs`` lives here; the service and UI never touch the
database directly. Built on the shared psycopg ``Database`` helper, mirroring
``AttachmentsRepository`` / ``SecurityRepository``.

This layer only reads/writes *metadata* rows. It never creates, reads, or
deletes any backup ``.sql`` file — file I/O is the service's responsibility.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database
from app.models.backup_log import BACKUP_LOGS_SCHEMA_SQL, LIST_COLUMNS

_LIST_COLUMNS_SQL = ", ".join(LIST_COLUMNS)


class BackupRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the backup_logs table + indexes if missing (safe to repeat)."""
        self.db.execute_script(BACKUP_LOGS_SCHEMA_SQL)

    # --- writes -------------------------------------------------------------
    def insert_log(
        self,
        backup_type: str,
        file_name: str | None,
        file_path: str | None,
        status: str,
        error_message: str | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any] | None:
        """Insert one backup-log row and return its id + created_at."""
        return self.db.fetch_one(
            "INSERT INTO backup_logs "
            "(backup_type, file_name, file_path, status, error_message, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, created_at",
            [backup_type, file_name, file_path, status, error_message, created_by],
        )

    # --- reads --------------------------------------------------------------
    def list_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return backup-log rows, newest first."""
        return self.db.fetch_all(
            f"SELECT {_LIST_COLUMNS_SQL} FROM backup_logs ORDER BY id DESC LIMIT %s",
            [int(limit)],
        )

    def get_log(self, log_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"SELECT {_LIST_COLUMNS_SQL} FROM backup_logs WHERE id = %s", [log_id]
        )

    def has_successful_backup_today(self, backup_type: str) -> bool:
        """True if a *successful* backup of ``backup_type`` already ran today.

        "Today" is the current calendar day in the database session's timezone
        (``created_at >= date_trunc('day', now())``). Only ``success`` rows
        count, so a failed attempt earlier today does not block a retry. Uses
        the existing ``created_at`` / ``backup_type`` indexes.
        """
        row = self.db.fetch_one(
            "SELECT 1 FROM backup_logs "
            "WHERE backup_type = %s AND status = 'success' "
            "AND created_at >= date_trunc('day', now()) "
            "LIMIT 1",
            [backup_type],
        )
        return row is not None

    def has_successful_auto_backup_today(self) -> bool:
        """True if any successful automatic backup already ran today.

        Startup and shutdown are both automatic triggers. The application should
        create at most one automatic backup per calendar day, not one per
        trigger.
        """
        row = self.db.fetch_one(
            "SELECT 1 FROM backup_logs "
            "WHERE backup_type IN ('startup', 'shutdown') AND status = 'success' "
            "AND created_at >= date_trunc('day', now()) "
            "LIMIT 1",
        )
        return row is not None

    def oldest_success_logs(self, keep: int) -> list[dict[str, Any]]:
        """Return successful-backup rows beyond the newest ``keep`` (oldest first).

        Used by retention: the returned rows are the ones to prune. Only rows
        that actually produced a file (status = 'success' with a file_path) are
        considered, so a failed attempt never counts against the retention quota
        and never causes a real backup to be deleted.
        """
        if keep is None or keep <= 0:
            return []
        return self.db.fetch_all(
            "SELECT id, file_path, file_name FROM backup_logs "
            "WHERE status = 'success' AND file_path IS NOT NULL "
            "ORDER BY id DESC OFFSET %s",
            [int(keep)],
        )

    # --- deletes ------------------------------------------------------------
    def delete_log(self, log_id: int) -> None:
        self.db.execute("DELETE FROM backup_logs WHERE id = %s", [log_id])
