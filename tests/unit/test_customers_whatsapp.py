from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.services import whatsapp_service
from app.services.whatsapp_service import normalize_egyptian_phone
import app.ui.screens.customers_screen as customers_module
from app.ui.screens.customers_screen import CustomersScreen


class _FakeCustomerService:
    def list_places_for_selection(self):
        return []

    def list_records(self, spec, keyword="", limit=500):
        return []


@pytest.fixture
def customer_screen():
    app = QApplication.instance() or QApplication([])
    _ = app
    return CustomersScreen(_FakeCustomerService())


@pytest.mark.parametrize(
    ("raw_phone", "expected"),
    [
        ("01012345678", "201012345678"),
        ("1012345678", "201012345678"),
        ("201012345678", "201012345678"),
        ("+20 (10) 1234-5678", "201012345678"),
        ("01112345678", "201112345678"),
        ("1212345678", "201212345678"),
        ("201512345678", "201512345678"),
    ],
)
def test_normalize_egyptian_phone_accepts_supported_mobile_formats(raw_phone, expected):
    assert normalize_egyptian_phone(raw_phone) == expected


@pytest.mark.parametrize(
    "raw_phone",
    [
        None,
        "",
        " +()-[]{} ",
        "01312345678",
        "201312345678",
        "0101234567",
        "010123456789",
        "0101234ABCD",
        "٠١٠١٢٣٤٥٦٧٨",
    ],
)
def test_normalize_egyptian_phone_rejects_empty_or_invalid_numbers(raw_phone):
    with pytest.raises(ValueError):
        normalize_egyptian_phone(raw_phone)


def test_build_whatsapp_url_normalizes_phone_and_percent_encodes_default_message():
    url = whatsapp_service.build_whatsapp_url("01012345678")

    assert url == (
        "https://web.whatsapp.com/send?phone=201012345678&"
        "text=%D8%A7%D9%84%D8%B3%D9%84%D8%A7%D9%85%20%D8%B9%D9%84%D9%8A%D9%83%D9%85"
    )
    assert "send=" not in url


def test_open_whatsapp_web_uses_default_browser_without_sending(monkeypatch):
    opened = []
    monkeypatch.setattr(
        whatsapp_service.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    url = whatsapp_service.open_whatsapp_web("1012345678")

    assert opened == [(url, 2)]
    assert "phone=201012345678" in url


def test_open_whatsapp_web_raises_when_browser_rejects_launch(monkeypatch):
    monkeypatch.setattr(whatsapp_service.webbrowser, "open", lambda *args, **kwargs: False)

    with pytest.raises(RuntimeError, match="default browser"):
        whatsapp_service.open_whatsapp_web("01012345678")


def test_open_whatsapp_web_wraps_browser_launch_errors(monkeypatch):
    def fail_to_open(*args, **kwargs):
        raise OSError("browser unavailable")

    monkeypatch.setattr(whatsapp_service.webbrowser, "open", fail_to_open)

    with pytest.raises(RuntimeError, match="default browser"):
        whatsapp_service.open_whatsapp_web("01012345678")


def test_press_enter_key_sends_windows_key_down_and_key_up(monkeypatch):
    events = []
    fake_user32 = SimpleNamespace(keybd_event=lambda *args: events.append(args))
    monkeypatch.setattr(
        whatsapp_service,
        "ctypes",
        SimpleNamespace(windll=SimpleNamespace(user32=fake_user32)),
        raising=False,
    )

    whatsapp_service.press_enter_key()

    assert events == [
        (0x0D, 0, 0, 0),
        (0x0D, 0, 0x0002, 0),
    ]


def test_press_enter_key_wraps_windows_input_errors(monkeypatch):
    def fail_key_event(*args):
        raise OSError("input unavailable")

    fake_user32 = SimpleNamespace(keybd_event=fail_key_event)
    monkeypatch.setattr(
        whatsapp_service,
        "ctypes",
        SimpleNamespace(windll=SimpleNamespace(user32=fake_user32)),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="Enter key"):
        whatsapp_service.press_enter_key()


def test_customers_screen_adds_whatsapp_button_to_data_entry_area(customer_screen):
    assert customer_screen.whatsapp_button.text() == "WhatsApp"
    assert customer_screen.whatsapp_button.parentWidget() is customer_screen.form_box


@pytest.mark.parametrize("mode", ["view", "new", "edit"])
def test_whatsapp_button_remains_available_in_all_customer_modes(customer_screen, mode):
    customer_screen.set_mode(mode)

    assert customer_screen.whatsapp_button.isEnabled()


def test_whatsapp_button_uses_current_phone_field(customer_screen, monkeypatch):
    opened = []
    monkeypatch.setattr(
        customers_module.whatsapp_service,
        "open_whatsapp_web",
        lambda phone: opened.append(phone) or "https://web.whatsapp.com/",
    )
    monkeypatch.setattr(
        customers_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: None),
    )
    customer_screen.inputs["phone_number"].setText("+20 (10) 1234-5678")

    customer_screen.whatsapp_button.click()

    assert opened == ["+20 (10) 1234-5678"]


def test_whatsapp_button_schedules_automatic_send_after_browser_load(customer_screen, monkeypatch):
    scheduled = []
    pressed = []
    messages = []
    monkeypatch.setattr(customers_module.whatsapp_service, "open_whatsapp_web", lambda phone: "url")
    monkeypatch.setattr(
        customers_module.whatsapp_service,
        "press_enter_key",
        lambda: pressed.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        customers_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: scheduled.append((delay, callback))),
        raising=False,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, message: messages.append((parent, title, message)),
    )
    customer_screen.inputs["phone_number"].setText("01012345678")

    customer_screen.whatsapp_button.click()

    assert len(scheduled) == 1
    assert scheduled[0][0] == 25_000
    assert not customer_screen.whatsapp_button.isEnabled()
    scheduled[0][1]()
    assert pressed == [True]
    assert messages == [
        (customer_screen, "تم الإرسال", "تم إرسال رسالة واتساب بنجاح."),
    ]
    assert customer_screen.whatsapp_button.isEnabled()
    assert customer_screen.whatsapp_button.text() == "WhatsApp"


def test_whatsapp_button_warns_when_phone_is_empty(customer_screen, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((parent, title, message)),
    )

    customer_screen.whatsapp_button.click()

    assert warnings == [
        (customer_screen, "تعذر فتح واتساب", "يرجى إدخال رقم الهاتف أولاً."),
    ]


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (ValueError("invalid"), "رقم الهاتف غير صالح. يرجى إدخال رقم موبايل مصري صحيح."),
        (RuntimeError("browser"), "تعذر فتح واتساب في المتصفح الافتراضي."),
    ],
)
def test_whatsapp_button_warns_for_invalid_phone_or_launch_failure(
    customer_screen,
    monkeypatch,
    error,
    expected_message,
):
    warnings = []

    def fail_to_open(phone):
        raise error

    monkeypatch.setattr(customers_module.whatsapp_service, "open_whatsapp_web", fail_to_open)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((parent, title, message)),
    )
    customer_screen.inputs["phone_number"].setText("01012345678")

    customer_screen.whatsapp_button.click()

    assert warnings == [(customer_screen, "تعذر فتح واتساب", expected_message)]


def test_whatsapp_button_warns_when_automatic_send_fails(customer_screen, monkeypatch):
    scheduled = []
    warnings = []
    monkeypatch.setattr(customers_module.whatsapp_service, "open_whatsapp_web", lambda phone: "url")
    monkeypatch.setattr(
        customers_module.whatsapp_service,
        "press_enter_key",
        lambda: (_ for _ in ()).throw(RuntimeError("send failed")),
        raising=False,
    )
    monkeypatch.setattr(
        customers_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: scheduled.append(callback)),
        raising=False,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((parent, title, message)),
    )
    customer_screen.inputs["phone_number"].setText("01012345678")

    customer_screen.whatsapp_button.click()
    scheduled[0]()

    assert warnings == [
        (
            customer_screen,
            "تعذر إرسال رسالة واتساب",
            "تم فتح واتساب، لكن تعذر الضغط على زر الإرسال تلقائيًا.",
        )
    ]
