import unittest

from qa_platform.core.database import PostgresConnection


class FakeColumn:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, rows=(), names=()):
        self._rows = list(rows)
        self.description = [FakeColumn(name) for name in names] or None
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class FakeConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=(), prepare=None):
        self.statements.append((statement, parameters))
        if "information_schema.columns" in statement:
            return FakeCursor(
                [(0, "project_key", "text", 1, None, 0)],
                ("cid", "name", "type", "notnull", "dflt_value", "pk"),
            )
        if "RETURNING id" in statement:
            return FakeCursor([(42,)], ("id",))
        if statement == "SELECT 1":
            return FakeCursor([(1,)], ("?column?",))
        return FakeCursor()


class PostgresCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.raw = FakeConnection()
        self.db = PostgresConnection(self.raw)

    def test_insert_ignore_translates_placeholders_and_returns_generated_id(self):
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO projects(name) VALUES(?)",
            ("Platform",),
        )

        statement, parameters = self.raw.statements[-1]
        self.assertEqual(parameters, ("Platform",))
        self.assertIn("VALUES(%s)", statement)
        self.assertIn("ON CONFLICT DO NOTHING RETURNING id", statement)
        self.assertEqual(cursor.lastrowid, 42)

    def test_sqlite_schema_autoincrement_and_group_concat_are_translated(self):
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )"""
        )
        self.db.execute("SELECT GROUP_CONCAT(name, ', ') FROM projects")

        self.assertIn("id BIGSERIAL PRIMARY KEY", self.raw.statements[-2][0])
        self.assertIn("STRING_AGG(name, ', ')", self.raw.statements[-1][0])

    def test_create_table_removes_sqlite_collation_and_translates_blob(self):
        self.db.execute(
            """CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                image_data BLOB
            )"""
        )

        statement = self.raw.statements[-1][0]
        self.assertIn("email TEXT NOT NULL UNIQUE", statement)
        self.assertIn("image_data BYTEA", statement)
        self.assertNotIn("LOWER(UNIQUE)", statement)

    def test_executescript_skips_sqlite_triggers_and_keeps_following_tables(self):
        self.db.executescript(
            """CREATE TABLE projects(id INTEGER PRIMARY KEY);
            CREATE TRIGGER IF NOT EXISTS cleanup AFTER DELETE ON projects BEGIN
                DELETE FROM attachments WHERE project_id=OLD.id;
            END;
            CREATE TABLE attachments(id INTEGER PRIMARY KEY);"""
        )

        statements = [statement for statement, _ in self.raw.statements]
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.lstrip().startswith("CREATE TABLE") for statement in statements))

    def test_sqlite_schema_introspection_returns_named_rows(self):
        row = self.db.execute("PRAGMA table_info(projects)").fetchone()

        self.assertEqual(row["name"], "project_key")
        self.assertEqual(row[1], "project_key")
        self.assertEqual(self.raw.statements[-1][1], ("projects",))

    def test_case_insensitive_email_filter_translates_to_lower(self):
        self.db.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE", ("A@B.C",))

        self.assertIn("LOWER(email) = LOWER(%s)", self.raw.statements[-1][0])


if __name__ == "__main__":
    unittest.main()
