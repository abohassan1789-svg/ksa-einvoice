"""Transaction helper placeholders."""

from contextlib import contextmanager

from app.database.session import SessionLocal


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
