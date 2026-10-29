#!/bin/sh
set -eu

# Creates a database dump plus a compressed copy of uploaded storage.
# Required for PostgreSQL: pg_dump on PATH and DATABASE_URL.
# Defaults for SQLite: qa-platform/qa_platform.sqlite3 and qa-platform/storage.

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-"$ROOT_DIR/backups"}
DATABASE_URL=${DATABASE_URL:-${QA_PLATFORM_DATABASE_URL:-}}
SQLITE_PATH=${SQLITE_PATH:-"$ROOT_DIR/qa-platform/qa_platform.sqlite3"}
STORAGE_DIR=${QA_STORAGE_DIR:-"$ROOT_DIR/qa-platform/storage"}
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-30}

umask 077
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PREFIX="$BACKUP_DIR/qa-platform-$STAMP"

if [ -n "$DATABASE_URL" ]; then
    command -v pg_dump >/dev/null 2>&1 || {
        printf 'pg_dump is required for PostgreSQL backups\n' >&2
        exit 1
    }
    pg_dump "$DATABASE_URL" --format=custom --no-owner --file="$PREFIX.dump"
    printf 'database=%s\n' "$PREFIX.dump" > "$PREFIX.manifest"
else
    [ -f "$SQLITE_PATH" ] || {
        printf 'SQLite database not found: %s\n' "$SQLITE_PATH" >&2
        exit 1
    }
    python - "$SQLITE_PATH" "$PREFIX.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
    printf 'database=%s\n' "$PREFIX.sqlite3" > "$PREFIX.manifest"
fi

if [ -d "$STORAGE_DIR" ]; then
    tar -czf "$PREFIX-storage.tar.gz" -C "$(dirname "$STORAGE_DIR")" "$(basename "$STORAGE_DIR")"
    printf 'storage=%s\n' "$PREFIX-storage.tar.gz" >> "$PREFIX.manifest"
fi

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$BACKUP_DIR" && sha256sum "$(basename "$PREFIX")".*) > "$PREFIX.sha256"
fi

if [ "$RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
    find "$BACKUP_DIR" -type f -name 'qa-platform-*' -mtime "+$RETENTION_DAYS" -delete
fi

printf 'Backup written to %s\n' "$PREFIX.manifest"
