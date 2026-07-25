"""SQLAlchemy engine factory placeholder."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config.database import build_database_url
from app.config.settings import get_settings


def create_app_engine() -> Engine:
    settings = get_settings()
    return create_engine(build_database_url(settings), pool_pre_ping=True)
