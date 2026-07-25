"""Schema definitions for the Users / Roles / Permissions system.

This module holds the *structure* (DDL) only — no SQL execution and no business
logic. The repository layer runs ``SECURITY_SCHEMA_SQL`` (all statements use
``CREATE TABLE IF NOT EXISTS``, so it is safe to run repeatedly and never drops
or resets anything). The same SQL is mirrored in
``app/database/migrations/004_create_security_schema.sql``.
"""

from __future__ import annotations

# Override semantics for user_permissions / role_permissions.
#   role_permissions.allowed : true => granted by the role (no row => not granted)
#   user_permissions.allowed : true => force allow, false => force deny
#                              (no row => inherit from the role)
SECURITY_SCHEMA_SQL = """
-- Backward compatibility: if a legacy table from an earlier design already
-- occupies one of our names but has a different primary key (no "id" column),
-- rename it to <name>_legacy so its data is preserved and the new, correctly
-- shaped table can be created. Idempotent and non-destructive.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'app_users')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'app_users' AND column_name = 'id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'app_users_legacy')
    THEN
        ALTER TABLE app_users RENAME TO app_users_legacy;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'roles')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'roles' AND column_name = 'id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'roles_legacy')
    THEN
        ALTER TABLE roles RENAME TO roles_legacy;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'permissions')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'permissions' AND column_name = 'id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'permissions_legacy')
    THEN
        ALTER TABLE permissions RENAME TO permissions_legacy;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS roles (
    id            serial PRIMARY KEY,
    role_code     varchar(50) UNIQUE NOT NULL,
    role_name_ar  varchar(120),
    role_name_en  varchar(120),
    description   text,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_users (
    id             serial PRIMARY KEY,
    username       varchar(60) UNIQUE NOT NULL,
    password_hash  varchar(255) NOT NULL,
    full_name      varchar(120),
    email          varchar(120),
    phone          varchar(40),
    role_id        integer REFERENCES roles(id) ON DELETE SET NULL,
    language       varchar(10) NOT NULL DEFAULT 'ar',
    is_active      boolean NOT NULL DEFAULT true,
    is_admin       boolean NOT NULL DEFAULT false,
    full_access    boolean NOT NULL DEFAULT false,
    notes          text,
    last_login_at  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permissions (
    id              serial PRIMARY KEY,
    permission_code varchar(150) UNIQUE NOT NULL,
    permission_type varchar(20) NOT NULL,
    module_code     varchar(50),
    module_name_ar  varchar(120),
    module_name_en  varchar(120),
    category_ar     varchar(120),
    category_en     varchar(120),
    target_code     varchar(80),
    target_name_ar  varchar(150),
    target_name_en  varchar(150),
    action_code     varchar(40),
    action_name_ar  varchar(80),
    action_name_en  varchar(80),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS role_permissions (
    id            serial PRIMARY KEY,
    role_id       integer NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id integer NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    allowed       boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS user_permissions (
    id            serial PRIMARY KEY,
    user_id       integer NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    permission_id integer NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    allowed       boolean NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, permission_id)
);

CREATE TABLE IF NOT EXISTS user_login_logs (
    id          serial PRIMARY KEY,
    user_id     integer REFERENCES app_users(id) ON DELETE SET NULL,
    username    varchar(60),
    login_time  timestamptz NOT NULL DEFAULT now(),
    logout_time timestamptz,
    status      varchar(20),
    ip_address  varchar(60),
    device_name varchar(120),
    notes       text
);

CREATE INDEX IF NOT EXISTS idx_permissions_active ON permissions (is_active);
CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions (role_id);
CREATE INDEX IF NOT EXISTS idx_user_permissions_user ON user_permissions (user_id);
CREATE INDEX IF NOT EXISTS idx_login_logs_user ON user_login_logs (user_id);
"""
