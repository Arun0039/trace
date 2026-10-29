"""SQLite and PostgreSQL connection compatibility for the QA platform."""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any


try:
    import psycopg
    from psycopg.rows import tuple_row
except ImportError:
    psycopg = None
    tuple_row = None

try:
    from psycopg_pool import ConnectionPool, PoolTimeout
except ImportError:
    ConnectionPool = None
    PoolTimeout = None


DATABASE_ERRORS = (
    (sqlite3.Error,)
    + ((psycopg.Error,) if psycopg else ())
    + ((PoolTimeout,) if PoolTimeout else ())
)
DATABASE_INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + (
    (psycopg.IntegrityError,) if psycopg else ()
)

_DATABASE_POOLS: dict[str, Any] = {}
_DATABASE_POOLS_LOCK = threading.Lock()
_DATABASE_POOL_MAX_SIZE = 10

_INSERT_ID_TABLES = {
    "application_analyses",
    "bugs",
    "custom_fields",
    "developer_errors",
    "executions",
    "form_attachments",
    "projects",
    "requirement_documents",
    "requirements",
    "requirement_suggestions",
    "roles",
    "teams",
    "test_cases",
    "users",
}


class DatabaseRow(dict):
    """Mapping row that preserves sqlite3.Row's integer-index behavior."""

    def __init__(self, names: list[str], values: tuple[Any, ...]):
        super().__init__()
        for name, value in zip(names, values):
            self.setdefault(name, value)
        self._names = names

    def __getitem__(self, key):
        if isinstance(key, int):
            key = self._names[key]
        return super().__getitem__(key)


class PostgresCursor:
    def __init__(self, cursor, lastrowid=None, buffered_row=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self._buffered_row = buffered_row

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def _row(self, values):
        if values is None:
            return None
        names = [column.name for column in self._cursor.description]
        return DatabaseRow(names, values)

    def fetchone(self):
        if self._buffered_row is not None:
            row, self._buffered_row = self._buffered_row, None
            return row
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        rows = []
        if self._buffered_row is not None:
            rows.append(self._buffered_row)
            self._buffered_row = None
        rows.extend(self._row(values) for values in self._cursor.fetchall())
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self, connection, release_connection=None):
        self._connection = connection
        self._release_connection = release_connection
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        return False

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._release_connection is None:
            self._connection.close()
        else:
            self._release_connection(self._connection)

    def executescript(self, script: str):
        script = re.sub(
            r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+\w+.*?\bEND\s*;",
            "",
            script,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def execute(self, sql: str, parameters=()):
        pragma_match = re.fullmatch(
            r"\s*PRAGMA\s+table_info\((\w+)\)\s*;?\s*", sql, re.IGNORECASE
        )
        if pragma_match:
            return self._table_info(pragma_match.group(1))

        statement = re.sub(
            r"^\s*PRAGMA\s+foreign_keys\s*=\s*ON\s*;?\s*$", "SELECT 1",
            sql, flags=re.IGNORECASE,
        )
        statement = re.sub(
            r"\bGROUP_CONCAT\s*\(\s*([^,()]+?)(?:\s*,\s*([^()]+?))?\s*\)",
            self._translate_group_concat,
            statement,
            flags=re.IGNORECASE,
        )
        if re.match(r"\s*CREATE\s+TABLE\b", statement, re.IGNORECASE):
            statement = re.sub(
                r"\s+COLLATE\s+NOCASE\b", "", statement, flags=re.IGNORECASE
            )
        else:
            statement = re.sub(
                r"(\b[\w.]+)\s*=\s*(\?)\s+COLLATE\s+NOCASE\b",
                r"LOWER(\1) = LOWER(\2)",
                statement,
                flags=re.IGNORECASE,
            )
            statement = re.sub(
                r"\b([\w.]+)\s+COLLATE\s+NOCASE\b",
                r"LOWER(\1)",
                statement,
                flags=re.IGNORECASE,
            )
            statement = re.sub(
                r"\s+COLLATE\s+NOCASE\b", "", statement, flags=re.IGNORECASE
            )
        statement = re.sub(r"\bBLOB\b", "BYTEA", statement, flags=re.IGNORECASE)

        insert_match = re.match(
            r"\s*INSERT\s+OR\s+IGNORE\s+INTO\s+(\w+)", statement, re.IGNORECASE
        )
        if insert_match:
            statement = re.sub(
                r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO",
                statement, count=1, flags=re.IGNORECASE,
            )
            if not re.search(r"\bON\s+CONFLICT\b", statement, re.IGNORECASE):
                statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        statement = re.sub(
            r"\bid\s+INTEGER\s+PRIMARY\s+KEY\b",
            "id BIGSERIAL PRIMARY KEY",
            statement,
            flags=re.IGNORECASE,
        )
        statement = statement.replace("?", "%s")

        table_match = re.match(
            r"\s*INSERT\s+INTO\s+(\w+)", statement, re.IGNORECASE
        )
        returns_id = bool(
            table_match
            and table_match.group(1).lower() in _INSERT_ID_TABLES
            and not re.search(r"\bRETURNING\b", statement, re.IGNORECASE)
        )
        if returns_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"

        cursor = self._connection.execute(statement, parameters, prepare=False)
        lastrowid = None
        buffered_row = None
        if returns_id:
            returned = cursor.fetchone()
            if returned is not None:
                lastrowid = returned[0]
                buffered_row = DatabaseRow(["id"], returned)
        return PostgresCursor(cursor, lastrowid, buffered_row)

    @staticmethod
    def _translate_group_concat(match):
        expression = match.group(1).strip()
        separator = match.group(2)
        if separator is None:
            separator = "','"
        return f"STRING_AGG(CAST({expression} AS TEXT), {separator.strip()})"

    def _table_info(self, table_name: str):
        cursor = self._connection.execute(
            """SELECT ordinal_position - 1 AS cid, column_name AS name,
            data_type AS type, CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
            column_default AS dflt_value,
            CASE WHEN column_name = 'id' THEN 1 ELSE 0 END AS pk
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position""",
            (table_name,),
            prepare=False,
        )
        return PostgresCursor(cursor)


def _postgres_pool(database: str):
    if ConnectionPool is None:
        raise RuntimeError(
            "PostgreSQL connection pooling requires psycopg-pool. "
            "Install the project requirements."
        )
    with _DATABASE_POOLS_LOCK:
        pool = _DATABASE_POOLS.get(database)
        if pool is None:
            pool = ConnectionPool(
                conninfo=database,
                min_size=1,
                max_size=_DATABASE_POOL_MAX_SIZE,
                timeout=10,
                kwargs={"row_factory": tuple_row, "connect_timeout": 10},
                open=True,
            )
            _DATABASE_POOLS[database] = pool
        return pool


def close_database_pools() -> None:
    with _DATABASE_POOLS_LOCK:
        pools = tuple(_DATABASE_POOLS.values())
        _DATABASE_POOLS.clear()
    for pool in pools:
        pool.close()


def connect_database(database: Path | str):
    if isinstance(database, str) and database.startswith(("postgres://", "postgresql://")):
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL support requires psycopg. Install the project requirements."
            )
        pool = _postgres_pool(database)
        return PostgresConnection(pool.getconn(timeout=10), pool.putconn)
    db = sqlite3.connect(str(database))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db
