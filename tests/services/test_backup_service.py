"""Unit tests for the Backup service (no real database or PostgreSQL needed).

A small in-memory fake repository stands in for ``BackupRepository`` and a fake
``dump_runner`` stands in for the ``pg_dump`` subprocess, so all business rules
(file naming, plain-SQL format, logging of success/failure, retention, settings,
safe deletion, password protection) can be verified without external services.
"""

import os
from pathlib import Path

import pytest

from app.services.backup_service import BackupService, ConnectionInfo


class FakeRepository:
    """In-memory stand-in for BackupRepository."""

    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.ensured = False
        # Simulated calendar day. Tests bump this to emulate "the next day".
        self.current_day = 1

    def ensure_schema(self):
        self.ensured = True

    def insert_log(self, backup_type, file_name, file_path, status, error_message=None, created_by=None):
        row = {
            "id": self._next_id,
            "backup_type": backup_type,
            "file_name": file_name,
            "file_path": file_path,
            "status": status,
            "error_message": error_message,
            "created_by": created_by,
            "created_at": "2026-07-06 00:00:00",
            "created_at_day": self.current_day,
        }
        self._next_id += 1
        self.rows.append(row)
        return {"id": row["id"], "created_at": row["created_at"]}

    def has_successful_backup_today(self, backup_type):
        return any(
            r["backup_type"] == backup_type
            and r["status"] == "success"
            and r.get("created_at_day") == self.current_day
            for r in self.rows
        )

    def has_successful_auto_backup_today(self):
        return any(
            r["backup_type"] in {"startup", "shutdown"}
            and r["status"] == "success"
            and r.get("created_at_day") == self.current_day
            for r in self.rows
        )

    def list_logs(self, limit=500):
        return list(reversed(self.rows))[:limit]

    def get_log(self, log_id):
        return next((r for r in self.rows if r["id"] == log_id), None)

    def oldest_success_logs(self, keep):
        if keep is None or keep <= 0:
            return []
        successes = [r for r in self.rows if r["status"] == "success" and r["file_path"]]
        successes.sort(key=lambda r: r["id"], reverse=True)  # newest first
        return successes[keep:]  # surplus beyond the newest `keep`

    def delete_log(self, log_id):
        self.rows = [r for r in self.rows if r["id"] != log_id]


def _make_service(tmp_path, runner):
    """Build a service wired to a temp Backups dir, temp settings, and a fake runner."""
    return BackupService(
        repository=FakeRepository(),
        backup_dir=tmp_path / "Backups",
        settings_path=tmp_path / "backup_settings.json",
        dump_runner=runner,
        pg_dump_path="pg_dump",
    )


def _writing_runner(content=b"-- SQL DUMP\nCREATE TABLE t();\n"):
    """A fake pg_dump that succeeds and writes a plain-SQL file to --file."""
    def runner(command, env):
        target = command[command.index("--file") + 1]
        with open(target, "wb") as handle:
            handle.write(content)
        return 0, ""
    return runner


def _failing_runner(stderr="connection refused"):
    def runner(command, env):
        return 1, stderr
    return runner


# --- file naming -----------------------------------------------------------
def test_file_name_format_and_extension():
    name = BackupService.build_file_name("manual")
    assert name.startswith("backup_manual_")
    assert name.endswith(".sql")
    # backup_manual_YYYYMMDD_HHMMSS.sql -> 8 + 1 + 6 digits in the timestamp
    stamp = name[len("backup_manual_"):-len(".sql")]
    date_part, time_part = stamp.split("_")
    assert date_part.isdigit() and len(date_part) == 8
    assert time_part.isdigit() and len(time_part) == 6


# --- successful backup -----------------------------------------------------
def test_manual_backup_creates_sql_file_and_success_log(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    result = service.create_backup("manual", created_by=7)
    assert result.success is True
    assert result.file_name.endswith(".sql")
    assert os.path.isfile(result.file_path)
    assert Path(result.file_path).read_text(encoding="utf-8").startswith("-- SQL DUMP")

    logs = service.list_backups()
    assert len(logs) == 1
    assert logs[0]["status"] == "success"
    assert logs[0]["backup_type"] == "manual"
    assert logs[0]["created_by"] == 7
    assert logs[0]["error_message"] is None


def test_backup_uses_plain_format_flags(tmp_path):
    captured = {}

    def runner(command, env):
        captured["command"] = command
        target = command[command.index("--file") + 1]
        Path(target).write_text("-- dump", encoding="utf-8")
        return 0, ""

    service = _make_service(tmp_path, runner)
    service.create_backup("manual")
    cmd = captured["command"]
    # Plain SQL format only — never custom/tar/directory.
    assert "--format" in cmd and cmd[cmd.index("--format") + 1] == "plain"
    for bad in ("-Fc", "-Ft", "-Fd", "custom", "tar", "directory"):
        assert bad not in cmd


# --- failure path ----------------------------------------------------------
def test_failed_backup_records_failed_log_and_no_leftover_file(tmp_path):
    service = _make_service(tmp_path, _failing_runner("could not connect"))
    result = service.create_backup("manual")
    assert result.success is False
    assert result.error_message and "could not connect" in result.error_message

    logs = service.list_backups()
    assert len(logs) == 1
    assert logs[0]["status"] == "failed"
    # No empty/partial .sql file should remain in the folder.
    sql_files = list((tmp_path / "Backups").glob("*.sql"))
    assert sql_files == []


def test_missing_pg_dump_is_recorded_not_raised(tmp_path):
    def runner(command, env):
        raise FileNotFoundError("pg_dump not found")

    service = _make_service(tmp_path, runner)
    result = service.create_backup("startup")
    assert result.success is False
    assert "pg_dump" in (result.error_message or "")
    assert service.list_backups()[0]["status"] == "failed"


# --- password protection ---------------------------------------------------
def test_password_never_appears_in_command_and_is_scrubbed_from_errors(tmp_path, monkeypatch):
    secret = "s3cr3t-pw"
    monkeypatch.setattr(
        BackupService, "_resolve_connection",
        lambda self: ConnectionInfo("localhost", 5432, "crm", "postgres", secret),
    )

    seen = {}

    def runner(command, env):
        seen["command"] = command
        seen["pgpassword"] = env.get("PGPASSWORD")
        # pg_dump echoing the password back in an error must be scrubbed.
        return 1, f"FATAL: password '{secret}' authentication failed"

    service = _make_service(tmp_path, runner)
    result = service.create_backup("manual")

    assert secret not in " ".join(seen["command"])          # not on the command line
    assert seen["pgpassword"] == secret                      # passed only via env
    assert secret not in (result.error_message or "")        # scrubbed from the message
    assert service.list_backups()[0]["error_message"] and secret not in service.list_backups()[0]["error_message"]


# --- retention -------------------------------------------------------------
def test_retention_keeps_only_max_backups(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    service.save_settings({"max_backups": 3})
    for _ in range(5):
        service.create_backup("manual")

    logs = service.list_backups()
    assert len(logs) == 3  # oldest two pruned
    remaining_files = list((tmp_path / "Backups").glob("*.sql"))
    assert len(remaining_files) == 3


def test_retention_disabled_when_zero(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    service.save_settings({"max_backups": 0})
    for _ in range(4):
        service.create_backup("manual")
    assert len(service.list_backups()) == 4


# --- delete selected -------------------------------------------------------
def test_delete_selected_removes_file_and_row(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    result = service.create_backup("manual")
    assert os.path.isfile(result.file_path)

    service.delete_backup(result.log_id)
    assert not os.path.isfile(result.file_path)
    assert service.list_backups() == []


def test_delete_never_touches_files_outside_backups_folder(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    outside = tmp_path / "important_business_data.sql"
    outside.write_text("keep me", encoding="utf-8")

    # A log row that points outside the Backups folder must not delete that file.
    service.repository.insert_log("manual", outside.name, str(outside), "success")
    row = service.list_backups()[0]
    service.delete_backup(row["id"])
    assert outside.exists()  # untouched — deletion is confined to Backups/*.sql


# --- settings --------------------------------------------------------------
def test_settings_round_trip_and_defaults(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    defaults = service.get_settings()
    assert defaults["backup_on_startup"] is True
    assert defaults["backup_on_shutdown"] is True
    assert defaults["max_backups"] == 20

    service.save_settings({"backup_on_startup": False, "max_backups": 9})
    reloaded = service.get_settings()
    assert reloaded["backup_on_startup"] is False
    assert reloaded["max_backups"] == 9
    assert service.is_backup_on_startup_enabled() is False


def test_startup_backup_skipped_when_disabled(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    service.save_settings({"backup_on_startup": False})
    assert service.run_startup_backup() is None
    assert service.list_backups() == []


def test_shutdown_backup_runs_when_enabled(tmp_path):
    service = _make_service(tmp_path, _writing_runner())
    service.save_settings({"backup_on_shutdown": True})
    result = service.run_shutdown_backup()
    assert result is not None and result.success
    assert service.list_backups()[0]["backup_type"] == "shutdown"


# --- once-per-day auto backups ---------------------------------------------
def test_startup_backup_runs_only_once_per_day(tmp_path):
    """Reopening the app the same day must NOT create another startup backup."""
    service = _make_service(tmp_path, _writing_runner())
    first = service.run_startup_backup()
    assert first is not None and first.success
    # Second and third opens on the same day are skipped.
    assert service.run_startup_backup() is None
    assert service.run_startup_backup() is None
    startups = [b for b in service.list_backups() if b["backup_type"] == "startup"]
    assert len(startups) == 1


def test_shutdown_backup_runs_only_once_per_day(tmp_path):
    """Closing the app several times the same day must NOT re-backup."""
    service = _make_service(tmp_path, _writing_runner())
    first = service.run_shutdown_backup()
    assert first is not None and first.success
    assert service.run_shutdown_backup() is None
    shutdowns = [b for b in service.list_backups() if b["backup_type"] == "shutdown"]
    assert len(shutdowns) == 1


def test_startup_backup_runs_again_next_day(tmp_path):
    """A new calendar day allows a fresh startup backup."""
    service = _make_service(tmp_path, _writing_runner())
    assert service.run_startup_backup() is not None
    assert service.run_startup_backup() is None  # same day: skipped
    service.repository.current_day += 1  # simulate the next day
    second = service.run_startup_backup()
    assert second is not None and second.success
    startups = [b for b in service.list_backups() if b["backup_type"] == "startup"]
    assert len(startups) == 2


def test_startup_and_shutdown_share_one_auto_backup_slot_per_day(tmp_path):
    """Only one automatic backup is created per day across startup/shutdown."""
    service = _make_service(tmp_path, _writing_runner())
    assert service.run_startup_backup() is not None
    assert service.run_shutdown_backup() is None
    assert service.run_startup_backup() is None
    types = sorted(b["backup_type"] for b in service.list_backups())
    assert types == ["startup"]


def test_shutdown_backup_runs_if_no_auto_backup_succeeded_today(tmp_path):
    """Shutdown may create today's backup only when startup did not."""
    service = _make_service(tmp_path, _writing_runner())
    result = service.run_shutdown_backup()
    assert result is not None and result.success
    assert service.run_startup_backup() is None
    types = sorted(b["backup_type"] for b in service.list_backups())
    assert types == ["shutdown"]


def test_failed_startup_does_not_block_retry_same_day(tmp_path):
    """A failed attempt (no successful row) must still allow a retry today."""
    calls = {"n": 0}

    def flaky_runner(command, env):
        calls["n"] += 1
        if calls["n"] == 1:
            return 1, "boom: first attempt fails"  # failure, no file written
        target = command[command.index("--file") + 1]
        with open(target, "wb") as handle:
            handle.write(b"-- SQL DUMP\n")
        return 0, ""

    service = _make_service(tmp_path, flaky_runner)
    first = service.run_startup_backup()
    assert first is not None and not first.success  # recorded as failed
    # Same day, but the earlier attempt failed → a retry is allowed and succeeds.
    second = service.run_startup_backup()
    assert second is not None and second.success
    startups = [b for b in service.list_backups() if b["backup_type"] == "startup"]
    assert len(startups) == 2  # one failed + one success row


# --- connection resolution -------------------------------------------------
def test_resolve_connection_prefers_database_url(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://alice:p%40ss@db.example:6543/mycrm")
    service = _make_service(tmp_path, _writing_runner())
    conn = service._resolve_connection()
    assert conn.host == "db.example"
    assert conn.port == 6543
    assert conn.dbname == "mycrm"
    assert conn.user == "alice"
    assert conn.password == "p@ss"  # URL-decoded


def test_resolve_connection_falls_back_to_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    service = _make_service(tmp_path, _writing_runner())
    conn = service._resolve_connection()
    # Values come from app settings / env, never hard-coded here.
    assert conn.host and conn.dbname and conn.user
    assert isinstance(conn.port, int)
