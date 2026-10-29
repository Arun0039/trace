"""Versioned schema changes for Product Owner operations."""

from __future__ import annotations

import uuid


MIGRATION_ID = "0001_product_owner_dashboard"


def _columns(db, table: str) -> set[str]:
    return {
        row["name"] for row in db.execute(f"PRAGMA table_info({table})")
    }


def upgrade(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )"""
    )
    if db.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
    ).fetchone():
        return

    for table, columns in (
        ("users", (("public_user_id", "TEXT"),)),
        ("tenants", (("public_tenant_id", "TEXT"), ("public_workspace_id", "TEXT"))),
    ):
        existing = _columns(db, table)
        for column, declaration in columns:
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    for row in db.execute("SELECT id FROM users WHERE public_user_id IS NULL"):
        db.execute(
            "UPDATE users SET public_user_id=? WHERE id=?",
            (str(uuid.uuid4()), row["id"]),
        )
    for row in db.execute(
        """SELECT id FROM tenants
        WHERE public_tenant_id IS NULL OR public_workspace_id IS NULL"""
    ):
        db.execute(
            """UPDATE tenants SET public_tenant_id=COALESCE(public_tenant_id,?),
            public_workspace_id=COALESCE(public_workspace_id,?) WHERE id=?""",
            (str(uuid.uuid4()), str(uuid.uuid4()), row["id"]),
        )

    db.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_users_public_user_id
            ON users(public_user_id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_public_tenant_id
            ON tenants(public_tenant_id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_public_workspace_id
            ON tenants(public_workspace_id);
        CREATE TABLE IF NOT EXISTS plans (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
            storage_limit_bytes INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS ix_usage_events_tenant_time
            ON usage_events(tenant_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_usage_events_type_time
            ON usage_events(event_type,created_at DESC);
        CREATE TABLE IF NOT EXISTS storage_usage_history (
            id INTEGER PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            total_bytes INTEGER NOT NULL,
            filesystem_bytes INTEGER NOT NULL,
            database_bytes INTEGER NOT NULL,
            calculated_at TEXT NOT NULL,
            UNIQUE(tenant_id,calculated_at)
        );
        CREATE INDEX IF NOT EXISTS ix_storage_usage_history_tenant_time
            ON storage_usage_history(tenant_id,calculated_at DESC);
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id INTEGER PRIMARY KEY,
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_label TEXT NOT NULL,
            action TEXT NOT NULL,
            target_tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
            reason TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('success','failure')),
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_time
            ON admin_audit_logs(created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_tenant_time
            ON admin_audit_logs(target_tenant_id,created_at DESC);
        """
    )
    if hasattr(db, "_connection"):
        db.execute(
            """CREATE OR REPLACE FUNCTION trace_immutable_user_id()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.public_user_id IS DISTINCT FROM NEW.public_user_id THEN
                    RAISE EXCEPTION 'public user IDs are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql"""
        )
        db.execute(
            """CREATE OR REPLACE FUNCTION trace_immutable_tenant_ids()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.public_tenant_id IS DISTINCT FROM NEW.public_tenant_id
                   OR OLD.public_workspace_id IS DISTINCT FROM NEW.public_workspace_id THEN
                    RAISE EXCEPTION 'public tenant and workspace IDs are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql"""
        )
        db.execute("DROP TRIGGER IF EXISTS trace_immutable_user_id ON users")
        db.execute(
            """CREATE TRIGGER trace_immutable_user_id BEFORE UPDATE OF public_user_id
            ON users FOR EACH ROW EXECUTE FUNCTION trace_immutable_user_id()"""
        )
        db.execute("DROP TRIGGER IF EXISTS trace_immutable_tenant_ids ON tenants")
        db.execute(
            """CREATE TRIGGER trace_immutable_tenant_ids
            BEFORE UPDATE OF public_tenant_id,public_workspace_id ON tenants
            FOR EACH ROW EXECUTE FUNCTION trace_immutable_tenant_ids()"""
        )
    else:
        db.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trace_immutable_user_id
            BEFORE UPDATE OF public_user_id ON users
            WHEN OLD.public_user_id IS NOT NEW.public_user_id
            BEGIN SELECT RAISE(ABORT,'public user IDs are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS trace_immutable_tenant_ids
            BEFORE UPDATE OF public_tenant_id,public_workspace_id ON tenants
            WHEN OLD.public_tenant_id IS NOT NEW.public_tenant_id
              OR OLD.public_workspace_id IS NOT NEW.public_workspace_id
            BEGIN SELECT RAISE(ABORT,'public tenant and workspace IDs are immutable'); END;
            """
        )
    db.execute(
        """INSERT OR IGNORE INTO plans
        (code,name,status,storage_limit_bytes,created_at,updated_at)
        VALUES('free_trial','Free trial','active',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    db.execute(
        """INSERT OR IGNORE INTO plans
        (code,name,status,storage_limit_bytes,created_at,updated_at)
        VALUES('legacy','Legacy','active',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    db.execute(
        """INSERT OR IGNORE INTO plans
        (code,name,status,storage_limit_bytes,created_at,updated_at)
        VALUES('starter','Starter','active',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    db.execute(
        """INSERT OR IGNORE INTO plans
        (code,name,status,storage_limit_bytes,created_at,updated_at)
        VALUES('pro','Pro','active',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    db.execute(
        "INSERT INTO schema_migrations(migration_id,applied_at) VALUES(?,CURRENT_TIMESTAMP)",
        (MIGRATION_ID,),
    )


def downgrade(db) -> None:
    """Remove only Product Owner-owned schema; preserve customer and audit history."""
    if hasattr(db, "_connection"):
        db.execute("DROP TRIGGER IF EXISTS trace_immutable_user_id ON users")
        db.execute("DROP TRIGGER IF EXISTS trace_immutable_tenant_ids ON tenants")
        db.execute("DROP FUNCTION IF EXISTS trace_immutable_user_id()")
        db.execute("DROP FUNCTION IF EXISTS trace_immutable_tenant_ids()")
    else:
        db.execute("DROP TRIGGER IF EXISTS trace_immutable_user_id")
        db.execute("DROP TRIGGER IF EXISTS trace_immutable_tenant_ids")
    db.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_tenant_time")
    db.execute("DROP INDEX IF EXISTS ix_admin_audit_logs_time")
    db.execute("DROP INDEX IF EXISTS ix_usage_events_type_time")
    db.execute("DROP INDEX IF EXISTS ix_usage_events_tenant_time")
    db.execute("DROP INDEX IF EXISTS ix_storage_usage_history_tenant_time")
    db.execute("DROP INDEX IF EXISTS ux_tenants_public_workspace_id")
    db.execute("DROP INDEX IF EXISTS ux_tenants_public_tenant_id")
    db.execute("DROP INDEX IF EXISTS ux_users_public_user_id")
    db.execute("DROP TABLE IF EXISTS admin_audit_logs")
    db.execute("DROP TABLE IF EXISTS usage_events")
    db.execute("DROP TABLE IF EXISTS storage_usage_history")
    db.execute("DROP TABLE IF EXISTS plans")
    for table, columns in (
        ("users", ("public_user_id",)),
        ("tenants", ("public_tenant_id", "public_workspace_id")),
    ):
        for column in columns:
            if column in _columns(db, table):
                db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    db.execute(
        "DELETE FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
    )
    if db.execute("SELECT COUNT(*) count FROM schema_migrations").fetchone()["count"] == 0:
        db.execute("DROP TABLE IF EXISTS schema_migrations")
