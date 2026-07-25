-- CRM Backup logs schema (وحدة النسخ الاحتياطي).
-- Records one metadata row per backup attempt (manual / startup / shutdown),
-- with its outcome (success / failed) and an error message when it failed.
--
-- The actual backup output is a plain-text .sql file created by pg_dump inside
-- the application's Backups/ folder — this table never stores backup content or
-- any database file, only metadata about each run.
--
-- Safe to run repeatedly: every statement uses CREATE ... IF NOT EXISTS and
-- nothing is dropped, reset, or deleted. Mirrors app/models/backup_log.py
-- (BACKUP_LOGS_SCHEMA_SQL), which the app also runs automatically at startup.

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
