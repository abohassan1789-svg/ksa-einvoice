-- CRM Attachments schema (وحدة المرفقات).
-- Stores uploaded files' real binary content inside the database in the
-- file_data column (PostgreSQL BYTEA) — never a local file path.
--
-- Safe to run repeatedly: every statement uses CREATE ... IF NOT EXISTS and
-- nothing is dropped, reset, or deleted. Mirrors app/models/attachment.py
-- (ATTACHMENTS_SCHEMA_SQL), which the app also runs automatically at startup.

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

-- Unique attachment code (ATT-1, ATT-2, ...) and lookup indexes.
CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_attachments_code ON crm_attachments (attachment_code);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_entity_type ON crm_attachments (entity_type);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_entity_id ON crm_attachments (entity_id);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_category ON crm_attachments (category);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_created_at ON crm_attachments (created_at);
CREATE INDEX IF NOT EXISTS idx_crm_attachments_file_hash ON crm_attachments (file_hash);
