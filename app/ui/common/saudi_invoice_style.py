"""Shared visual tokens and tiny style helpers for the Saudi invoice screen.

Presentation only — colours, the button-kind palette, qtawesome icon helpers and
the RTL alignment idiom. No business or database logic lives here. These are used
by both ``SaudiSalesInvoicePage`` and its lookup dialogs so the look stays in one
place. Existing shared modules are left untouched.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

try:  # qtawesome is installed (1.4.x); degrade gracefully if it ever is not.
    import qtawesome as qta
except Exception:  # pragma: no cover - defensive
    qta = None

# --- design tokens (match F:\ErpLogstic reference) --------------------------
SI_GREEN = "#00843D"
SI_GREEN_DARK = "#046A31"
SI_BLUE = "#2563EB"
SI_BLUE_DARK = "#1D4ED8"
SI_RED = "#DC2626"
SI_RED_DARK = "#B91C1C"
SI_GRAY = "#4B5563"
SI_GRAY_DARK = "#374151"
SI_BORDER = "#D7DEE7"
SI_CARD_BORDER = "#E5E7EB"
SI_PAGE_BG = "#F5F7FA"
SI_WHITE = "#FFFFFF"
SI_READONLY_BG = "#F4F6F9"
SI_SELECT_BG = "#E7F6EE"
SI_TOTAL_BG = "#E8F5EC"
SI_TOTAL_BORDER = "#BFE6CF"
SI_FOOTER_BG = "#EAF6EF"
SI_FOOTER_BORDER = "#D7EBDF"
SI_TEXT = "#111827"

# (background, foreground, border, hover) per button kind.
BTN_KINDS: dict[str, tuple[str, str, str, str]] = {
    "green": (SI_GREEN, "#FFFFFF", SI_GREEN, SI_GREEN_DARK),
    "blue": (SI_BLUE, "#FFFFFF", SI_BLUE, SI_BLUE_DARK),
    "red": (SI_RED, "#FFFFFF", SI_RED, SI_RED_DARK),
    "white": ("#FFFFFF", "#374151", SI_BORDER, "#F3F4F6"),
    "gray": (SI_GRAY, "#FFFFFF", SI_GRAY, SI_GRAY_DARK),
}


def si_icon(name: str, color: str = "#374151") -> QIcon:
    if qta is None:
        return QIcon()
    try:
        return qta.icon(name, color=color)
    except Exception:  # pragma: no cover - unknown icon name
        return QIcon()


def si_pixmap(name: str, color: str = "#374151", size: int = 24) -> QPixmap:
    if qta is None:
        return QPixmap()
    try:
        return qta.icon(name, color=color).pixmap(size, size)
    except Exception:  # pragma: no cover
        return QPixmap()


def button_qss(kind: str) -> str:
    bg, fg, border, hover = BTN_KINDS.get(kind, BTN_KINDS["gray"])
    return (
        "QPushButton {"
        f" background:{bg}; color:{fg}; border:1px solid {border};"
        " border-radius:8px; padding:4px 10px;"
        " font-family:'Cairo', 'Segoe UI', 'Tahoma', 'Arial'; font-size:12px; font-weight:700; }"
        f"QPushButton:hover {{ background:{hover}; }}"
        "QPushButton:disabled { background:#EAECEF; color:#AEB6C0; border-color:#EAECEF; }"
    )


def style_button(button, kind: str) -> None:
    button.setStyleSheet(button_qss(kind))


def rtl_alignment(is_arabic: bool = True):
    """The core RTL idiom: AlignAbsolute stops Qt mirroring AlignRight visually."""
    horizontal = Qt.AlignRight if is_arabic else Qt.AlignLeft
    return horizontal | Qt.AlignVCenter | Qt.AlignAbsolute


__all__ = [name for name in dir() if name.startswith("SI_") or name in {
    "BTN_KINDS", "si_icon", "si_pixmap", "button_qss", "style_button", "rtl_alignment",
}]
