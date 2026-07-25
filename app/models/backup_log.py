"""Schema definition and domain constants for the Backup module (النسخ الاحتياطي).

This module holds the *structure* (DDL) and reusable domain constants only — no
SQL execution and no business logic. The repository layer runs
``BACKUP_LOGS_SCHEMA_SQL`` (every statement uses ``CREATE ... IF NOT EXISTS`` so
it is safe to run repeatedly and never drops, resets, or deletes anything). The
same SQL is mirrored in
``app/database/migrations/007_create_backup_logs_schema.sql``.

Scope note: this module only records *metadata* about each backup run. The real
backup output is a plain-text ``.sql`` file produced by ``pg_dump`` and written
inside the application's ``Backups/`` folder — never inside the database.
"""

from __future__ import annotations

# Table that records one row per backup attempt (success or failure).
TABLE_NAME = "backup_logs"

# Backup trigger types.
BACKUP_TYPE_MANUAL = "manual"
BACKUP_TYPE_STARTUP = "startup"
BACKUP_TYPE_SHUTDOWN = "shutdown"
BACKUP_TYPES: tuple[str, ...] = (
    BACKUP_TYPE_MANUAL,
    BACKUP_TYPE_STARTUP,
    BACKUP_TYPE_SHUTDOWN,
)

# Outcome statuses.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# Backup files are always plain-text SQL (pg_dump plain format).
BACKUP_FILE_EXTENSION = "sql"

# Folder (relative to the application root) where the .sql files are written.
BACKUP_DIR_NAME = "Backups"

# Default optional settings (persisted to backup_settings.json, see the service).
DEFAULT_BACKUP_ON_STARTUP = True
DEFAULT_BACKUP_ON_SHUTDOWN = True
# Maximum number of backup files to keep. 0 (or less) means "keep everything".
DEFAULT_MAX_BACKUPS = 20

# Columns loaded for the history table (in display order).
LIST_COLUMNS: tuple[str, ...] = (
    "id",
    "backup_type",
    "file_name",
    "file_path",
    "status",
    "error_message",
    "created_at",
    "created_by",
)


BACKUP_LOGS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS backup_logs (
    id              serial PRIMARY KEY,
    backup_type     varchar(20) NOT NULL,
    file_name       varchar(255),
    file_path       text,
    status          varchar(20) NOT NULL,
    error_message   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      integer
);

CREATE INDEX IF NOT EXISTS idx_backup_logs_created_at ON backup_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_backup_logs_backup_type ON backup_logs (backup_type);
CREATE INDEX IF NOT EXISTS idx_backup_logs_status ON backup_logs (status);
"""
