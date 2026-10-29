#!/bin/sh
set -eu

# Destructive restore. Stop the application first and take a fresh backup.
# Usage: RESTORE_CONFIRM=yes ./deploy/restore.sh <backup-prefix-without-extension>

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DATABASE_URL=${DATABASE_URL:-${QA_PLATFORM_DATABASE_URL:-}}
SQLITE_PATH=${SQLITE_PATH:-"$ROOT_DIR/qa-platform/qa_platform.sqlite3"}
STORAGE_DIR=${QA_STORAGE_DIR:-"$ROOT_DIR/qa-platform/storage"}
PREFIX=${1:-}

if [ "$(printf '%s' "${RESTORE_CONFIRM:-}")" != "yes" ]; then
    printf 'Refusing to restore without RESTORE_CONFIRM=yes\n' >&2
    exit 1
fi
if [ -z "$PREFIX" ]; then
    printf 'Usage: RESTORE_CONFIRM=yes %s <backup-prefix>\n' "$0" >&2
    exit 1
fi
PREFIX=${PREFIX%.manifest}
PREFIX=${PREFIX%.sha256}

if [ -n "$DATABASE_URL" ]; then
    command -v pg_restore >/dev/null 2>&1 || {
        printf 'pg_restore is required for PostgreSQL restores\n' >&2
        exit 1
    }
    pg_restore "$DATABASE_URL" --clean --if-exists --no-owner "$PREFIX.dump"
else
    [ -f "$PREFIX.sqlite3" ] || {
        printf 'SQLite backup not found: %s\n' "$PREFIX.sqlite3" >&2
        exit 1
    }
    mkdir -p "$(dirname "$SQLITE_PATH")"
    cp "$PREFIX.sqlite3" "$SQLITE_PATH"
fi

if [ -f "$PREFIX-storage.tar.gz" ]; then
    mkdir -p "$(dirname "$STORAGE_DIR")"
    tar -xzf "$PREFIX-storage.tar.gz" -C "$(dirname "$STORAGE_DIR")"
fi

printf 'Restore complete. Restart the application and run /health plus a sign-in test.\n'
