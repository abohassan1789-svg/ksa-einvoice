"""Command-line bridge used by the installer wizard.

The Inno Setup wizard collects the administrator's choices, then calls this
module with the bundled Python so that IP detection, port/DB validation, and
config generation all run through the *same* code the application uses — no
logic is duplicated in Pascal or PowerShell.

Every sub-command prints a single JSON object to stdout and uses the exit code
to signal success (0) or failure (non-zero), which is easy for the installer's
Pascal ``Exec`` + result parsing to consume.

Examples (as invoked by the installer)::

    python -m app.config.configure_cli detect-ips
    python -m app.config.configure_cli check-port --port 8000
    python -m app.config.configure_cli test-db --host 192.168.1.10 --port 8000 \
        --db crm --user postgres --password ****
    python -m app.config.configure_cli write --mode lan --address 192.168.1.10 \
        --port 8000 --db crm --user postgres --password **** --out "C:\\ProgramData\\CRM\\crm-connection.json"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.config import connection_config as cc
from app.services import network_service as net


def _emit(obj: dict, ok: bool) -> int:
    """Print JSON and return a shell exit code."""
    print(json.dumps(obj, ensure_ascii=False))
    return 0 if ok else 1


def _resolve_password(args) -> str:
    """Resolve the DB password without ever putting it on the command line.

    Priority: ``--password-file`` (deleted-by-caller temp file) then the
    ``CRM_DB_PASSWORD`` environment variable, then a literal ``--password`` (kept
    only for local/dev use). This keeps secrets out of the process argument list.
    """
    pw_file = getattr(args, "password_file", "") or ""
    if pw_file:
        try:
            return Path(pw_file).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError:
            return ""
    env_pw = os.environ.get("CRM_DB_PASSWORD")
    if env_pw is not None:
        return env_pw
    return getattr(args, "password", "") or ""


def _cmd_detect_ips(_args) -> int:
    ips = net.detect_ipv4_addresses()
    return _emit({"ok": True, "addresses": ips}, ok=True)


def _cmd_check_port(args) -> int:
    result = net.is_port_available(args.port, address=args.address)
    return _emit({"ok": result.ok, "message": result.message, "detail": result.detail}, result.ok)


def _cmd_test_db(args) -> int:
    result = net.test_database(
        host=args.host, port=args.port, db_name=args.db,
        user=args.user, password=_resolve_password(args), sslmode=args.sslmode,
    )
    return _emit({"ok": result.ok, "message": result.message, "detail": result.detail}, result.ok)


def _scrub(text: str, password: str) -> str:
    """Never let a password leak into installer stdout/logs."""
    return text.replace(password, "******") if password else text


def _cmd_create_db(args) -> int:
    """Create the application database only if it does not already exist.

    Connects to the maintenance database (default ``postgres``) and issues a
    single ``CREATE DATABASE`` only when the target is absent. It NEVER drops,
    truncates, or overwrites an existing database — an existing target is
    reported as ``existed`` and left completely untouched.
    """
    import psycopg
    from psycopg import sql

    password = _resolve_password(args)
    try:
        with psycopg.connect(
            host=args.host, port=args.port, dbname=args.maintenance_db,
            user=args.user, password=password, sslmode=args.sslmode,
            connect_timeout=10, autocommit=True,
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", [args.db]
            ).fetchone()
            if exists:
                return _emit(
                    {"ok": True, "existed": True, "created": False,
                     "message": f"Database {args.db!r} already exists; reusing it untouched."},
                    ok=True,
                )
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.db)))
            return _emit(
                {"ok": True, "existed": False, "created": True,
                 "message": f"Database {args.db!r} created."},
                ok=True,
            )
    except Exception as exc:  # noqa: BLE001 - report a scrubbed, actionable error
        return _emit(
            {"ok": False, "message": _scrub(f"{type(exc).__name__}: {exc}", password)},
            ok=False,
        )


def _cmd_provision(args) -> int:
    """Provision the application schema and seed essential system data.

    Runs the SAME idempotent bootstrap the application performs on first launch:
    the security schema, the default Admin account, the permission registry sync,
    and the attachments schema — all ``CREATE ... IF NOT EXISTS`` / upsert style,
    so repeating it never duplicates rows or overwrites data. No demo/sample data
    is ever written. Uses an explicit connection built from the arguments so it
    does not depend on any profile write order.
    """
    from urllib.parse import quote_plus

    password = _resolve_password(args)
    user = quote_plus(args.user)
    pw = quote_plus(password or "")
    auth = f"{user}:{pw}" if pw else user
    url = (
        f"postgresql://{auth}@{args.host}:{args.port}/{args.db}?sslmode={args.sslmode}"
    )
    try:
        from app.database.db import Database
        from app.repositories.attachments_repository import AttachmentsRepository
        from app.repositories.security_repository import SecurityRepository
        from app.services.attachments_service import AttachmentsService
        from app.services.auth_service import AuthService
        from app.services.permissions_sync_service import PermissionsSyncService

        db = Database(url)
        repo = SecurityRepository(db)
        repo.ensure_schema()
        AuthService(repo).seed_admin()
        sync_result = PermissionsSyncService(repo).sync()
        AttachmentsService(AttachmentsRepository(db)).ensure_schema()
        return _emit(
            {"ok": True,
             "message": "Schema provisioned and default admin/roles/permissions seeded.",
             "permissions": sync_result},
            ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            from app.config.database import describe_db_error

            message = describe_db_error(
                exc, host=args.host, port=args.port, db_name=args.db, password=password,
                fallback_prefix="Database initialization failed",
            )
        except Exception:  # pragma: no cover - fall back to a scrubbed raw error
            message = _scrub(f"{type(exc).__name__}: {exc}", password)
        return _emit({"ok": False, "message": message}, ok=False)


def _cmd_write(args) -> int:
    profile = cc.ConnectionProfile(
        connection_mode=args.mode,
        server_address=args.address,
        server_port=args.port,
        db_name=args.db,
        db_user=args.user,
        db_password=_resolve_password(args),
        sslmode=args.sslmode,
        cloud_hostname=args.cloud_hostname or "",
        https=args.https,
        reverse_proxy=args.reverse_proxy,
        ssl_cert_path=args.ssl_cert or "",
    )
    errors = profile.validate()
    if errors:
        return _emit({"ok": False, "message": "; ".join(errors)}, ok=False)

    out = Path(args.out) if args.out else None
    written = cc.save(profile, path=out)

    exported = None
    if args.export_client:
        exported = str(cc.export_client_profile(
            profile,
            path=Path(args.export_client),
            include_password=False,
        ))

    return _emit(
        {"ok": True, "message": f"Configuration written to {written}",
         "path": str(written), "client_profile": exported},
        ok=True,
    )


def _cmd_set_backup_dir(args) -> int:
    """Record the installer-chosen backup folder in ``backup_settings.json``.

    ``BackupService`` already reads this file for ``backup_on_startup`` /
    ``backup_on_shutdown`` / ``max_backups``; ``backup_dir`` joins them so the
    folder the administrator picks at install time is the folder the automatic
    startup/shutdown backups are written to.

    Merged, never overwritten: a re-install or an upgrade must not silently reset
    a customer's retention count or turn their auto-backups back on. Doing this
    in Python rather than in the wizard's Pascal also means the JSON escaping and
    the UTF-8 encoding of a non-ASCII path are handled by the stdlib instead of
    by hand.
    """
    folder = Path(args.path)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _emit(
            {"ok": False, "message": f"Could not create the backup folder {folder}: {exc}"},
            ok=False,
        )
    if not os.access(folder, os.W_OK):
        return _emit(
            {"ok": False, "message": f"The backup folder {folder} is not writable."},
            ok=False,
        )

    target = Path(args.out)
    data: dict = {}
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            data = existing
    except (OSError, json.JSONDecodeError):
        pass  # first install, or an unreadable file we replace with valid defaults

    data["backup_dir"] = str(folder)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return _emit(
            {"ok": False, "message": f"Could not write {target}: {exc}"}, ok=False
        )
    return _emit(
        {"ok": True, "message": f"Backup folder set to {folder}",
         "backup_dir": str(folder), "path": str(target)},
        ok=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="configure_cli", description="CRM server configuration bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bdir = sub.add_parser("set-backup-dir", help="Record the chosen backup folder (merged)")
    p_bdir.add_argument("--path", required=True, help="Folder the .sql backups are written to")
    p_bdir.add_argument("--out", required=True, help="Path of backup_settings.json")

    sub.add_parser("detect-ips", help="List local IPv4 addresses (JSON)")

    p_port = sub.add_parser("check-port", help="Check whether a port can be bound")
    p_port.add_argument("--port", type=int, required=True)
    p_port.add_argument("--address", default="0.0.0.0")

    p_db = sub.add_parser("test-db", help="Test a PostgreSQL connection")
    p_db.add_argument("--host", required=True)
    p_db.add_argument("--port", type=int, required=True)
    p_db.add_argument("--db", default="crm")
    p_db.add_argument("--user", default="postgres")
    p_db.add_argument("--password", default="")
    p_db.add_argument("--password-file", dest="password_file", default="",
                      help="Read the password from this file instead of the command line")
    p_db.add_argument("--sslmode", default="prefer")

    p_cdb = sub.add_parser("create-db", help="Create the app database only if it is missing")
    p_cdb.add_argument("--host", required=True)
    p_cdb.add_argument("--port", type=int, required=True)
    p_cdb.add_argument("--db", required=True, help="Application database to create if absent")
    p_cdb.add_argument("--user", default="postgres")
    p_cdb.add_argument("--password", default="")
    p_cdb.add_argument("--password-file", dest="password_file", default="")
    p_cdb.add_argument("--sslmode", default="prefer")
    p_cdb.add_argument("--maintenance-db", dest="maintenance_db", default="postgres",
                       help="Maintenance database to connect to for CREATE DATABASE")

    p_prov = sub.add_parser("provision", help="Provision schema + seed admin/roles/permissions")
    p_prov.add_argument("--host", required=True)
    p_prov.add_argument("--port", type=int, required=True)
    p_prov.add_argument("--db", required=True)
    p_prov.add_argument("--user", default="postgres")
    p_prov.add_argument("--password", default="")
    p_prov.add_argument("--password-file", dest="password_file", default="")
    p_prov.add_argument("--sslmode", default="prefer")

    p_w = sub.add_parser("write", help="Generate the connection profile file")
    p_w.add_argument("--mode", choices=[cc.MODE_LAN, cc.MODE_CLOUD], required=True)
    p_w.add_argument("--address", required=True)
    p_w.add_argument("--port", type=int, default=cc.DEFAULT_PORT)
    p_w.add_argument("--db", default="crm")
    p_w.add_argument("--user", default="postgres")
    p_w.add_argument("--password", default="")
    p_w.add_argument("--password-file", dest="password_file", default="",
                     help="Read the password from this file instead of the command line")
    p_w.add_argument("--sslmode", default="prefer")
    p_w.add_argument("--cloud-hostname", dest="cloud_hostname", default="")
    p_w.add_argument("--https", action="store_true")
    p_w.add_argument("--reverse-proxy", dest="reverse_proxy", action="store_true")
    p_w.add_argument("--ssl-cert", dest="ssl_cert", default="")
    p_w.add_argument("--out", default="")
    p_w.add_argument("--export-client", dest="export_client", default="")
    return parser


_COMMANDS = {
    "detect-ips": _cmd_detect_ips,
    "check-port": _cmd_check_port,
    "test-db": _cmd_test_db,
    "create-db": _cmd_create_db,
    "provision": _cmd_provision,
    "write": _cmd_write,
    "set-backup-dir": _cmd_set_backup_dir,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except Exception as exc:  # never leak a traceback to the installer
        return _emit({"ok": False, "message": f"{type(exc).__name__}: {exc}"}, ok=False)


if __name__ == "__main__":
    sys.exit(main())
