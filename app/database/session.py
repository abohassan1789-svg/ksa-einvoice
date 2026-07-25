"""Database session factory placeholder."""

from sqlalchemy.orm import sessionmaker

from app.database.connection import create_app_engine


SessionLocal = sessionmaker(bind=create_app_engine(), autoflush=False, autocommit=False)
