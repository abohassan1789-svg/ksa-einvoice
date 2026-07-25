"""Tests for the connection profile, credential encryption, and network checks.

These cover the installer/first-run plumbing without requiring PostgreSQL, Qt,
or the Inno Setup toolchain.
"""

from __future__ import annotations

import json

import pytest

from app.config import connection_config as cc
from app.config import crypto
from app.services import network_service as net


# --- crypto -----------------------------------------------------------------


def test_encrypt_is_not_plaintext_and_round_trips():
    token = crypto.encrypt("s3cr3t!")
    assert token != "s3cr3t!"
    assert crypto.is_encrypted(token)
    assert crypto.decrypt(token) == "s3cr3t!"


def test_decrypt_empty_is_empty():
    assert crypto.decrypt("") == ""
    assert crypto.decrypt(None) == ""


def test_legacy_plaintext_passthrough():
    # An unprefixed value is treated as already-plain (backward compatible).
    assert crypto.decrypt("legacy-plain") == "legacy-plain"


# --- profile validation -----------------------------------------------------


def test_validate_flags_missing_address_and_bad_port():
    p = cc.ConnectionProfile(server_address="", server_port=70000)
    errors = p.validate()
    assert any("address" in e.lower() for e in errors)
    assert any("port" in e.lower() for e in errors)


def test_valid_profile_has_no_errors():
    p = cc.ConnectionProfile(server_address="192.168.1.10", server_port=5432)
    assert p.validate() == []


def test_default_port_is_postgresql_default():
    assert cc.DEFAULT_PORT == 5432
    assert cc.ConnectionProfile().server_port == 5432


def test_database_url_uses_effective_host_and_port():
    p = cc.ConnectionProfile(
        connection_mode=cc.MODE_CLOUD, server_address="ignored",
        cloud_hostname="erp.example.com", server_port=5432,
        db_name="crm", db_user="postgres", db_password="p@ss word",
    )
    url = p.database_url()
    assert "erp.example.com:5432" in url
    assert "/crm" in url
    # Password special characters must be URL-encoded.
    assert "p%40ss" in url


# --- save / load round-trip -------------------------------------------------


def test_save_load_round_trip_encrypts_password(tmp_path, monkeypatch):
    target = tmp_path / "crm-connection.json"
    monkeypatch.setenv("CRM_CONFIG_PATH", str(target))

    original = cc.ConnectionProfile(
        connection_mode=cc.MODE_LAN, server_address="10.0.0.5", server_port=5432,
        db_name="crm", db_user="postgres", db_password="hunter2",
    )
    cc.save(original)

    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["database"]["password"] != "hunter2"  # never plaintext
    assert on_disk["server"]["port"] == 5432

    loaded = cc.load()
    assert loaded is not None
    assert loaded.db_password == "hunter2"
    assert loaded.server_address == "10.0.0.5"


def test_windows_profile_acl_allows_standard_users_to_read():
    grants = cc._windows_profile_acl_grants(current_user="")
    assert "*S-1-5-18:F" in grants  # LocalSystem
    assert "*S-1-5-32-544:F" in grants  # Builtin Administrators
    assert "*S-1-5-32-545:RX" in grants  # Builtin Users


def test_export_client_profile_omits_password(tmp_path):
    p = cc.ConnectionProfile(
        server_address="10.0.0.5", server_port=5432, db_password="hunter2",
    )
    out = tmp_path / "client.json"
    cc.export_client_profile(p, path=out, include_password=False)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["database"]["password"] == ""
    assert data["password_required"] is True


def test_export_with_password_requires_passphrase(tmp_path):
    p = cc.ConnectionProfile(server_address="10.0.0.5", db_password="x")
    with pytest.raises(ValueError):
        cc.export_client_profile(p, path=tmp_path / "c.json", include_password=True)


# --- network checks ---------------------------------------------------------


def test_detect_ipv4_excludes_loopback():
    for ip in net.detect_ipv4_addresses():
        assert not ip.startswith("127.")
        assert not ip.startswith("169.254.")


def test_is_port_available_rejects_out_of_range():
    assert net.is_port_available(0).ok is False
    assert net.is_port_available(70000).ok is False


def test_is_port_available_true_for_free_port():
    # Bind an ephemeral port, release it, then confirm it reports available.
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    assert net.is_port_available(free_port, address="127.0.0.1").ok is True


def test_can_reach_detects_open_and_closed():
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert net.can_reach("127.0.0.1", port, timeout=1).ok is True
    finally:
        srv.close()
    # Now closed.
    assert net.can_reach("127.0.0.1", port, timeout=1).ok is False


def test_ensure_connection_prompts_when_saved_profile_is_unreachable(monkeypatch):
    from app.ui.screens import connection_screen

    profile = cc.ConnectionProfile(
        server_address="192.168.1.5",
        server_port=5432,
        db_name="crm",
        db_user="postgres",
        db_password="secret",
    )
    dialog_calls = []

    class FakeDialog:
        def __init__(self, parent=None):
            dialog_calls.append(parent)

        def exec(self):
            return connection_screen.QDialog.Accepted

    def fake_test_database(**kwargs):
        assert kwargs["host"] == "192.168.1.5"
        assert kwargs["port"] == 5432
        return net.CheckResult(False, "not reachable")

    monkeypatch.setattr(connection_screen.cc, "load", lambda: profile)
    monkeypatch.setattr(connection_screen.net, "test_database", fake_test_database)
    monkeypatch.setattr(connection_screen, "FirstRunConnectionDialog", FakeDialog)

    assert connection_screen.ensure_connection(None) is True
    assert dialog_calls == [None]


def test_first_run_save_requires_reachable_connection():
    from app.ui.screens import connection_screen

    calls = []

    class FakeWidget:
        def save(self, require_reachable=False):
            calls.append(require_reachable)
            return True

    class FakeDialog:
        widget = FakeWidget()
        accepted = False

        def accept(self):
            self.accepted = True

    dialog = FakeDialog()
    connection_screen.FirstRunConnectionDialog._save_and_close(dialog)

    assert calls == [True]
    assert dialog.accepted is True
