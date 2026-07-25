"""Unit tests for the Attachments service (no real database needed).

A small in-memory fake repository stands in for ``AttachmentsRepository`` so the
business rules, code generation, validation, hashing and export can all be
verified without PostgreSQL.
"""

import os
import tempfile

import pytest

from app.models.attachment import DEFAULT_MAX_FILE_SIZE
from app.services.attachments_service import (
    AttachmentsService,
    AttachmentValidationError,
)


class FakeRepository:
    """In-memory stand-in for AttachmentsRepository."""

    def __init__(self):
        self.rows = []
        self._next_id = 1
        self.ensured = False

    def ensure_schema(self):
        self.ensured = True

    def max_code_number(self):
        numbers = []
        for row in self.rows:
            code = row.get("attachment_code", "")
            if code.startswith("ATT-") and code[4:].isdigit():
                numbers.append(int(code[4:]))
        return max(numbers) if numbers else 0

    def next_code_number(self):
        # In-memory stand-in for the PostgreSQL sequence: deterministic MAX+1,
        # which mirrors the repository's pre-migration fallback path.
        return self.max_code_number() + 1

    def code_exists(self, code, exclude_id=None):
        return any(
            r["attachment_code"] == code and r["id"] != exclude_id for r in self.rows
        )

    def find_by_hash(self, file_hash):
        return [r for r in self.rows if r.get("file_hash") == file_hash]

    def insert(self, data, file_bytes):
        row = dict(data)
        row["id"] = self._next_id
        row["file_data"] = file_bytes
        self._next_id += 1
        self.rows.append(row)
        return {"id": row["id"], "attachment_code": row["attachment_code"]}

    def update(self, attachment_id, data, file_bytes=None):
        for row in self.rows:
            if row["id"] == attachment_id:
                row.update(data)
                if file_bytes is not None:
                    row["file_data"] = file_bytes
                return

    def delete(self, attachment_id):
        self.rows = [r for r in self.rows if r["id"] != attachment_id]

    def set_favorite(self, attachment_id, favorite):
        for row in self.rows:
            if row["id"] == attachment_id:
                row["is_favorite"] = favorite

    def list_attachments(self, filters=None):
        return list(self.rows)

    def get_metadata(self, attachment_id):
        return next((r for r in self.rows if r["id"] == attachment_id), None)

    def get_file_payload(self, attachment_id):
        return next((r for r in self.rows if r["id"] == attachment_id), None)

    def statistics(self):
        return {
            "total": len(self.rows),
            "total_size": sum(r.get("file_size", 0) for r in self.rows),
            "pdf_count": sum(1 for r in self.rows if r.get("file_extension") == "pdf"),
            "image_count": sum(1 for r in self.rows if r.get("file_extension") in ("jpg", "jpeg", "png")),
            "favorite_count": sum(1 for r in self.rows if r.get("is_favorite")),
        }


@pytest.fixture
def service():
    return AttachmentsService(repository=FakeRepository())


def _make_file(suffix=".txt", content=b"hello world"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    return path


# --- format_file_size ------------------------------------------------------
def test_format_file_size_units():
    assert AttachmentsService.format_file_size(0) == "0 B"
    assert AttachmentsService.format_file_size(512) == "512 B"
    assert AttachmentsService.format_file_size(2048) == "2.00 KB"
    assert AttachmentsService.format_file_size(5 * 1024 * 1024) == "5.00 MB"


# --- code generation -------------------------------------------------------
def test_code_auto_generates_att1_then_att2(service):
    assert service.get_next_attachment_code() == "ATT-1"
    service.create_attachment(
        {"title": "first", "file_extension": "txt", "file_size": 5},
        b"hello",
    )
    assert service.get_next_attachment_code() == "ATT-2"
    service.create_attachment(
        {"title": "second", "file_extension": "txt", "file_size": 5},
        b"world",
    )
    assert service.get_next_attachment_code() == "ATT-3"


def test_duplicate_code_is_rejected(service):
    service.create_attachment(
        {"attachment_code": "ATT-1", "title": "a", "file_extension": "txt", "file_size": 1},
        b"x",
    )
    with pytest.raises(AttachmentValidationError):
        service.create_attachment(
            {"attachment_code": "ATT-1", "title": "b", "file_extension": "txt", "file_size": 1},
            b"y",
        )


# --- file metadata + hash --------------------------------------------------
def test_read_file_metadata_and_hash(service):
    path = _make_file(".txt", b"hello world")
    try:
        meta = service.read_file_metadata(path)
    finally:
        os.remove(path)
    assert meta["file_extension"] == "txt"
    assert meta["file_size"] == 11
    assert meta["mime_type"] == "text/plain"
    assert meta["file_data"] == b"hello world"
    # deterministic SHA-256 of "hello world"
    assert meta["file_hash"] == (
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )


def test_binary_is_stored_in_database_layer(service):
    result = service.create_attachment(
        {"title": "doc", "file_extension": "txt", "file_size": 5},
        b"bytes",
    )
    stored = service.get_file_payload(result["id"])
    assert stored["file_data"] == b"bytes"  # real content saved, not a path


def test_duplicate_file_detected_by_hash(service):
    file_hash = service.calculate_file_hash(b"same")
    service.create_attachment(
        {"title": "a", "file_extension": "txt", "file_size": 4, "file_hash": file_hash},
        b"same",
    )
    duplicates = service.detect_duplicate_file(file_hash)
    assert len(duplicates) == 1


# --- validation ------------------------------------------------------------
def test_large_file_is_rejected(service):
    with pytest.raises(AttachmentValidationError):
        service.validate_file("pdf", DEFAULT_MAX_FILE_SIZE + 1)


def test_unsupported_extension_is_rejected(service):
    with pytest.raises(AttachmentValidationError):
        service.validate_file("exe", 10)


def test_title_required(service):
    with pytest.raises(AttachmentValidationError):
        service.create_attachment(
            {"title": "", "file_extension": "txt", "file_size": 1}, b"x"
        )


def test_new_attachment_requires_file_bytes(service):
    with pytest.raises(AttachmentValidationError):
        service.create_attachment(
            {"title": "no file", "file_extension": "txt", "file_size": 0}, b""
        )


# --- export ----------------------------------------------------------------
def test_export_writes_same_bytes(service):
    result = service.create_attachment(
        {"title": "doc", "file_extension": "txt", "file_size": 7},
        b"content",
    )
    fd, target = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        service.export_attachment(result["id"], target)
        with open(target, "rb") as handle:
            assert handle.read() == b"content"
    finally:
        os.remove(target)


# --- statistics ------------------------------------------------------------
def test_statistics_human_size(service):
    service.create_attachment(
        {"title": "pdf", "file_extension": "pdf", "file_size": 2048}, b"x" * 10
    )
    stats = service.get_attachment_statistics()
    assert stats["total"] == 1
    assert stats["pdf_count"] == 1
    assert "KB" in stats["total_size_human"] or "B" in stats["total_size_human"]


# --- entity defaults -------------------------------------------------------
def test_entity_type_defaults_to_other(service):
    result = service.create_attachment(
        {"title": "x", "file_extension": "txt", "file_size": 1}, b"x"
    )
    stored = service.get_attachment_by_id(result["id"])
    assert stored["entity_type"] == "Other"
    assert stored["entity_id"] is None
