"""Schema definition and domain constants for the Attachments module (المرفقات).

This module holds the *structure* (DDL) and reusable domain constants only — no
SQL execution and no business logic. The repository layer runs
``ATTACHMENTS_SCHEMA_SQL`` (every statement uses ``CREATE ... IF NOT EXISTS`` so
it is safe to run repeatedly and never drops, resets, or deletes anything). The
same SQL is mirrored in
``app/database/migrations/005_create_attachments_schema.sql``.

Storage note: the real file bytes are stored inside the database in the
``file_data`` column (PostgreSQL ``bytea``). The original local file path is
never persisted — only the binary content and its metadata.
"""

from __future__ import annotations

# Table that stores every uploaded file's binary content + metadata.
TABLE_NAME = "crm_attachments"

# Auto-generated code format: ATT-1, ATT-2, ATT-3 ...
CODE_PREFIX = "ATT"

# Configurable maximum upload size (bytes). Default 20 MB.
DEFAULT_MAX_FILE_SIZE = 20 * 1024 * 1024

# Allowed upload extensions (lower-case, without the dot).
ALLOWED_EXTENSIONS: tuple[str, ...] = (
    "pdf",
    "jpg",
    "jpeg",
    "png",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "txt",
)

IMAGE_EXTENSIONS: frozenset[str] = frozenset({"jpg", "jpeg", "png"})
DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({"doc", "docx", "txt"})
EXCEL_EXTENSIONS: frozenset[str] = frozenset({"xls", "xlsx"})
PDF_EXTENSIONS: frozenset[str] = frozenset({"pdf"})

# Fallback MIME types when the platform mimetypes database is incomplete.
MIME_BY_EXTENSION: dict[str, str] = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
}

# (code, arabic label) — categories shown in the UI. ``code`` is stored.
ATTACHMENT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Identity", "هوية / إثبات شخصية"),
    ("Contract", "عقد"),
    ("Invoice", "فاتورة"),
    ("Receipt", "إيصال"),
    ("Image", "صورة"),
    ("Document", "مستند"),
    ("Other", "أخرى"),
)

# (code, arabic label) — generic CRM record types this attachment can link to.
# The module is intentionally generic: any screen can reuse it by passing its own
# (entity_type, entity_id) pair. Not hard-coded to customers.
ENTITY_TYPES: tuple[tuple[str, str], ...] = (
    ("Customer", "عميل"),
    ("DailyFollowUp", "متابعة يومية"),
    ("Contract", "عقد"),
    ("Shipment", "شحنة"),
    ("Case", "حالة"),
    ("Other", "أخرى"),
)

DEFAULT_ENTITY_TYPE = "Other"

# Metadata-only columns loaded for list/table queries. The heavy ``file_data``
# binary column is intentionally excluded here — it is fetched only when a single
# attachment is opened, previewed, or exported (see the repository).
LIST_COLUMNS: tuple[str, ...] = (
    "id",
    "attachment_code",
    "entity_type",
    "entity_id",
    "title",
    "category",
    "original_file_name",
    "file_extension",
    "mime_type",
    "file_size",
    "file_hash",
    "tags",
    "notes",
    "is_favorite",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
)


ATTACHMENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crm_attachments (
    id                  serial PRIMARY KEY,
    attachment_code     varchar(40) NOT NULL,
    entity_type         varchar(60) NOT NULL DEFAULT 'Other',
    entity_id           integer,
    title               varchar(200) NOT NULL,
    category            varchar(60),
    original_file_name  varchar(255) NOT NULL,
    file_extension      varchar(20),
    mime_type           varchar(120),
    file_size           bigint NOT NULL DEFAULT 0,
    file_hash           varchar(64),
    file_data           bytea NOT NULL,
    notes               text,
    tags                text,
    is_favorite         boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    created_by          integer,
    updated_by          integer
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_attachments_code ON crm_attachments (attachment_code);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_entity_type ON crm_attachments (entity_type);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_entity_id ON crm_attachments (entity_id);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_category ON crm_attachments (category);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_created_at ON crm_attachments (created_at);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_file_hash ON crm_attachments (file_hash);
"""
