"""Headless smoke test the installer runs to confirm the application can start.

Imports the whole application and constructs a Qt application off-screen. It does
NOT open a window, run the event loop, or touch the database (the database is
verified separately by the installer's connect/migrate/provision steps). Its only
job is to prove that every dependency imported and the GUI toolkit initialises.

Exit code 0 = OK. Non-zero with a message on stderr = failure.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    try:
        # Core GUI toolkit.
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        # Every third-party runtime dependency the app needs.
        import sqlalchemy  # noqa: F401
        import psycopg  # noqa: F401
        import dotenv  # noqa: F401
        import pydantic  # noqa: F401
        import openpyxl  # noqa: F401
        import reportlab  # noqa: F401
        import qrcode  # noqa: F401
        import PIL  # noqa: F401
        import num2words  # noqa: F401
        import cryptography  # noqa: F401
        import qtawesome  # noqa: F401
        import arabic_reshaper  # noqa: F401
        import bidi  # noqa: F401

        # Application modules (import path only; no side effects run).
        import app.main  # noqa: F401
        from app.ui.review_window import run_app  # noqa: F401
        from app.ui.main_window import ReviewMainWindow  # noqa: F401
        from app.ui.login_window import LoginWindow  # noqa: F401

        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        qapp = QApplication.instance() or QApplication([])
        _ = qapp
    except Exception as exc:  # noqa: BLE001
        print(f"App verification FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    # Soft check: the Saudi-invoice preview/PDF uses QtWebEngine (PySide6 Addons).
    # Missing WebEngine does not stop the app from starting, so warn, don't fail.
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        webengine = "available"
    except Exception:  # noqa: BLE001
        webengine = "MISSING (invoice preview/PDF export will not work)"

    print(f"App verification OK. QtWebEngine: {webengine}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
