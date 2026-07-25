"""Contract tests for the PHASEINV2 Main Device installer.

These validate the installer/build scripts encode the required behaviour without
needing the Inno Setup toolchain, PyInstaller output, or PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"
ISS = INSTALLER / "PHASEINV2-MainDevice.iss"
BUILD = INSTALLER / "build_main_device.ps1"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8")


def test_main_device_installer_files_exist():
    assert ISS.exists()
    assert BUILD.exists()


def test_installer_offers_local_and_internal_server_modes():
    text = _iss()
    assert "Local Mode" in text
    assert "Internal Server Mode" in text
    assert "TInputOptionWizardPage" in text


def test_internal_server_mode_rejects_loopback():
    text = _iss()
    assert "function IsLoopbackAddress" in text
    assert "127.0.0.1" in text
    assert "localhost" in text
    assert "cannot be localhost" in text


def test_local_mode_forces_localhost():
    text = _iss()
    # Local mode always talks to the local server.
    assert "DbHost := '127.0.0.1';" in text


def test_installer_tests_connection_before_finishing():
    text = _iss()
    assert "test-db" in text
    # A pre-flight test on the server page and an authoritative test at install.
    assert "preflight-test" in text
    assert "connection-test" in text


def test_installer_creates_database_only_if_missing_and_never_drops():
    text = _iss()
    assert "create-db --db" in text
    upper = text.upper()
    for forbidden in ("DROP DATABASE", "DROP TABLE", "TRUNCATE", "DROP SCHEMA"):
        assert forbidden not in upper


def test_installer_provisions_schema_and_seeds():
    text = _iss()
    assert "provision --db" in text


def test_installer_writes_profile_to_phaseinv2_programdata():
    text = _iss()
    assert r"{commonappdata}\PHASEINV2\connection.json" in text
    assert "write --mode lan" in text


def test_installer_backs_up_existing_profile_before_replacing():
    text = _iss()
    assert "BackupExistingProfile" in text
    assert ".bak-" in text


def test_installer_does_not_bundle_or_install_postgresql():
    """PostgreSQL is installed manually by the customer (decision 2026-07-15).

    The inverse of what this file asserted until then. Bundling it back would
    silently re-add ~374 MB and re-introduce an unattended install of a database
    engine onto a machine that may already have one.
    """
    text = _iss()
    assert "postgresql-windows-x64.exe" not in text
    assert "InstallBundledPostgreSQLIfNeeded" not in text
    assert "--optionfile" not in text  # the bundled installer's secret-passing
    assert "superpassword=" not in text


def test_local_mode_pre_flight_tests_the_connection():
    """Local Mode must verify PostgreSQL is actually there.

    It never had to before — the wizard would just install an engine if none was
    found. Now a customer who has not installed PostgreSQL has to learn it on
    this page, not after a "successful" install.
    """
    text = _iss()
    assert "preflight-test-local" in text
    assert "does not install PostgreSQL" in text


def test_installer_never_puts_password_on_command_line_or_log():
    text = _iss()
    # All DB operations use a password file; the raw password is never a CLI arg.
    assert "--password-file" in text
    assert '--password "' not in text
    assert "password not logged" in text


def test_installer_asks_for_a_backup_folder_and_wires_it_to_the_app():
    """The Backup Folder page must reach the application, not just look nice.

    Each of these is a link in the one chain that makes it real; see
    tests/unit/test_installer_backup_dir_wiring.py for the app-side half.
    """
    text = _iss()
    assert "CreateInputDirPage" in text          # the page exists
    assert "set-backup-dir --path" in text       # it is recorded via the CLI
    assert r"{commonappdata}\PHASEINV2\backup_settings.json" in text
    # The chosen folder must be writable by a standard user, or the app's
    # un-elevated automatic backups silently produce nothing.
    assert r'Name: "{code:GetBackupDir}"; Permissions: users-modify' in text


def test_installer_has_optional_desktop_shortcut_and_start_menu():
    text = _iss()
    assert "Name: \"desktopicon\"" in text
    assert "Tasks: desktopicon" in text  # desktop shortcut is optional
    assert r"{group}\{#MyAppShortName}" in text  # Start Menu shortcut


def test_installer_has_uninstaller_and_preserves_programdata():
    text = _iss()
    assert "UninstallDisplayName" in text
    # ProgramData (profile/logs/backups) is created but never scheduled for delete.
    assert "UninstallDelete" not in text


def test_installer_allows_install_dir_selection_and_stable_appid():
    text = _iss()
    assert "DisableDirPage=no" in text
    assert "AppId={{B8D31F42-6A7C-4E59-9F30-1C2E4A6B8D01}" in text
    assert "DefaultDirName={autopf}\\PHASEINV2" in text


def test_installer_output_and_branding():
    text = _iss()
    assert "OutputBaseFilename=PHASEINV2_MainDevice_Setup" in text
    assert '#define MyAppExeName "PHASEINV2.exe"' in text
    assert "PrivilegesRequired=admin" in text


def test_installer_logs_to_programdata_without_password():
    text = _iss()
    assert r"{commonappdata}\PHASEINV2\logs" in text
    assert "AppendInstallLog" in text


def test_build_script_produces_final_installer_and_reports_checksum():
    text = BUILD.read_text(encoding="utf-8")
    assert "PHASEINV2_MainDevice_Setup.exe" in text
    assert "PyInstaller" in text
    assert "SHA256" in text or "SHA-256" in text
    assert "PHASEINV2-MainDevice.iss" in text
    # Final EXE is copied to the repo-level dist folder.
    assert "FinalDistDir" in text and "dist" in text


def test_configure_cli_exposes_create_db_and_provision():
    text = (ROOT / "app" / "config" / "configure_cli.py").read_text(encoding="utf-8")
    assert '"create-db"' in text
    assert '"provision"' in text
    # create-db must be non-destructive.
    assert "CREATE DATABASE" in text
    upper = text.upper()
    assert "DROP DATABASE" not in upper


def test_app_spec_collects_app_as_real_package():
    # Guards the fix for `No module named 'app.main'`: the `app` package must be
    # collected as real files so the `_internal/app` data folder cannot shadow it.
    spec = (INSTALLER / "PHASEINV2-app.spec").read_text(encoding="utf-8")
    assert "module_collection_mode" in spec
    assert "'app'" in spec
    assert "collect_submodules('app')" in spec


def test_build_freezes_app_from_spec():
    text = BUILD.read_text(encoding="utf-8")
    assert "PHASEINV2-app.spec" in text


def test_entrypoint_logs_startup_errors_and_no_exe_path_shadow():
    src = (INSTALLER / "entrypoints" / "crm_app.py").read_text(encoding="utf-8")
    assert "app-startup-error.log" in src
    assert "_log_startup_error" in src
    # When frozen we must NOT prepend the executable dir to sys.path.
    assert "Do NOT prepend the executable" in src or "can shadow the packaged" in src


def test_installer_db_setup_is_resilient():
    text = _iss()
    # Retries the connection test (fresh PostgreSQL service may be slow to accept)
    # and warns-and-continues instead of a fatal runtime error.
    assert "RunConfigLoggedRetry" in text
    assert "WarnContinue" in text
    # The profile is written even if the DB is unreachable at install time.
    assert "even if the DB is not reachable" in text


def test_connection_config_prefers_phaseinv2_programdata():
    text = (ROOT / "app" / "config" / "connection_config.py").read_text(encoding="utf-8")
    assert 'BRAND_DIRNAME = "PHASEINV2"' in text
    assert 'CONFIG_FILENAME = "connection.json"' in text
    # Legacy path still readable for upgrades.
    assert "LEGACY_BRAND_DIRNAME" in text
    assert "CRM_CONFIG_PATH" in text
