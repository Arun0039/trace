import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import app
from migrate_sqlite_to_postgres import _quote_identifier, dependency_order, migrate


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
                second_id INTEGER NOT NULL REFERENCES second_table(id)
            );
            CREATE TABLE second_table(
                id INTEGER PRIMARY KEY,
                first_id INTEGER NOT NULL REFERENCES first_table(id)
            );"""
        )

        with self.assertRaisesRegex(ValueError, "circular foreign-key"):
            dependency_order(self.db, ["first_table", "second_table"])

    def test_dependency_order_defers_nullable_foreign_key_cycles(self):
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

        order = dependency_order(self.db, ["first_table", "second_table"])

        self.assertEqual(set(order), {"first_table", "second_table"})

    def test_identifier_quoting_rejects_sql_fragments(self):
        self.assertEqual(_quote_identifier("project_table"), '"project_table"')
        with self.assertRaisesRegex(ValueError, "Unexpected database identifier"):
            _quote_identifier('projects"; DROP TABLE users; --')


@unittest.skipUnless(
    os.environ.get("QA_TEST_POSTGRES_URL"),
    "Set QA_TEST_POSTGRES_URL to run PostgreSQL integration tests.",
)
class PostgreSQLRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ["QA_TEST_POSTGRES_URL"]
        self.environment = patch.dict(
            os.environ,
            {
                "QA_ADMIN_PASSWORD": "PostgresSmokePassword2026",
                "QA_ALLOW_PUBLIC_AUTH": "true",
            },
        )
        self.environment.start()
        app.initialize(self.database_url)
        self.server = app.create_server(
            host="127.0.0.1", port=0, db_path=self.database_url
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.environment.stop()

    def request(self, path, method="GET", body=None, cookie=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(
            self.base_url + path, data=payload, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read()), response.headers
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def test_postgres_schema_signup_sessions_and_product_owner_api(self):
        email = f"postgres-{uuid.uuid4().hex}@example.test"
        status, signup, _ = self.request(
            "/api/auth/signup",
            "POST",
            {
                "first_name": "Postgres",
                "last_name": "Smoke",
                "email": email,
                "workspace_name": "PostgreSQL Integration Workspace",
                "password": "WorkspacePassword2026",
                "confirm_password": "WorkspacePassword2026",
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(signup["user"]["public_user_id"])
        with app.database(self.database_url) as db:
            subscription = db.execute(
                "SELECT status,trial_started_at,trial_ends_at FROM tenant_subscriptions WHERE tenant_id=?",
                (signup["workspace"]["id"],),
            ).fetchone()
            workspace_ids = db.execute(
                """SELECT public_tenant_id,public_workspace_id FROM tenants
                WHERE id=?""",
                (signup["workspace"]["id"],),
            ).fetchone()
        self.assertEqual(subscription["status"], "trialing")
        self.assertTrue(subscription["trial_started_at"])
        self.assertTrue(subscription["trial_ends_at"])
        self.assertTrue(workspace_ids["public_tenant_id"])
        self.assertTrue(workspace_ids["public_workspace_id"])

        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"password": "PostgresSmokePassword2026"},
        )
        self.assertEqual(status, 200)
        admin_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, dashboard, _ = self.request(
            "/api/product-owner/dashboard", cookie=admin_cookie
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(dashboard["tenants"], 2)

    def test_sqlite_migration_preserves_rows_and_primary_keys(self):
        import psycopg
        from psycopg import sql

        database_name = f"qa_migration_{uuid.uuid4().hex}"
        parsed_url = urlsplit(self.database_url)
        maintenance_url = urlunsplit(parsed_url._replace(path="/postgres"))
        target_url = urlunsplit(parsed_url._replace(path=f"/{database_name}"))
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                source_path = Path(temporary) / "migration-source.sqlite3"
                app.initialize(source_path)
                with app.database(source_path) as db:
                    now = "2026-10-29T00:00:00+00:00"
                    tenant_id, _ = app.create_workspace(
                        db, "PostgreSQL Migration Workspace", now
                    )
                    role_id = db.execute(
                        "SELECT id FROM roles WHERE name='Workspace Owner'"
                    ).fetchone()["id"]
                    user_cursor = db.execute(
                        """INSERT INTO users
                        (first_name,last_name,email,password_hash,role_id,status,
                        created_at,updated_at,public_user_id)
                        VALUES(?,?,?,?,?,'Active',?,?,?)""",
                        (
                            "Migration",
                            "Owner",
                            "migration-owner@example.test",
                            app.hash_password("WorkspacePassword2026"),
                            role_id,
                            now,
                            now,
                            str(uuid.uuid4()),
                        ),
                    )
                    user_id = user_cursor.lastrowid
                    db.execute(
                        "UPDATE tenants SET owner_user_id=? WHERE id=?",
                        (user_id, tenant_id),
                    )
                    db.execute(
                        """INSERT INTO tenant_memberships
                        (tenant_id,user_id,role_id,status,created_at,updated_at)
                        VALUES(?,?,?,'Active',?,?)""",
                        (tenant_id, user_id, role_id, now, now),
                    )
                    db.execute(
                        """INSERT INTO tenant_subscriptions
                        (tenant_id,status,plan,trial_started_at,trial_ends_at,
                        created_at,updated_at)
                        VALUES(?,'trialing','trial',?,?,?,?)""",
                        (tenant_id, now, "2026-11-12T00:00:00+00:00", now, now),
                    )
                    cursor = db.execute(
                        """INSERT INTO projects
                        (project_key,name,qa_owner,created_at,updated_at)
                        VALUES(?,?,?,?,?)""",
                        (
                            "POSTGRES-MIGRATION-VERIFY",
                            "PostgreSQL Migration Verification",
                            "Integration Test",
                            "2026-10-29T00:00:00+00:00",
                            "2026-10-29T00:00:00+00:00",
                        ),
                    )
                    expected_id = cursor.lastrowid

                row_counts = migrate(source_path, target_url)

                with app.database(target_url) as db:
                    project = db.execute(
                        "SELECT id,name FROM projects WHERE project_key=?",
                        ("POSTGRES-MIGRATION-VERIFY",),
                    ).fetchone()
                    tenant = db.execute(
                        "SELECT owner_user_id FROM tenants WHERE id=?",
                        (tenant_id,),
                    ).fetchone()
                    membership = db.execute(
                        """SELECT user_id FROM tenant_memberships
                        WHERE tenant_id=?""",
                        (tenant_id,),
                    ).fetchone()
                self.assertEqual(project["id"], expected_id)
                self.assertEqual(
                    project["name"], "PostgreSQL Migration Verification"
                )
                self.assertEqual(tenant["owner_user_id"], user_id)
                self.assertEqual(membership["user_id"], user_id)
                self.assertEqual(row_counts["projects"], 1)
        finally:
            with psycopg.connect(maintenance_url, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )

if __name__ == "__main__":
    unittest.main()
