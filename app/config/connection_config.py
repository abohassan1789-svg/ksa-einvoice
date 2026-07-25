"""The CRM connection profile — the single source of truth for *how this
machine reaches the CRM server* (the central PostgreSQL host).

Both the installer wizard and the running application read and write this file,
so future hostname/port changes are done from the ERP Settings screen without
reinstalling, and a Client installer can import the same file.

File format (JSON, stable ``schema_version``)::

    {
      "schema_version": 1,
      "connection_mode": "lan",          # "lan" | "cloud"
      "server": {"address": "192.168.1.10", "port": 5432},
      "database": {
        "name": "crm",
        "user": "postgres",
        "password": "dpapi:...",         # NEVER plain text (see crypto.py)
        "sslmode": "prefer"
      },
      "cloud": {                          # only meaningful when mode == "cloud"
        "hostname": "erp.company.com",
        "https": true,
        "reverse_proxy": true,
        "ssl_cert_path": ""
      },
      "updated_at": "2026-07-08T10:00:00"
    }

Resolution order for the file path (first that exists / is writable wins):

1. ``$PHASEINV2_CONFIG_PATH`` / ``$CRM_CONFIG_PATH``  explicit override
   (installer + tests use this; both names accepted for compatibility)
2. ``%ProgramData%\\PHASEINV2\\connection.json``   machine-wide, survives updates
3. ``%ProgramData%\\CRM\\crm-connection.json``     legacy machine-wide location
4. ``<app_root>/config/connection.json``           dev / portable fallback
5. ``<app_root>/config/crm-connection.json``       legacy dev / portable fallback

New profiles are always written to the ``PHASEINV2`` location (2). The legacy
``CRM`` locations remain readable so an already-configured machine keeps working
after an upgrade. No IP address or password is ever hard-coded here: everything
comes from the values the administrator chose.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import crypto

SCHEMA_VERSION = 1
# Current (PHASEINV2) machine-wide brand directory + profile filename, plus the
# legacy (CRM) names kept only for backward-compatible reads.
BRAND_DIRNAME = "PHASEINV2"
LEGACY_BRAND_DIRNAME = "CRM"
CONFIG_FILENAME = "connection.json"
LEGACY_CONFIG_FILENAME = "crm-connection.json"
CLIENT_PROFILE_FILENAME = "crm-client-profile.json"

MODE_LAN = "lan"
MODE_CLOUD = "cloud"
DEFAULT_PORT = 5432  # PostgreSQL listening port advertised to clients.


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _app_root() -> Path:
    # app/config/connection_config.py -> project root is three parents up.
    return Path(__file__).resolve().parents[2]


def _programdata_dir(brand: str) -> Path | None:
    """``%ProgramData%\\<brand>`` on Windows, else ``None``."""
    program_data = os.getenv("ProgramData")
    if os.name == "nt" and program_data:
        return Path(program_data) / brand
    return None


def default_config_dir() -> Path:
    """Preferred machine-wide directory for new profiles (current brand)."""
    machine = _programdata_dir(BRAND_DIRNAME)
    if machine is not None:
        return machine
    return _app_root() / "config"


def _candidate_paths() -> list[Path]:
    """Existing-profile lookup order (newest brand first, then legacy)."""
    candidates: list[Path] = []
    new_machine = _programdata_dir(BRAND_DIRNAME)
    if new_machine is not None:
        candidates.append(new_machine / CONFIG_FILENAME)
    legacy_machine = _programdata_dir(LEGACY_BRAND_DIRNAME)
    if legacy_machine is not None:
        candidates.append(legacy_machine / LEGACY_CONFIG_FILENAME)
    config_dir = _app_root() / "config"
    candidates.append(config_dir / CONFIG_FILENAME)
    candidates.append(config_dir / LEGACY_CONFIG_FILENAME)
    return candidates


def config_path() -> Path:
    """Resolve the profile path without creating anything."""
    override = os.getenv("PHASEINV2_CONFIG_PATH") or os.getenv("CRM_CONFIG_PATH")
    if override:
        return Path(override)

    for candidate in _candidate_paths():
        if candidate.exists():
            return candidate

    # Nothing on disk yet -> prefer the current machine-wide location for writes.
    return default_config_dir() / CONFIG_FILENAME


def config_exists() -> bool:
    return config_path().exists()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ConnectionProfile:
    connection_mode: str = MODE_LAN
    server_address: str = ""
    server_port: int = DEFAULT_PORT
    db_name: str = "crm"
    db_user: str = "postgres"
    db_password: str = ""  # held in memory as plain text; encrypted on save
    sslmode: str = "prefer"
    # Cloud-only metadata.
    cloud_hostname: str = ""
    https: bool = False
    reverse_proxy: bool = False
    ssl_cert_path: str = ""
    updated_at: str = ""

    # ---- validation ----------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty == valid)."""
        errors: list[str] = []
        if self.connection_mode not in (MODE_LAN, MODE_CLOUD):
            errors.append(f"Unknown connection mode: {self.connection_mode!r}")
        if not str(self.server_address).strip():
            errors.append("Server address is required.")
        if not (1 <= int(self.server_port) <= 65535):
            errors.append("Port must be between 1 and 65535.")
        if not str(self.db_name).strip():
            errors.append("Database name is required.")
        if not str(self.db_user).strip():
            errors.append("Database user is required.")
        return errors

    # ---- serialization -------------------------------------------------

    def to_dict(self, passphrase: str | None = None) -> dict[str, Any]:
        """Serialize with the password encrypted at rest."""
        encrypted = crypto.encrypt(self.db_password or "", passphrase=passphrase)
        return {
            "schema_version": SCHEMA_VERSION,
            "connection_mode": self.connection_mode,
            "server": {"address": self.server_address, "port": int(self.server_port)},
            "database": {
                "name": self.db_name,
                "user": self.db_user,
                "password": encrypted,
                "sslmode": self.sslmode,
            },
            "cloud": {
                "hostname": self.cloud_hostname,
                "https": bool(self.https),
                "reverse_proxy": bool(self.reverse_proxy),
                "ssl_cert_path": self.ssl_cert_path,
            },
            "updated_at": self.updated_at or _now_iso(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], passphrase: str | None = None) -> "ConnectionProfile":
        server = data.get("server", {}) or {}
        database = data.get("database", {}) or {}
        cloud = data.get("cloud", {}) or {}
        stored_pw = database.get("password", "") or ""
        try:
            password = crypto.decrypt(stored_pw, passphrase=passphrase)
        except Exception:
            # Wrong/absent passphrase or unreadable blob -> leave blank so the UI
            # can prompt for it rather than crashing.
            password = ""
        return cls(
            connection_mode=data.get("connection_mode", MODE_LAN),
            server_address=server.get("address", ""),
            server_port=int(server.get("port", DEFAULT_PORT) or DEFAULT_PORT),
            db_name=database.get("name", "crm"),
            db_user=database.get("user", "postgres"),
            db_password=password,
            sslmode=database.get("sslmode", "prefer"),
            cloud_hostname=cloud.get("hostname", ""),
            https=bool(cloud.get("https", False)),
            reverse_proxy=bool(cloud.get("reverse_proxy", False)),
            ssl_cert_path=cloud.get("ssl_cert_path", ""),
            updated_at=data.get("updated_at", ""),
        )

    # ---- convenience ---------------------------------------------------

    @property
    def effective_host(self) -> str:
        """The host the client actually dials."""
        if self.connection_mode == MODE_CLOUD and self.cloud_hostname:
            return self.cloud_hostname
        return self.server_address

    def database_url(self) -> str:
        """Build a SQLAlchemy URL (psycopg driver) from the profile."""
        from urllib.parse import quote_plus

        user = quote_plus(self.db_user)
        pw = quote_plus(self.db_password or "")
        auth = f"{user}:{pw}" if pw else user
        return (
            f"postgresql+psycopg://{auth}@{self.effective_host}:{int(self.server_port)}"
            f"/{self.db_name}?sslmode={self.sslmode}"
        )


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load(passphrase: str | None = None) -> ConnectionProfile | None:
    """Load the profile, or ``None`` if no config file exists."""
    path = config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return ConnectionProfile.from_dict(data, passphrase=passphrase)


def save(profile: ConnectionProfile, path: Path | None = None, passphrase: str | None = None) -> Path:
    """Atomically write the profile (0600-ish) and return the path written."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    profile.updated_at = _now_iso()
    payload = json.dumps(profile.to_dict(passphrase=passphrase), indent=2, ensure_ascii=False)

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)  # atomic on Windows and POSIX
    _restrict_permissions(target)  # harden the final file, not the temp
    return target


def export_client_profile(
    profile: ConnectionProfile,
    path: Path | None = None,
    include_password: bool = False,
    passphrase: str | None = None,
) -> Path:
    """Write a portable profile a Client installer can import.

    By default the password is omitted (the client enters it or uses its own
    login), which is the safest option. If *include_password* is set, a
    *passphrase* is required so the secret travels as a passphrase-encrypted
    Fernet token rather than a machine-bound DPAPI blob.
    """
    if include_password and not passphrase:
        raise ValueError("A passphrase is required to export a profile with the password.")

    target = path or (default_config_dir() / CLIENT_PROFILE_FILENAME)
    target.parent.mkdir(parents=True, exist_ok=True)

    data = profile.to_dict(passphrase=passphrase if include_password else None)
    if not include_password:
        data["database"]["password"] = ""  # client supplies it later
        data["password_required"] = True

    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def _restrict_permissions(path: Path) -> None:
    """Best-effort: secure the profile while allowing standard users to run CRM."""
    try:
        if os.name == "nt":
            import subprocess

            # The installer often runs elevated as a domain/local admin, while
            # employees later run CRM as standard users. If the profile remains
            # admin-only, the app cannot read it and falls back to localhost.
            grants = _windows_profile_acl_grants(os.environ.get("USERNAME", ""))
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", *grants],
                check=False, capture_output=True,
            )
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def _windows_profile_acl_grants(current_user: str = "") -> list[str]:
    """Return locale-independent icacls grants for the machine-wide profile."""
    grants = [
        "/grant:r", "*S-1-5-18:F",  # LocalSystem
        "/grant:r", "*S-1-5-32-544:F",  # Builtin Administrators
        "/grant:r", "*S-1-5-32-545:RX",  # Builtin Users can run the client.
    ]
    if current_user:
        grants += ["/grant:r", f"{current_user}:F"]
    return grants
