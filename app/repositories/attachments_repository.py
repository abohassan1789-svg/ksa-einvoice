"""Database access for the Attachments module (المرفقات).

All SQL for ``crm_attachments`` lives here; services and UI never touch the
database directly. Built on the shared psycopg ``Database`` helper, mirroring
``SecurityRepository``.

Performance contract: list/metadata queries NEVER select the heavy ``file_data``
binary column. The real file bytes are loaded only for a single row when it is
opened, previewed, or exported (``get_file_payload``).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database
from app.models.attachment import (
    ATTACHMENTS_SCHEMA_SQL,
    CODE_PREFIX,
    LIST_COLUMNS,
)

# Columns selected for list/detail views — metadata only, no ``file_data``.
_META_COLUMNS_SQL = ", ".join(LIST_COLUMNS)


class AttachmentsRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the attachments table + indexes if missing (safe to repeat)."""
        self.db.execute_script(ATTACHMENTS_SCHEMA_SQL)

    # --- code generation ----------------------------------------------------
    def next_code_number(self) -> int:
        """Reserve and return the next ``ATT-<n>`` suffix atomically.

        Uses the PostgreSQL sequence ``crm_attachment_code_seq`` (migration 008)
        via ``nextval``, which is atomic: two devices creating an attachment at
        the same instant receive different numbers instead of colliding on the
        UNIQUE ``attachment_code`` index. Falls back to the old MAX+1 scan only if
        the sequence has not been created yet (pre-migration database).
        """
        try:
            row = self.db.fetch_one("SELECT nextval('crm_attachment_code_seq') AS n")
            return int(row["n"]) if row else 0
        except Exception:
            return self.max_code_number() + 1

    def max_code_number(self) -> int:
        """Largest numeric suffix among ``ATT-<n>`` codes (0 if none)."""
        row = self.db.fetch_one(
            "SELECT COALESCE(MAX(CAST(substring(attachment_code from %s) AS integer)), 0) AS n "
            "FROM crm_attachments WHERE attachment_code ~ %s",
            [f"^{CODE_PREFIX}-([0-9]+)$", f"^{CODE_PREFIX}-[0-9]+$"],
        )
        return int(row["n"]) if row else 0

    def code_exists(self, code: str, exclude_id: int | None = None) -> bool:
        if exclude_id is None:
            row = self.db.fetch_one(
                "SELECT 1 FROM crm_attachments WHERE attachment_code = %s LIMIT 1", [code]
            )
        else:
            row = self.db.fetch_one(
                "SELECT 1 FROM crm_attachments WHERE attachment_code = %s AND id <> %s LIMIT 1",
                [code, exclude_id],
            )
        return row is not None

    # --- duplicate detection ------------------------------------------------
    def find_by_hash(self, file_hash: str) -> list[dict[str, Any]]:
        if not file_hash:
            return []
        return self.db.fetch_all(
            f"SELECT {_META_COLUMNS_SQL} FROM crm_attachments WHERE file_hash = %s "
            "ORDER BY id",
            [file_hash],
        )

    # --- queries (metadata only) -------------------------------------------
    def list_attachments(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return attachment metadata rows. Never loads ``file_data``."""
        filters = filters or {}
        sql = f"SELECT {_META_COLUMNS_SQL} FROM crm_attachments"
        clauses: list[str] = []
        params: list[Any] = []

        keyword = str(filters.get("keyword", "") or "").strip()
        if keyword:
            like = f"%{keyword}%"
            clauses.append(
                "(attachment_code ILIKE %s OR title ILIKE %s OR original_file_name ILIKE %s "
                "OR COALESCE(notes, '') ILIKE %s OR COALESCE(tags, '') ILIKE %s)"
            )
            params += [like, like, like, like, like]

        if filters.get("entity_type"):
            clauses.append("entity_type = %s")
            params.append(filters["entity_type"])

        if filters.get("entity_id") is not None:
            clauses.append("entity_id = %s")
            params.append(filters["entity_id"])

        if filters.get("category"):
            clauses.append("category = %s")
            params.append(filters["category"])

        extensions = filters.get("extensions")
        if extensions:
            placeholders = ", ".join(["%s"] * len(extensions))
            clauses.append(f"lower(file_extension) IN ({placeholders})")
            params += [e.lower() for e in extensions]

        if filters.get("favorite_only"):
            clauses.append("is_favorite = true")

        if filters.get("date_from"):
            clauses.append("created_at >= %s")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            # inclusive of the whole end day
            clauses.append("created_at < (%s::date + 1)")
            params.append(filters["date_to"])

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(int(filters.get("limit", 1000)))
        return self.db.fetch_all(sql, params)

    def get_metadata(self, attachment_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"SELECT {_META_COLUMNS_SQL} FROM crm_attachments WHERE id = %s",
            [attachment_id],
        )

    def get_file_payload(self, attachment_id: int) -> dict[str, Any] | None:
        """Load the binary content + the few fields needed to open/export it.

        This is the ONLY method that reads ``file_data``.
        """
        row = self.db.fetch_one(
            "SELECT id, attachment_code, original_file_name, file_extension, mime_type, "
            "file_size, file_data FROM crm_attachments WHERE id = %s",
            [attachment_id],
        )
        if row and row.get("file_data") is not None:
            row["file_data"] = bytes(row["file_data"])
        return row

    # --- statistics ---------------------------------------------------------
    def statistics(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(file_size), 0) AS total_size, "
            "COUNT(*) FILTER (WHERE lower(file_extension) = 'pdf') AS pdf_count, "
            "COUNT(*) FILTER (WHERE lower(file_extension) IN ('jpg','jpeg','png')) AS image_count, "
            "COUNT(*) FILTER (WHERE is_favorite) AS favorite_count "
            "FROM crm_attachments"
        )
        return dict(row) if row else {
            "total": 0, "total_size": 0, "pdf_count": 0,
            "image_count": 0, "favorite_count": 0,
        }

    # --- writes -------------------------------------------------------------
    def insert(self, data: dict[str, Any], file_bytes: bytes) -> dict[str, Any]:
        row = self.db.fetch_one(
            "INSERT INTO crm_attachments "
            "(attachment_code, entity_type, entity_id, title, category, original_file_name, "
            "file_extension, mime_type, file_size, file_hash, file_data, notes, tags, "
            "is_favorite, created_by, updated_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING id, attachment_code",
            [
                data.get("attachment_code"), data.get("entity_type"), data.get("entity_id"),
                data.get("title"), data.get("category"), data.get("original_file_name"),
                data.get("file_extension"), data.get("mime_type"), data.get("file_size"),
                data.get("file_hash"), file_bytes, data.get("notes"), data.get("tags"),
                data.get("is_favorite", False), data.get("created_by"), data.get("updated_by"),
            ],
        )
        return {"id": int(row["id"]), "attachment_code": row["attachment_code"]}

    def update(self, attachment_id: int, data: dict[str, Any],
               file_bytes: bytes | None = None) -> None:
        """Update metadata; replace ``file_data`` only when ``file_bytes`` given."""
        columns = [
            "attachment_code", "entity_type", "entity_id", "title", "category",
            "notes", "tags", "is_favorite", "updated_by",
        ]
        assignments = [f"{c} = %s" for c in columns]
        params: list[Any] = [data.get(c) for c in columns]

        if file_bytes is not None:
            # A new file replaces the stored binary and its derived metadata.
            for c in ("original_file_name", "file_extension", "mime_type",
                      "file_size", "file_hash"):
                assignments.append(f"{c} = %s")
                params.append(data.get(c))
            assignments.append("file_data = %s")
            params.append(file_bytes)

        assignments.append("updated_at = now()")
        params.append(attachment_id)
        self.db.execute(
            f"UPDATE crm_attachments SET {', '.join(assignments)} WHERE id = %s",
            params,
        )

    def delete(self, attachment_id: int) -> None:
        self.db.execute("DELETE FROM crm_attachments WHERE id = %s", [attachment_id])

    def set_favorite(self, attachment_id: int, favorite: bool) -> None:
        self.db.execute(
            "UPDATE crm_attachments SET is_favorite = %s, updated_at = now() WHERE id = %s",
            [favorite, attachment_id],
        )
