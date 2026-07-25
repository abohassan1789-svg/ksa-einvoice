"""Entry point for the CRM review app.

The screens that used to live in this single file have been split by module:

* shared theme/helpers -> ``app.ui.common``
* the F1 lookup dialog   -> ``app.ui.dialogs.record_lookup_dialog``
* the reusable CRUD page -> ``app.ui.screens.base_crud_screen``
* one screen per module  -> ``app.ui.screens.<module>_screen``
* the sidebar/navigation -> ``app.ui.main_window``

This module now only wires up the Qt application and launches the main window.
The names below are re-exported for backward compatibility (existing imports
and tests, e.g. ``from app.ui.review_window import filter_lookup_rows``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.repositories.security_repository import SecurityRepository
from app.security.session_context import SESSION
from app.services.auth_service import AuthService
from app.services.permission_service import PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common.loading_splash import LoadingSplash
from app.ui.common.lookup_filters import filter_lookup_rows, project_visible_columns
from app.ui.common.theme import (
    BORDER,
    EXTRA_COLUMN_LABELS,
    GREEN,
    GREEN_DARK,
    LOOKUP_LAYOUT_KEYS,
    RIGHT_ALIGNED_FIELDS,
    TEXT,
    _RightAlignDelegate,
    _button_style,
)
from app.ui.dialogs.record_lookup_dialog import RecordLookupDialog
from app.ui.login_window import LoginWindow
from app.ui.main_window import ReviewMainWindow
from app.ui.screens.base_crud_screen import BaseCrudScreen

# Backward-compatible alias for the former class name.
ReviewCrudPage = BaseCrudScreen

__all__ = [
    "filter_lookup_rows",
    "project_visible_columns",
    "RecordLookupDialog",
    "ReviewCrudPage",
    "BaseCrudScreen",
    "ReviewMainWindow",
    "run_app",
]


def _bootstrap_security(progress=None) -> tuple[AuthService, PermissionService, Exception | None]:
    """Ensure the security schema exists, seed the default admin, and sync
    permissions from the registry. Returns the services and any error so the
    UI can decide what to do (e.g. DB not reachable)."""
    repository = SecurityRepository()
    auth_service = AuthService(repository)
    permission_service = PermissionService(repository)
    sync_service = PermissionsSyncService(repository)
    error: Exception | None = None
    try:
        if progress:
            progress(1, 3, "")
        repository.ensure_schema()
        if progress:
            progress(2, 3, "")
        auth_service.seed_admin()
        sync_service.sync()
        # Attachments module schema (safe, idempotent CREATE IF NOT EXISTS).
        from app.services.attachments_service import AttachmentsService

        AttachmentsService().ensure_schema()
        if progress:
            progress(3, 3, "")
    except Exception as exc:  # DB down / not provisioned
        error = exc
    return auth_service, permission_service, error


def run_app() -> int:
    # QtWebEngine (used by the Saudi invoice preview/PDF export) asks for shared
    # OpenGL contexts; this must be set before the QApplication is created.
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication([])
    app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet("QWidget { font-family: 'Segoe UI', 'Tahoma', 'Arial'; }")
    app.setStyle("Fusion")

    # Keep the process alive across the brief window-less gaps between the splash
    # closing and the next window opening (login, then the main window). Without
    # this, Qt's default quit-on-last-window-closed can terminate the app in that
    # gap — near-certain when launched with pythonw.exe (the desktop shortcut).
    # Restored to True right before the event loop so closing the main window
    # still exits cleanly.
    app.setQuitOnLastWindowClosed(False)

    # 1) Loading splash FIRST, while the security schema is provisioned,
    #    Admin/1 is seeded, and permissions are synced.
    # 0) Ensure this machine has a connection profile before touching the DB.
    #    On a fresh install with no profile, prompt for it (first-run screen).
    from app.ui.screens.connection_screen import ensure_connection

    if not ensure_connection(None):
        return 0

    splash = LoadingSplash()
    splash.show()
    app.processEvents()
    auth_service, permission_service, bootstrap_error = _bootstrap_security(
        progress=splash.set_progress
    )
    splash.close()
    app.processEvents()

    if bootstrap_error is not None:
        from app.config.database import describe_db_error
        from app.config.settings import get_settings

        cfg = get_settings()
        # Server-unreachable / wrong-password / wrong-database get their own
        # clear message; anything that fails AFTER a working connection (the
        # actual security-schema/permission init) is shown verbatim, prefixed as
        # a bootstrap failure so the real cause is never hidden.
        message = describe_db_error(
            bootstrap_error,
            host=cfg.db_host,
            port=cfg.db_port,
            db_name=cfg.db_name,
            password=cfg.db_password,
            fallback_prefix="تعذّر تهيئة نظام الصلاحيات",
        )
        QMessageBox.warning(None, "تنبيه قاعدة البيانات", message)

    # 1b) Startup backup (plain-SQL via pg_dump). Best-effort and fully guarded:
    #     a backup failure must never stop the application from opening. Honors
    #     the "backup on startup" setting and records the outcome in backup_logs.
    try:
        from app.services.backup_service import BackupService

        BackupService().run_startup_backup()
    except Exception:
        pass

    # 2) Login.
    login = LoginWindow(auth_service)
    if login.exec() != QDialog.Accepted or login.user is None:
        return 0

    # 3) Load the logged-in user's effective permissions into the session.
    try:
        allowed = permission_service.effective_codes(login.user)
    except Exception:
        allowed = set()
    SESSION.set_session(login.user, allowed)

    # 4) Show the splash again while the viewable screens are pre-built, then
    #    open the ready main window.
    splash.show()
    app.processEvents()
    window = ReviewMainWindow()
    window.prewarm_all(progress=splash.set_progress)
    window.showMaximized()
    splash.close()
    app.setQuitOnLastWindowClosed(True)
    return app.exec()
