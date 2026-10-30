#!/bin/sh
set -eu

DATA_ROOT="${QA_DATA_ROOT:-/app/qa-platform}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_ROOT/storage"
    chown appuser:appuser "$DATA_ROOT" 2>/dev/null || true
    if [ "$(stat -c '%u' "$DATA_ROOT/storage")" != "1000" ]; then
        chown -R appuser:appuser "$DATA_ROOT"
    fi
    exec gosu appuser "$@"
fi

exec "$@"
