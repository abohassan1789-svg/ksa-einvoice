"""The installer's Backup Folder page must actually reach BackupService.

Three pieces have to line up, and each is silent when it doesn't:

1. the wizard runs ``phaseinv2-config.exe set-backup-dir`` → writes
   ``backup_dir`` into ``%ProgramData%\\PHASEINV2\\backup_settings.json``,
2. ``entrypoints/crm_app.py`` reads that key on every frozen launch and exports
   it as ``BACKUP_DIR``,
3. ``BackupService`` reads ``BACKUP_DIR``.

Step 2 is the fragile one. crm_app previously did
``os.environ.setdefault("BACKUP_DIR", <ProgramData>\\Backups)`` unconditionally,
so a chosen folder would have been overridden on every launch and the wizard page
would have been pure decoration — the install would still "succeed" and backups
would still be written, just never where the customer asked. Nothing would have
raised.

No freezing and no database: the entrypoint is imported by path and its helpers
called directly.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ENTRYPOINT = (
    Path(__file__).resolve().parents[2] / "installer" / "entrypoints" / "crm_app.py"
)


def _load_crm_app():
    """Import installer/entrypoints/crm_app.py (outside the app package)."""
    spec = importlib.util.spec_from_file_location("_crm_app_entry", _ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_entrypoint_file_exists() -> None:
    assert _ENTRYPOINT.is_file(), f"missing frozen entry point: {_ENTRYPOINT}"


def test_reads_the_folder_the_installer_recorded(tmp_path: Path) -> None:
    settings = tmp_path / "backup_settings.json"
    chosen = tmp_path / "D_Backups"
    settings.write_text(
        json.dumps({"backup_dir": str(chosen), "max_backups": 7}), encoding="utf-8"
    )
    assert _load_crm_app()._chosen_backup_dir(str(settings)) == str(chosen)


def test_arabic_path_survives_the_round_trip(tmp_path: Path) -> None:
    """The folder is customer-chosen, so it can be non-ASCII. UTF-8 both ways."""
    settings = tmp_path / "backup_settings.json"
    chosen = tmp_path / "نسخ احتياطية"
    settings.write_text(json.dumps({"backup_dir": str(chosen)}, ensure_ascii=False),
                        encoding="utf-8")
    assert _load_crm_app()._chosen_backup_dir(str(settings)) == str(chosen)


@pytest.mark.parametrize(
    "content",
    ["", "not json at all", "[]", '"a string"', "{}", '{"backup_dir": ""}',
     '{"backup_dir": null}', '{"backup_dir": "   "}'],
)
def test_missing_or_junk_settings_fall_back_to_the_default(tmp_path: Path, content: str) -> None:
    """Never raise on the startup path — an empty answer means "use the default"."""
    settings = tmp_path / "backup_settings.json"
    settings.write_text(content, encoding="utf-8")
    assert _load_crm_app()._chosen_backup_dir(str(settings)) == ""


def test_absent_file_falls_back_to_the_default(tmp_path: Path) -> None:
    assert _load_crm_app()._chosen_backup_dir(str(tmp_path / "nope.json")) == ""


def test_cli_writes_a_key_the_entrypoint_can_read(tmp_path: Path) -> None:
    """End-to-end across the seam: the CLI writes it, the entrypoint reads it.

    The two sides agree on a literal key name in two different files, so pin the
    contract rather than trusting that both were updated together.
    """
    from app.config.configure_cli import main as cli_main

    settings = tmp_path / "backup_settings.json"
    chosen = tmp_path / "chosen"
    assert cli_main(["set-backup-dir", "--path", str(chosen), "--out", str(settings)]) == 0
    assert _load_crm_app()._chosen_backup_dir(str(settings)) == str(chosen)
    assert chosen.is_dir(), "set-backup-dir must create the folder it records"


def test_cli_merges_instead_of_resetting_other_settings(tmp_path: Path) -> None:
    """A re-install must not turn auto-backups back on or reset retention."""
    from app.config.configure_cli import main as cli_main

    settings = tmp_path / "backup_settings.json"
    settings.write_text(
        json.dumps({"backup_on_startup": False, "backup_on_shutdown": False,
                    "max_backups": 7}),
        encoding="utf-8",
    )
    assert cli_main(["set-backup-dir", "--path", str(tmp_path / "b"), "--out", str(settings)]) == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["backup_on_startup"] is False
    assert data["backup_on_shutdown"] is False
    assert data["max_backups"] == 7
    assert data["backup_dir"] == str(tmp_path / "b")


def test_backup_service_honours_the_env_var_the_entrypoint_exports(tmp_path: Path, monkeypatch) -> None:
    """Close the loop: BACKUP_DIR is what BackupService actually writes to."""
    from app.services.backup_service import BackupService

    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "from_env"))
    service = BackupService(repository=object())
    assert service.backup_dir == str(tmp_path / "from_env")
