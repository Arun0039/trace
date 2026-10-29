"""Copy an existing QA Platform SQLite database into an empty PostgreSQL database."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import app
from qa_platform.core.config import database_path


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BATCH_SIZE = 500


def _tables(sqlite_db: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in sqlite_db.execute(
            """SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"""
        )
    ]


def _nullable_foreign_keys(
    sqlite_db: sqlite3.Connection, tables: list[str]
) -> dict[str, set[str]]:
    table_set = set(tables)
    deferred = {table: set() for table in tables}
    for table in tables:
        nullable_columns = {
            row[1]
            for row in sqlite_db.execute(f'PRAGMA table_info("{table}")')
            if not row[3] and not row[5]
        }
        for foreign_key in sqlite_db.execute(f'PRAGMA foreign_key_list("{table}")'):
            if foreign_key[2] in table_set and foreign_key[3] in nullable_columns:
                deferred[table].add(foreign_key[3])
    return deferred


def dependency_order(sqlite_db: sqlite3.Connection, tables: list[str]) -> list[str]:
    table_set = set(tables)
    dependencies = {table: set() for table in tables}
    deferred = _nullable_foreign_keys(sqlite_db, tables)
    for table in tables:
        for foreign_key in sqlite_db.execute(f'PRAGMA foreign_key_list("{table}")'):
            referenced_table = foreign_key[2]
            if (
                referenced_table in table_set
                and foreign_key[3] not in deferred[table]
            ):
                dependencies[table].add(referenced_table)

    ordered = []
    ready = sorted(table for table, refs in dependencies.items() if not refs)
    while ready:
        table = ready.pop(0)
        ordered.append(table)
        for child in sorted(dependencies):
            if table in dependencies[child]:
                dependencies[child].remove(table)
                if not dependencies[child] and child not in ordered and child not in ready:
                    ready.append(child)
        ready.sort()

    if len(ordered) != len(tables):
        remaining = sorted(set(tables) - set(ordered))
        raise ValueError(
            "The SQLite schema contains circular foreign-key dependencies that "
            f"cannot be copied safely: {', '.join(remaining)}."
        )
    return ordered


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"Unexpected database identifier: {identifier!r}")
    return f'"{identifier}"'


def _postgres_tables(db) -> set[str]:
    return {
        row["table_name"]
        for row in db.execute(
            """SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema() AND table_type='BASE TABLE'"""
        )
    }


def _target_columns(db, table: str) -> set[str]:
    return {
        row["column_name"]
        for row in db.execute(
            """SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=?""",
            (table,),
        )
    }


def _copy_table(
    source,
    target,
    table: str,
    columns: list[str],
    deferred_foreign_keys: set[str] | None = None,
) -> int:
    deferred_foreign_keys = deferred_foreign_keys or set()
    quoted_table = _quote_identifier(table)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    select_cursor = source.execute(f"SELECT {quoted_columns} FROM {quoted_table}")
    copied = 0
    while True:
        rows = select_cursor.fetchmany(_BATCH_SIZE)
        if not rows:
            break
        for row in rows:
            target.execute(
                f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})",
                tuple(
                    None if column in deferred_foreign_keys else row[column]
                    for column in columns
                ),
            )
        copied += len(rows)
    return copied


def _restore_deferred_foreign_keys(
    source, target, table: str, foreign_key_columns: set[str]
) -> None:
    if not foreign_key_columns:
        return
    primary_key = [
        row["name"]
        for row in sorted(
            source.execute(f'PRAGMA table_info("{table}")'),
            key=lambda row: row[5],
        )
        if row[5]
    ]
    if not primary_key:
        raise ValueError(
            f"Cannot restore deferred foreign keys for keyless table {table!r}."
        )

    selected_columns = list(dict.fromkeys([*primary_key, *sorted(foreign_key_columns)]))
    quoted_table = _quote_identifier(table)
    quoted_columns = ", ".join(
        _quote_identifier(column) for column in selected_columns
    )
    assignments = ", ".join(
        f"{_quote_identifier(column)}=?"
        for column in sorted(foreign_key_columns)
    )
    predicate = " AND ".join(
        f"{_quote_identifier(column)}=?" for column in primary_key
    )
    rows = source.execute(
        f"SELECT {quoted_columns} FROM {quoted_table}"
    ).fetchall()
    for row in rows:
        target.execute(
            f"UPDATE {quoted_table} SET {assignments} WHERE {predicate}",
            tuple(row[column] for column in sorted(foreign_key_columns))
            + tuple(row[column] for column in primary_key),
        )


def _reset_sequence(target, table: str) -> None:
    sequence_column = target.execute(
        """SELECT column_default FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name=? AND column_name='id'""",
        (table,),
    ).fetchone()
    if not sequence_column or not sequence_column["column_default"]:
        return
    if "nextval(" not in sequence_column["column_default"]:
        return
    quoted_table = _quote_identifier(table)
    max_id = target.execute(f"SELECT MAX(id) FROM {quoted_table}").fetchone()[0]
    sequence = target.execute(
        "SELECT pg_get_serial_sequence(?, 'id')", (table,)
    ).fetchone()[0]
    if sequence:
        target.execute(
            "SELECT setval(?, ?, ?)",
            (sequence, max_id if max_id is not None else 1, max_id is not None),
        )


def _remove_bootstrap_rows(target) -> None:
    for table in ("global_admin_profile", "ai_provider_configs", "ai_settings", "roles"):
        target.execute(f"DELETE FROM {_quote_identifier(table)}")


def migrate(source_path: Path | str, target_url: str) -> dict[str, int]:
    source_path = Path(source_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")
    if not target_url.startswith(("postgres://", "postgresql://")):
        raise ValueError("A PostgreSQL target URL is required.")

    with tempfile.TemporaryDirectory(prefix="qa-platform-migration-") as temporary:
        snapshot_path = Path(temporary) / "migration.sqlite3"
        source_uri = source_path.as_uri() + "?mode=ro"
        original = sqlite3.connect(source_uri, uri=True)
        snapshot = sqlite3.connect(snapshot_path)
        try:
            original.backup(snapshot)
        finally:
            snapshot.close()
            original.close()

        app.initialize(snapshot_path)
        source = sqlite3.connect(
            snapshot_path.as_uri() + "?mode=ro", uri=True
        )
        source.row_factory = sqlite3.Row
        try:
            tables = _tables(source)
            ordered_tables = dependency_order(source, tables)
            deferred_foreign_keys = _nullable_foreign_keys(source, tables)
            with app.database(target_url) as target:
                target_tables = _postgres_tables(target)
                for table in sorted(set(tables) & target_tables):
                    if target.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0]:
                        raise ValueError(
                            "Migration target is not empty. Refusing to overwrite "
                            f"existing data in table {table!r}."
                        )

            app.initialize(target_url)
            try:
                with app.database(target_url) as target:
                    target_tables = _postgres_tables(target)
                    missing = sorted(set(tables) - target_tables)
                    if missing:
                        raise ValueError(
                            "PostgreSQL schema is missing source tables: "
                            + ", ".join(missing)
                        )

                    for table in reversed(ordered_tables):
                        target.execute(f"DELETE FROM {_quote_identifier(table)}")

                    counts = {}
                    for table in ordered_tables:
                        source_columns = [
                            row["name"]
                            for row in source.execute(f'PRAGMA table_info("{table}")')
                        ]
                        target_columns = _target_columns(target, table)
                        unavailable = sorted(set(source_columns) - target_columns)
                        if unavailable:
                            raise ValueError(
                                f"PostgreSQL table {table!r} cannot store source columns: "
                                + ", ".join(unavailable)
                            )
                        counts[table] = _copy_table(
                            source,
                            target,
                            table,
                            source_columns,
                            deferred_foreign_keys[table],
                        )

                    for table in ordered_tables:
                        _restore_deferred_foreign_keys(
                            source,
                            target,
                            table,
                            deferred_foreign_keys[table],
                        )

                    for table in ordered_tables:
                        expected = source.execute(
                            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                        ).fetchone()[0]
                        actual = target.execute(
                            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                        ).fetchone()[0]
                        if expected != actual:
                            raise RuntimeError(
                                f"Row-count verification failed for {table}: "
                                f"SQLite has {expected}; PostgreSQL has {actual}."
                            )
                        _reset_sequence(target, table)
            except Exception:
                with app.database(target_url) as target:
                    _remove_bootstrap_rows(target)
                raise
            return counts
        finally:
            source.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy the configured SQLite database to PostgreSQL."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=database_path(),
        help="SQLite database file (default: QA_PLATFORM_DB or qa_platform.sqlite3).",
    )
    args = parser.parse_args(argv)
    target_url = os.environ.get("QA_PLATFORM_DATABASE_URL", "").strip()
    if not target_url:
        parser.error("Set QA_PLATFORM_DATABASE_URL to the PostgreSQL target URL.")
    try:
        counts = migrate(args.source, target_url)
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    print("SQLite source retained unchanged. PostgreSQL row counts verified:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
