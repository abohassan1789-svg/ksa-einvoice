"""Idempotent SQL migration runner for the KSA e-Invoice database.

This project does not use Alembic. Its schema lives in numbered SQL files under
``app/database/migrations/`` (``001_...sql`` .. ``016_...sql`` plus ``015a_...``).
This runner applies every *forward* migration that has not been applied yet, in
filename order, tracking what has run in a ``schema_migrations`` table so it is
safe to run repeatedly.

Only forward, structural migrations are applied. The following are skipped:

* ``*_downgrade_*.sql`` — rollback scripts, never auto-applied.
* ``*_verify_*.sql``    — manual verification helpers.
* non-``.sql`` files    — e.g. one-off data-import PowerShell scripts.

Each file is executed through libpq's *simple query* protocol
(``PGconn.exec_``), which runs a multi-statement script as a single implicit
transaction — so a migration either fully applies or not at all — and, unlike
the extended protocol, accepts several statements in one call.

Connection settings come from the environment / ``.env`` via
:func:`app.config.settings.get_settings` (the installer writes ``.env`` before
calling this), or from explicit ``--host/--port/--db/--user/--password`` flags.

Usage::

    python -m app.database.migrate                 # use .env / profile
    python -m app.database.migrate --status        # list applied/pending, do nothing
    python -m app.database.migrate --host localhost --port 5432 \
        --db ksa_einvoice --user postgres --password ****
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# A forward migration starts with digits, an optional letter suffix (e.g. 015a),
# then an underscore, and is a .sql file that is neither a downgrade nor a verify.
_FORWARD_RE = re.compile(r"^\d+[a-z]?_.+\.sql$", re.IGNORECASE)


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return forward migration files sorted in apply order."""
    files: list[Path] = []
    for path in directory.glob("*.sql"):
        name = path.name.lower()
        if not _FORWARD_RE.match(name):
            continue
        if "downgrade" in name or "verify" in name:
            continue
        files.append(path)
    # Filename order == apply order because prefixes are zero-padded and the
    # only letter-suffixed file (015a) sorts after 015_ and before 016_.
    return sorted(files, key=lambda p: p.name.lower())


def _connect(args):
    import psycopg

    host = args.host
    if host is None:
        # Fall back to the app's own resolution (.env, then connection profile).
        from app.config.settings import get_settings

        s = get_settings()
        return psycopg.connect(
            host=s.db_host, port=s.db_port, dbname=s.db_name,
            user=s.db_user, password=s.db_password,
            connect_timeout=10, autocommit=True,
        )
    return psycopg.connect(
        host=host, port=args.port, dbname=args.db,
        user=args.user, password=_resolve_password(args),
        connect_timeout=10, autocommit=True,
    )


def _resolve_password(args) -> str:
    pw_file = getattr(args, "password_file", "") or ""
    if pw_file:
        try:
            return Path(pw_file).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError:
            return ""
    return getattr(args, "password", "") or ""


def _ensure_tracking_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    text PRIMARY KEY,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def _applied_set(conn) -> set[str]:
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def _exec_script(conn, sql_text: str) -> None:
    """Run a multi-statement SQL script atomically (simple query protocol)."""
    from psycopg.pq import ExecStatus

    result = conn.pgconn.exec_(sql_text.encode("utf-8"))
    ok = (ExecStatus.COMMAND_OK, ExecStatus.TUPLES_OK, ExecStatus.EMPTY_QUERY)
    if result.status not in ok:
        message = (result.error_message or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(message or f"migration failed (status={result.status})")


def run(args) -> int:
    migrations = discover_migrations()
    if not migrations:
        print("No migration files found in", MIGRATIONS_DIR)
        return 1

    conn = _connect(args)
    try:
        _ensure_tracking_table(conn)
        applied = _applied_set(conn)
        pending = [m for m in migrations if m.name not in applied]

        if args.status:
            print(f"Applied: {len(applied)}   Pending: {len(pending)}")
            for m in migrations:
                mark = "[x]" if m.name in applied else "[ ]"
                print(f"  {mark} {m.name}")
            return 0

        if not pending:
            print(f"Database is up to date ({len(applied)} migrations already applied).")
            return 0

        print(f"Applying {len(pending)} pending migration(s):")
        for m in pending:
            sys.stdout.write(f"  -> {m.name} ... ")
            sys.stdout.flush()
            sql_text = m.read_text(encoding="utf-8")
            try:
                _exec_script(conn, sql_text)
            except Exception as exc:  # noqa: BLE001
                print("FAILED")
                print(f"\nMigration {m.name} failed:\n{exc}", file=sys.stderr)
                return 2
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)"
                " ON CONFLICT (filename) DO NOTHING",
                [m.name],
            )
            print("ok")
        print(f"Done. {len(pending)} migration(s) applied.")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate", description="Apply pending SQL migrations to the database."
    )
    p.add_argument("--status", action="store_true",
                   help="Show applied/pending migrations and exit without changing anything.")
    p.add_argument("--host", default=None,
                   help="DB host (default: read from .env / connection profile).")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--db", default=None, help="Database name.")
    p.add_argument("--user", default="postgres")
    p.add_argument("--password", default="")
    p.add_argument("--password-file", dest="password_file", default="",
                   help="Read the password from this file instead of the command line.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
