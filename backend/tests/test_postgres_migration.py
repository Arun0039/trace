import sqlite3
import unittest

from migrate_sqlite_to_postgres import _quote_identifier, dependency_order


class PostgresMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_dependency_order_places_parent_tables_before_children(self):
        self.db.executescript(
            """CREATE TABLE projects(id INTEGER PRIMARY KEY);
            CREATE TABLE requirements(
                id INTEGER PRIMARY KEY,
                project_id INTEGER REFERENCES projects(id)
            );
            CREATE TABLE test_cases(
                id INTEGER PRIMARY KEY,
                requirement_id INTEGER REFERENCES requirements(id)
            );"""
        )

        order = dependency_order(
            self.db, ["test_cases", "requirements", "projects"]
        )

        self.assertLess(order.index("projects"), order.index("requirements"))
        self.assertLess(order.index("requirements"), order.index("test_cases"))

    def test_dependency_order_rejects_circular_references(self):
        self.db.executescript(
            """CREATE TABLE first_table(
                id INTEGER PRIMARY KEY,
                second_id INTEGER REFERENCES second_table(id)
            );
            CREATE TABLE second_table(
                id INTEGER PRIMARY KEY,
                first_id INTEGER REFERENCES first_table(id)
            );"""
        )

        with self.assertRaisesRegex(ValueError, "circular foreign-key"):
            dependency_order(self.db, ["first_table", "second_table"])

    def test_identifier_quoting_rejects_sql_fragments(self):
        self.assertEqual(_quote_identifier("project_table"), '"project_table"')
        with self.assertRaisesRegex(ValueError, "Unexpected database identifier"):
            _quote_identifier('projects"; DROP TABLE users; --')


if __name__ == "__main__":
    unittest.main()
