"""Themed loading splash shown while the screens are pre-built.

Pure UI: a frameless centered card with a title and a progress bar. The shell
drives it through :meth:`set_progress` while it warms up each screen, then it is
closed and the ready main window is shown.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.theme import GREEN_DARK


class LoadingSplash(QWidget):
    """Small frameless splash with a progress bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFixedSize(480, 280)
        self._build_ui()
        self._center()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background:{GREEN_DARK}; border-radius:14px; }}")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(12)

        title = QLabel("إدارة الفواتير الإلكترونية")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color:#FFFFFF; font-size:25px; font-weight:900; background:transparent;")

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.bar.setStyleSheet(
            "QProgressBar { background:rgba(255,255,255,0.18); border:none; border-radius:6px; }"
            "QProgressBar::chunk { background:#FFFFFF; border-radius:6px; }"
        )

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(self.bar)

    def _center(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            center = screen.availableGeometry().center()
            geo = self.frameGeometry()
            geo.moveCenter(center)
            self.move(geo.topLeft())

    def set_progress(self, current: int, total: int, name: str) -> None:
        """Update the progress bar. ``name`` is the screen currently loading."""
        percent = int(current / total * 100) if total else 0
        self.bar.setValue(percent)
