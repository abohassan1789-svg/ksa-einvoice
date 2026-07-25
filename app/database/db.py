"""Lightweight shared PostgreSQL access helper (psycopg).

The running app talks to PostgreSQL through raw psycopg (see
``ReviewDataService``); the SQLAlchemy stubs in this package are not used by the
live app. This helper gives the security layer the same proven pattern: a single
long-lived autocommit connection that is reused for every query and reopened if
it goes stale. Keeping it here means all database access stays in the
database/repository layer.
"""

from __future__ import annotations

from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from app.config.database import build_database_url, runtime_connect_kwargs
from app.config.settings import get_settings


class Database:
    """A reusable autocommit psycopg connection with small query helpers."""

    def __init__(self, database_url: str | None = None) -> None:
        if database_url is None:
            database_url = build_database_url(get_settings())
        # ReviewDataService stores URLs in SQLAlchemy form; normalise to plain.
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://")
        self._connection: psycopg.Connection | None = None

    def connection(self) -> psycopg.Connection:
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(
                self.database_url,
                row_factory=dict_row,
                autocommit=True,
                **runtime_connect_kwargs(),
            )
        return self._connection

    def execute(self, query: Any, params: Iterable[Any] | None = None) -> None:
        try:
            self.connection().execute(query, list(params) if params is not None else None)
        except psycopg.OperationalError:
            self._connection = None
            raise

    def fetch_all(self, query: Any, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
        try:
            cur = self.connection().execute(query, list(params) if params is not None else None)
            return list(cur)
        except psycopg.OperationalError:
            self._connection = None
            raise

    def fetch_one(self, query: Any, params: Iterable[Any] | None = None) -> dict[str, Any] | None:
        try:
            cur = self.connection().execute(query, list(params) if params is not None else None)
            return cur.fetchone()
        except psycopg.OperationalError:
            self._connection = None
            raise

    def execute_script(self, sql: str) -> None:
        """Run a multi-statement DDL script (used for schema provisioning)."""
        try:
            self.connection().execute(sql)
        except psycopg.OperationalError:
            self._connection = None
            raise
