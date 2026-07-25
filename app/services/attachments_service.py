"""Business logic for the Attachments module (المرفقات).

Reads/writes go through :class:`AttachmentsRepository` (database layer); this
service owns validation, auto code generation, file metadata extraction, hash
based duplicate detection, and export. Mirrors the project's existing
service/repository split (e.g. ``PermissionService`` + ``SecurityRepository``).

Storage: the real file bytes are saved inside the database (BYTEA). No local
file path is ever stored, and opening/exporting always reads ``file_data`` from
the database — never the original source path.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from typing import Any

from app.models.attachment import (
    ALLOWED_EXTENSIONS,
    CODE_PREFIX,
    DEFAULT_ENTITY_TYPE,
    DEFAULT_MAX_FILE_SIZE,
    MIME_BY_EXTENSION,
)
from app.repositories.attachments_repository import AttachmentsRepository


class AttachmentValidationError(ValueError):
    """Raised for friendly, user-facing validation failures (Arabic message)."""


class AttachmentsService:
    def __init__(
        self,
        repository: AttachmentsRepository | None = None,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        self.repository = repository or AttachmentsRepository()
        self.max_file_size = max_file_size

    # --- schema -------------------------------------------------------------
    def ensure_schema(self) -> None:
        self.repository.ensure_schema()

    # --- code generation ----------------------------------------------------
    def get_next_attachment_code(self) -> str:
        """Return the next free code in ``ATT-1``, ``ATT-2`` ... format.

        Allocated atomically from a PostgreSQL sequence so simultaneous uploads
        on different devices never collide on the same ``attachment_code``.
        """
        return f"{CODE_PREFIX}-{self.repository.next_code_number()}"

    # --- file helpers -------------------------------------------------------
    @staticmethod
    def calculate_file_hash(file_bytes: bytes) -> str:
        """SHA-256 hex digest of the file content (used for duplicate detection)."""
        return hashlib.sha256(file_bytes or b"").hexdigest()

    @staticmethod
    def detect_mime_type(file_name: str, extension: str) -> str:
        guessed, _ = mimetypes.guess_type(file_name)
        if guessed:
            return guessed
        return MIME_BY_EXTENSION.get(extension.lower(), "application/octet-stream")

    def read_file_metadata(self, file_path: str) -> dict[str, Any]:
        """Read a chosen file and return its bytes + derived metadata.

        Does NOT save anything — this only inspects the file the user picked so
        the form can be populated. Saving happens later via ``create_attachment``.
        """
        if not file_path or not os.path.isfile(file_path):
            raise AttachmentValidationError("الملف المختار غير موجود.")
        with open(file_path, "rb") as handle:
            file_bytes = handle.read()

        original_file_name = os.path.basename(file_path)
        extension = os.path.splitext(original_file_name)[1].lstrip(".").lower()
        return {
            "original_file_name": original_file_name,
            "file_extension": extension,
            "mime_type": self.detect_mime_type(original_file_name, extension),
            "file_size": len(file_bytes),
            "file_hash": self.calculate_file_hash(file_bytes),
            "file_data": file_bytes,
        }

    def detect_duplicate_file(self, file_hash: str) -> list[dict[str, Any]]:
        """Return existing attachments whose content hash matches (may be empty)."""
        return self.repository.find_by_hash(file_hash)

    @staticmethod
    def format_file_size(size_bytes: Any) -> str:
        """Human-readable size: bytes / KB / MB / GB."""
        try:
            size = float(size_bytes or 0)
        except (TypeError, ValueError):
            return "0 B"
        if size < 1024:
            return f"{int(size)} B"
        for unit in ("KB", "MB", "GB"):
            size /= 1024.0
            if size < 1024 or unit == "GB":
                return f"{size:.2f} {unit}"
        return f"{size:.2f} GB"

    # --- validation ---------------------------------------------------------
    def validate_file(self, extension: str, file_size: int) -> None:
        ext = (extension or "").lower()
        if ext not in ALLOWED_EXTENSIONS:
            allowed = "، ".join(ALLOWED_EXTENSIONS)
            raise AttachmentValidationError(
                f"نوع الملف غير مسموح به: «{ext or 'غير معروف'}». "
                f"الأنواع المسموح بها: {allowed}."
            )
        if file_size is not None and file_size > self.max_file_size:
            limit_mb = self.max_file_size / (1024 * 1024)
            raise AttachmentValidationError(
                f"حجم الملف يتجاوز الحد المسموح به ({limit_mb:.0f} ميجابايت)."
            )

    def validate_attachment_data(
        self,
        data: dict[str, Any],
        is_new: bool,
        file_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """Validate + normalise a payload before insert/update.

        Returns a cleaned dict. Raises :class:`AttachmentValidationError` with a
        friendly Arabic message on the first problem found.
        """
        clean = dict(data)

        title = str(clean.get("title", "") or "").strip()
        if not title:
            raise AttachmentValidationError("العنوان مطلوب.")
        clean["title"] = title

        # entity_type defaults to "Other" when not chosen; entity_id is optional.
        clean["entity_type"] = str(clean.get("entity_type") or DEFAULT_ENTITY_TYPE).strip()
        clean["entity_id"] = self._coerce_optional_int(clean.get("entity_id"), "كود السجل المرتبط")

        # attachment_code: auto-generate when blank, else must be unique.
        code = str(clean.get("attachment_code", "") or "").strip()
        if not code:
            code = self.get_next_attachment_code()
        clean["attachment_code"] = code
        exclude_id = None if is_new else clean.get("id")
        if self.repository.code_exists(code, exclude_id=exclude_id):
            raise AttachmentValidationError(f"كود المرفق مكرر وغير مسموح به: «{code}».")

        # A new attachment must carry file content; an update may keep the old file.
        if is_new and not file_bytes:
            raise AttachmentValidationError("يجب اختيار ملف قبل الحفظ.")

        if file_bytes is not None:
            self.validate_file(clean.get("file_extension", ""), clean.get("file_size", len(file_bytes)))

        for key in ("category", "notes", "tags"):
            value = clean.get(key)
            clean[key] = str(value).strip() if value not in (None, "") else None
        clean["is_favorite"] = bool(clean.get("is_favorite", False))
        return clean

    @staticmethod
    def _coerce_optional_int(value: Any, label: str) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise AttachmentValidationError(f"قيمة رقمية غير صحيحة في {label}.")

    # --- CRUD ---------------------------------------------------------------
    def create_attachment(self, data: dict[str, Any], file_bytes: bytes) -> dict[str, Any]:
        clean = self.validate_attachment_data(data, is_new=True, file_bytes=file_bytes)
        return self.repository.insert(clean, file_bytes)

    def update_attachment(
        self,
        attachment_id: int,
        data: dict[str, Any],
        optional_file_bytes: bytes | None = None,
    ) -> None:
        payload = dict(data)
        payload["id"] = attachment_id
        clean = self.validate_attachment_data(
            payload, is_new=False, file_bytes=optional_file_bytes
        )
        self.repository.update(attachment_id, clean, optional_file_bytes)

    def delete_attachment(self, attachment_id: int) -> None:
        self.repository.delete(attachment_id)

    def set_favorite(self, attachment_id: int, favorite: bool) -> None:
        self.repository.set_favorite(attachment_id, favorite)

    # --- reads --------------------------------------------------------------
    def list_attachments(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.repository.list_attachments(filters)

    def get_attachment_by_id(self, attachment_id: int) -> dict[str, Any] | None:
        """Metadata only (no binary) — for loading the form / table row."""
        return self.repository.get_metadata(attachment_id)

    def get_file_payload(self, attachment_id: int) -> dict[str, Any] | None:
        """Binary + minimal fields, loaded only when opening/previewing/exporting."""
        return self.repository.get_file_payload(attachment_id)

    def export_attachment(self, attachment_id: int, target_path: str) -> str:
        """Write the stored binary to ``target_path``. Reads from DB, not disk."""
        payload = self.repository.get_file_payload(attachment_id)
        if not payload or payload.get("file_data") is None:
            raise AttachmentValidationError("تعذر العثور على محتوى الملف في قاعدة البيانات.")
        with open(target_path, "wb") as handle:
            handle.write(payload["file_data"])
        return target_path

    def get_attachment_statistics(self) -> dict[str, Any]:
        stats = self.repository.statistics()
        stats["total_size_human"] = self.format_file_size(stats.get("total_size", 0))
        return stats
