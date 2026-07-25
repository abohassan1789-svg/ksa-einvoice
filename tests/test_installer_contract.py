from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"


def test_bundled_current_data_dump_exists_and_is_not_empty():
    dump = INSTALLER / "assets" / "data" / "crm-current-data.sql"
    assert dump.exists()
    assert dump.stat().st_size > 1_000_000


def test_main_server_installer_build_files_exist():
    assert (INSTALLER / "build.ps1").exists()
    assert (INSTALLER / "CRM-Main-Server.iss").exists()


def test_client_installer_build_file_exists():
    assert (INSTALLER / "CRM-Client.iss").exists()


def test_build_script_supports_client_installer_output():
    build_text = (INSTALLER / "build.ps1").read_text(encoding="utf-8")
    assert "[switch]$Client" in build_text
    assert "CRM-Client.iss" in build_text
    assert "CRM-CLIENT-SUB-V4.0.1-Setup.exe" in build_text
    assert "client installer must not include the PostgreSQL prerequisite" in build_text


def test_installer_sources_reference_postgresql_default_port():
    build_text = (INSTALLER / "CRM-Main-Server.iss").read_text(encoding="utf-8")
    readme_text = (INSTALLER / "README_BUILD.md").read_text(encoding="utf-8")
    assert "5432" in build_text
    assert "5432" in readme_text
    assert "8000" not in build_text


def test_installer_is_branded_as_crm_main_v4():
    build_text = (INSTALLER / "CRM-Main-Server.iss").read_text(encoding="utf-8")
    assert '#define MyAppName "CRM MAIN V4"' in build_text
    assert '#define MyAppVersion "4.0.0"' in build_text
    assert "OutputBaseFilename=CRM-MAIN-V4-Setup" in build_text


def test_installer_always_allows_install_directory_selection():
    build_text = (INSTALLER / "CRM-Main-Server.iss").read_text(encoding="utf-8")
    assert "DisableDirPage=no" in build_text
    assert "UsePreviousAppDir=no" in build_text


def test_installer_always_creates_desktop_shortcut():
    build_text = (INSTALLER / "CRM-Main-Server.iss").read_text(encoding="utf-8")
    assert 'Name: "{commondesktop}\\CRM MAIN V4"; Filename: "{app}\\{#MyAppExeName}"' in build_text
    assert 'Tasks: desktopicon' not in build_text


def test_installer_skips_bundled_postgresql_when_existing_install_detected():
    build_text = (INSTALLER / "CRM-Main-Server.iss").read_text(encoding="utf-8")
    assert "function PostgreSQLLooksInstalled" in build_text
    assert "InstallBundledPostgreSQLIfNeeded" in build_text
    assert "PostgreSQL already appears to be installed" in build_text


def test_restore_script_keeps_existing_database_data():
    restore_text = (INSTALLER / "scripts" / "restore_database.ps1").read_text(encoding="utf-8")
    assert "already has $tableCount public tables" in restore_text
    assert "exit 0" in restore_text
    forbidden = ["DROP DATABASE", "DROP TABLE", "TRUNCATE"]
    upper = restore_text.upper()
    for word in forbidden:
        assert word not in upper


def test_postgres_setup_scripts_accept_password_file():
    for name in ["configure_postgres.ps1", "restore_database.ps1"]:
        text = (INSTALLER / "scripts" / name).read_text(encoding="utf-8")
        assert "SuperUserPasswordFile" in text
        assert "Get-Content -Raw" in text


def test_client_installer_is_branded_as_sub_device_v401():
    build_text = (INSTALLER / "CRM-Client.iss").read_text(encoding="utf-8")
    assert '#define MyAppName "CRM CLIENT SUB V4.0.1"' in build_text
    assert '#define MyAppVersion "4.0.1"' in build_text
    assert "OutputBaseFilename=CRM-CLIENT-SUB-V4.0.1-Setup" in build_text


def test_client_installer_writes_connection_profile_to_main_server():
    build_text = (INSTALLER / "CRM-Client.iss").read_text(encoding="utf-8")
    assert "Main Server Connection" in build_text
    assert "test-db --host" in build_text
    assert "write --mode lan --address" in build_text
    assert "{commonappdata}\\CRM\\crm-connection.json" in build_text


def test_client_installer_rejects_loopback_server_addresses():
    build_text = (INSTALLER / "CRM-Client.iss").read_text(encoding="utf-8")
    assert "function IsLoopbackAddress" in build_text
    assert "127.0.0.1" in build_text
    assert "localhost" in build_text
    assert "cannot be localhost" in build_text


def test_client_installer_does_not_install_or_configure_postgresql():
    build_text = (INSTALLER / "CRM-Client.iss").read_text(encoding="utf-8")
    forbidden = [
        "postgresql-windows-x64.exe",
        "configure_postgres.ps1",
        "restore_database.ps1",
        "firewall.ps1",
        "crm-current-data.sql",
        "InstallBundledPostgreSQL",
        "Bundled CRM data restore",
    ]
    for text in forbidden:
        assert text not in build_text


def test_client_installer_allows_path_and_creates_shortcut():
    build_text = (INSTALLER / "CRM-Client.iss").read_text(encoding="utf-8")
    assert "DisableDirPage=no" in build_text
    assert "UsePreviousAppDir=no" in build_text
    assert 'Name: "{commondesktop}\\CRM CLIENT SUB V4.0.1"; Filename: "{app}\\{#MyAppExeName}"' in build_text
    assert "Tasks: desktopicon" not in build_text
