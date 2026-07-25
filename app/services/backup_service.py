"""Business logic for the Backup module (النسخ الاحتياطي).

Creates plain-text SQL database backups with ``pg_dump`` and records every
attempt (success or failure) in the ``backup_logs`` table through
:class:`BackupRepository`. Mirrors the project's existing service/repository
split (e.g. ``AttachmentsService`` + ``AttachmentsRepository``).

Design contract (kept deliberately narrow — this module touches nothing else):

* Output is ALWAYS a plain-text ``.sql`` file (``pg_dump --format plain``). No
  custom / tar / directory format, and no copying of database files.
* Connection details come from ``DATABASE_URL`` if set, otherwise from the app
  settings (:func:`get_settings`). Nothing is hard-coded.
* The password is passed to ``pg_dump`` only through the ``PGPASSWORD``
  environment variable — never on the command line, in logs, or in the UI.
* Startup/shutdown backups never raise to the caller, so they cannot block the
  application from opening or closing. A manual backup returns a result object
  the UI can use to show a clear error.
* File deletion is restricted to ``.sql`` files that live directly inside the
  ``Backups/`` folder, so business data can never be removed by this module.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from app.config.settings import get_settings
from app.models.backup_log import (
    BACKUP_DIR_NAME,
    BACKUP_FILE_EXTENSION,
    BACKUP_TYPES,
    DEFAULT_BACKUP_ON_SHUTDOWN,
    DEFAULT_BACKUP_ON_STARTUP,
    DEFAULT_MAX_BACKUPS,
    STATUS_FAILED,
    STATUS_SUCCESS,
)
from app.repositories.backup_repository import BackupRepository

# Application root (…/CRM_PYTHON_APP_STRUCTURE): app/services/ -> app/ -> root.
_APP_ROOT = Path(__file__).resolve().parents[2]

# Runner returns ``(returncode, stderr)``. Injectable so tests need no real DB.
DumpRunner = Callable[[list[str], dict[str, str]], "tuple[int, str]"]


class BackupError(Exception):
    """Raised for user-facing backup failures (friendly Arabic message)."""


@dataclass(frozen=True)
class ConnectionInfo:
    host: str
    port: int
    dbname: str
    user: str
    password: str


@dataclass(frozen=True)
class BackupResult:
    success: bool
    backup_type: str
    file_name: str
    file_path: str
    error_message: str | None = None
    log_id: int | None = None


def _default_dump_runner(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run pg_dump as a subprocess and return ``(returncode, stderr)``."""
    proc = subprocess.run(command, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stderr or ""


class BackupService:
    def __init__(
        self,
        repository: BackupRepository | None = None,
        backup_dir: str | os.PathLike[str] | None = None,
        pg_dump_path: str | None = None,
        settings_path: str | os.PathLike[str] | None = None,
        dump_runner: DumpRunner | None = None,
    ) -> None:
        self.repository = repository or BackupRepository()
        self.backup_dir = str(
            backup_dir
            or os.getenv("BACKUP_DIR")
            or (_APP_ROOT / BACKUP_DIR_NAME)
        )
        # Locate the pg_dump binary. An explicit path (constructor arg or the
        # PG_DUMP_PATH env var) always wins; otherwise we auto-discover it — on
        # PATH first, then in the standard PostgreSQL install folders — so the
        # feature works out of the box even when PostgreSQL's bin is not on PATH.
        explicit = pg_dump_path or os.getenv("PG_DUMP_PATH")
        self.pg_dump_path = explicit or self._discover_pg_dump()
        self.settings_path = Path(
            settings_path
            or os.getenv("BACKUP_SETTINGS_PATH")
            or (_APP_ROOT / "backup_settings.json")
        )
        self._dump_runner = dump_runner or _default_dump_runner

    # --- pg_dump discovery --------------------------------------------------
    @staticmethod
    def _discover_pg_dump() -> str:
        """Find the pg_dump executable without requiring PATH configuration.

        Order: (1) ``pg_dump`` on PATH, (2) the standard Windows PostgreSQL
        install folders (``…\\PostgreSQL\\<ver>\\bin\\pg_dump.exe``), preferring the
        highest installed major version — a newer pg_dump can dump older servers,
        but not the reverse. Falls back to the bare name so the normal
        "not found" error still surfaces if nothing is located.
        """
        on_path = shutil.which("pg_dump")
        if on_path:
            return on_path

        candidates: list[str] = []
        program_dirs = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("ProgramW6432", r"C:\Program Files"),
        ]
        for base in filter(None, dict.fromkeys(program_dirs)):
            candidates.extend(
                glob.glob(os.path.join(base, "PostgreSQL", "*", "bin", "pg_dump.exe"))
            )

        def _version(path: str) -> int:
            parts = Path(path).parts
            try:
                return int(parts[parts.index("PostgreSQL") + 1])
            except (ValueError, IndexError):
                return -1

        existing = [p for p in candidates if os.path.isfile(p)]
        if existing:
            existing.sort(key=_version, reverse=True)
            return existing[0]
        return "pg_dump"

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        self.repository.ensure_schema()

    # --- folder -------------------------------------------------------------
    def ensure_backup_dir(self) -> str:
        """Create the Backups/ folder if missing and return its path."""
        os.makedirs(self.backup_dir, exist_ok=True)
        return self.backup_dir

    # --- naming -------------------------------------------------------------
    @staticmethod
    def build_file_name(backup_type: str, moment: datetime | None = None) -> str:
        """``backup_<type>_YYYYMMDD_HHMMSS.sql`` (plain SQL, always .sql)."""
        stamp = (moment or datetime.now()).strftime("%Y%m%d_%H%M%S")
        return f"backup_{backup_type}_{stamp}.{BACKUP_FILE_EXTENSION}"

    # --- connection resolution ---------------------------------------------
    def _resolve_connection(self) -> ConnectionInfo:
        """Read connection info from DATABASE_URL, else from app settings.

        Never hard-codes any value. The returned password is used solely to set
        PGPASSWORD for the pg_dump subprocess.
        """
        url = os.getenv("DATABASE_URL")
        if url:
            parsed = urlparse(url)
            return ConnectionInfo(
                host=parsed.hostname or "localhost",
                port=parsed.port or 5432,
                dbname=(parsed.path or "").lstrip("/") or "postgres",
                user=unquote(parsed.username) if parsed.username else "postgres",
                password=unquote(parsed.password) if parsed.password else "",
            )
        settings = get_settings()
        return ConnectionInfo(
            host=settings.db_host,
            port=int(settings.db_port),
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
        )

    # --- pg_dump ------------------------------------------------------------
    def _run_pg_dump(self, conn: ConnectionInfo, target_path: str) -> None:
        """Invoke pg_dump in plain-SQL mode, writing to ``target_path``.

        The password is provided only through PGPASSWORD; ``--no-password``
        prevents any interactive prompt if it is wrong or missing.
        """
        command = [
            self.pg_dump_path,
            "--host", conn.host,
            "--port", str(conn.port),
            "--username", conn.user,
            "--dbname", conn.dbname,
            "--format", "plain",
            "--encoding", "UTF8",
            "--no-password",
            "--file", target_path,
        ]
        env = dict(os.environ)
        if conn.password:
            env["PGPASSWORD"] = conn.password
        try:
            returncode, stderr = self._dump_runner(command, env)
        except FileNotFoundError as exc:
            raise BackupError(
                "تعذر العثور على أداة pg_dump. تأكد من تثبيت PostgreSQL "
                "أو ضبط المتغير PG_DUMP_PATH على مسار pg_dump."
            ) from exc
        if returncode != 0:
            detail = self._scrub(stderr, conn.password).strip()
            raise BackupError(detail or f"فشل pg_dump (رمز الخروج {returncode}).")

    # --- create -------------------------------------------------------------
    def create_backup(self, backup_type: str, created_by: int | None = None) -> BackupResult:
        """Create one plain-SQL backup and record the outcome in backup_logs.

        Always writes a log row (success or failed). Raises nothing for the
        ``pg_dump`` failure path — the returned :class:`BackupResult` carries the
        error so callers decide how to surface it.
        """
        if backup_type not in BACKUP_TYPES:
            raise ValueError(f"نوع نسخة احتياطية غير معروف: {backup_type}")

        # Best-effort: make sure the log table and folder exist first.
        try:
            self.ensure_schema()
        except Exception:
            pass
        folder = self.ensure_backup_dir()
        file_name = self.build_file_name(backup_type)
        file_path = os.path.join(folder, file_name)
        # Two backups can land in the same second (same YYYYMMDD_HHMMSS name); add
        # a short suffix on collision so a prior backup is never silently
        # overwritten, while keeping the standard name in the normal case.
        base, ext = os.path.splitext(file_name)
        counter = 2
        while os.path.exists(file_path):
            file_name = f"{base}_{counter}{ext}"
            file_path = os.path.join(folder, file_name)
            counter += 1

        status = STATUS_SUCCESS
        error_message: str | None = None
        password = ""
        try:
            conn = self._resolve_connection()
            password = conn.password
            self._run_pg_dump(conn, file_path)
            if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
                raise BackupError("لم يتم إنشاء ملف نسخة احتياطية صالح.")
        except Exception as exc:  # noqa: BLE001 - record every failure, never crash
            status = STATUS_FAILED
            error_message = self._scrub(str(exc), password) or "فشل إنشاء النسخة الاحتياطية."
            # Drop any partial/empty file so it can't be mistaken for a good backup.
            self._delete_backup_file_safely(file_path)

        log_id = self._record(backup_type, file_name, file_path, status, error_message, created_by)

        if status == STATUS_SUCCESS:
            try:
                self.apply_retention()
            except Exception:
                pass  # retention is best-effort; never fail a good backup over it

        return BackupResult(
            success=status == STATUS_SUCCESS,
            backup_type=backup_type,
            file_name=file_name,
            file_path=file_path,
            error_message=error_message,
            log_id=log_id,
        )

    def _record(
        self,
        backup_type: str,
        file_name: str,
        file_path: str,
        status: str,
        error_message: str | None,
        created_by: int | None,
    ) -> int | None:
        try:
            row = self.repository.insert_log(
                backup_type, file_name, file_path, status, error_message, created_by
            )
            return int(row["id"]) if row else None
        except Exception:
            # If even the log write fails (e.g. DB down), we still must not crash
            # the caller — especially on startup/shutdown paths.
            return None

    # --- convenience triggers ----------------------------------------------
    def _already_auto_backed_up_today(self) -> bool:
        """True if a successful ``backup_type`` backup already ran today.

        This is what makes the auto-backups run at most once per day instead of
        on every open/close: the first successful startup (or shutdown) backup
        of the day is kept, and later opens/closes on the same day are skipped.
        Never raises — if the check itself fails we return ``False`` so a backup
        is still attempted rather than silently lost.
        """
        try:
            checker = getattr(self.repository, "has_successful_auto_backup_today", None)
            if callable(checker):
                return bool(checker())
            return (
                self.repository.has_successful_backup_today("startup")
                or self.repository.has_successful_backup_today("shutdown")
            )
        except Exception:
            return False

    def run_startup_backup(self, created_by: int | None = None) -> BackupResult | None:
        """Startup auto-backup. At most once per day; honors the setting; never raises.

        Skips (returns ``None``) when the feature is disabled, or when a
        successful startup backup was already taken today — so reopening the app
        several times in one day does not create a new backup each time.
        """
        if not self.is_backup_on_startup_enabled():
            return None
        if self._already_auto_backed_up_today():
            return None
        try:
            return self.create_backup("startup", created_by)
        except Exception:
            return None

    def run_shutdown_backup(self, created_by: int | None = None) -> BackupResult | None:
        """Shutdown auto-backup. At most once per day; honors the setting; never raises.

        Skips (returns ``None``) when the feature is disabled, or when a
        successful shutdown backup was already taken today — so closing the app
        several times in one day does not create a new backup each time.
        """
        if not self.is_backup_on_shutdown_enabled():
            return None
        if self._already_auto_backed_up_today():
            return None
        try:
            return self.create_backup("shutdown", created_by)
        except Exception:
            return None

    # --- history ------------------------------------------------------------
    def list_backups(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.repository.list_logs(limit)

    def delete_backup(self, log_id: int) -> None:
        """Delete a backup: remove its .sql file (if inside Backups/) and its row."""
        row = self.repository.get_log(log_id)
        if row:
            self._delete_backup_file_safely(row.get("file_path"))
        self.repository.delete_log(log_id)

    # --- retention ----------------------------------------------------------
    def apply_retention(self) -> int:
        """Keep only the newest ``max_backups`` successful backups.

        Deletes the oldest surplus ``.sql`` files from the Backups folder and
        their matching ``backup_logs`` rows. Returns how many were removed. A
        limit of 0 (or less) disables pruning entirely.
        """
        max_backups = self.get_settings().get("max_backups", DEFAULT_MAX_BACKUPS)
        try:
            max_backups = int(max_backups)
        except (TypeError, ValueError):
            max_backups = DEFAULT_MAX_BACKUPS
        if max_backups <= 0:
            return 0
        removed = 0
        for row in self.repository.oldest_success_logs(max_backups):
            self._delete_backup_file_safely(row.get("file_path"))
            self.repository.delete_log(int(row["id"]))
            removed += 1
        return removed

    # --- optional settings (persisted to backup_settings.json) -------------
    def get_settings(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "backup_on_startup": DEFAULT_BACKUP_ON_STARTUP,
            "backup_on_shutdown": DEFAULT_BACKUP_ON_SHUTDOWN,
            "max_backups": DEFAULT_MAX_BACKUPS,
        }
        try:
            stored = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
        return data

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        current.update(settings or {})
        current["backup_on_startup"] = bool(current.get("backup_on_startup"))
        current["backup_on_shutdown"] = bool(current.get("backup_on_shutdown"))
        try:
            current["max_backups"] = max(0, int(current.get("max_backups", DEFAULT_MAX_BACKUPS)))
        except (TypeError, ValueError):
            current["max_backups"] = DEFAULT_MAX_BACKUPS
        try:
            self.settings_path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        return current

    def is_backup_on_startup_enabled(self) -> bool:
        return bool(self.get_settings().get("backup_on_startup", DEFAULT_BACKUP_ON_STARTUP))

    def is_backup_on_shutdown_enabled(self) -> bool:
        return bool(self.get_settings().get("backup_on_shutdown", DEFAULT_BACKUP_ON_SHUTDOWN))

    # --- helpers ------------------------------------------------------------
    def _delete_backup_file_safely(self, file_path: str | None) -> None:
        """Remove a file only if it is a ``.sql`` directly inside Backups/.

        This is the single guard that makes deletion incapable of touching any
        business data or file outside the backup folder.
        """
        if not file_path:
            return
        try:
            path = Path(file_path).resolve()
            folder = Path(self.backup_dir).resolve()
            if path.parent == folder and path.suffix.lower() == ".sql" and path.is_file():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _scrub(text: str | None, secret: str) -> str:
        """Remove the DB password from any text before it is logged or shown."""
        if not text:
            return ""
        cleaned = str(text)
        if secret:
            cleaned = cleaned.replace(secret, "******")
        return cleaned
