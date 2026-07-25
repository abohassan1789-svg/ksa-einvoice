"""Application settings loaded from environment variables."""

from dataclasses import dataclass
import os

try:
    from dotenv import load_dotenv
except ImportError:  # Allows basic skeleton inspection before dependencies are installed.
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    db_name: str = "crm"
    db_user: str = "postgres"
    db_password: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    app_env: str = "development"
    log_level: str = "INFO"


def get_settings() -> Settings:
    """Resolve settings, layering sources highest-priority first:

    1. The CRM connection profile (written by the installer / Settings screen),
       when present, provides the database host/port/name/user/password.
    2. Environment variables (``.env``) fill anything the profile does not set.
    3. Hard-coded defaults are the final fallback.

    This keeps the existing ``.env`` workflow working while letting an installed
    machine drive its connection from the encrypted profile without edits here.
    """
    if load_dotenv is not None:
        load_dotenv()

    # Environment / defaults baseline.
    db_name = os.getenv("DB_NAME", "crm")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "5432"))

    # Overlay the connection profile when it exists (installed deployments).
    profile = _load_profile()
    if profile is not None:
        db_name = profile.db_name or db_name
        db_user = profile.db_user or db_user
        db_password = profile.db_password or db_password
        db_host = profile.effective_host or db_host
        db_port = int(profile.server_port or db_port)

    return Settings(
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def _load_profile():
    """Load the connection profile if one is present; never raise on failure."""
    try:
        from app.config import connection_config

        return connection_config.load()
    except Exception:
        return None
