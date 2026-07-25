"""Helpers for opening WhatsApp Web from the CRM."""

from __future__ import annotations

import ctypes
import re
import webbrowser
from urllib.parse import quote


_FORMATTING_CHARACTERS = re.compile(r"[\s+\-()\[\]{}]")
_INTERNATIONAL_MOBILE = re.compile(r"201[0125][0-9]{8}")
DEFAULT_MESSAGE = "السلام عليكم"
WHATSAPP_WEB_URL = "https://web.whatsapp.com/send"
_VK_RETURN = 0x0D
_KEYEVENTF_KEYUP = 0x0002


def normalize_egyptian_phone(phone: str | None) -> str:
    """Return an Egyptian mobile number in WhatsApp's international format."""
    if phone is None:
        raise ValueError("Phone number is required.")

    compact = _FORMATTING_CHARACTERS.sub("", str(phone))
    if not compact:
        raise ValueError("Phone number is required.")

    if re.fullmatch(r"01[0125][0-9]{8}", compact):
        compact = f"2{compact}"
    elif re.fullmatch(r"1[0125][0-9]{8}", compact):
        compact = f"20{compact}"

    if not _INTERNATIONAL_MOBILE.fullmatch(compact):
        raise ValueError("Invalid Egyptian mobile number.")
    return compact


def build_whatsapp_url(phone: str | None, message: str = DEFAULT_MESSAGE) -> str:
    """Build a WhatsApp Web URL that prefills, but does not send, a message."""
    normalized_phone = normalize_egyptian_phone(phone)
    encoded_message = quote(message, safe="")
    return f"{WHATSAPP_WEB_URL}?phone={normalized_phone}&text={encoded_message}"


def open_whatsapp_web(phone: str | None, message: str = DEFAULT_MESSAGE) -> str:
    """Open WhatsApp Web in the default browser and return the opened URL."""
    url = build_whatsapp_url(phone, message)
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        raise RuntimeError("Unable to open WhatsApp Web in the default browser.") from exc
    if not opened:
        raise RuntimeError("Unable to open WhatsApp Web in the default browser.")
    return url


def press_enter_key() -> None:
    """Press Enter in the foreground Windows application."""
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(_VK_RETURN, 0, 0, 0)
        user32.keybd_event(_VK_RETURN, 0, _KEYEVENTF_KEYUP, 0)
    except Exception as exc:
        raise RuntimeError("Unable to press the Enter key.") from exc
