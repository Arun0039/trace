import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_platform.core.config import (
    BACKEND_ROOT,
    DATA_ROOT,
    FRONTEND_ROOT,
    cors_origins,
    database_connection,
    database_path,
    load_environment_file,
    server_address,
)


class ConfigurationTests(unittest.TestCase):
    def test_separated_code_keeps_existing_runtime_data_paths(self):
        repository_root = Path(__file__).resolve().parents[2]
        self.assertEqual(BACKEND_ROOT, repository_root / "backend")
        self.assertEqual(FRONTEND_ROOT, repository_root / "frontend")
        self.assertEqual(DATA_ROOT, repository_root / "qa-platform")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QA_PLATFORM_DB", None)
            self.assertEqual(database_path(), DATA_ROOT / "qa_platform.sqlite3")

    def test_cors_origins_are_explicit_and_comma_separated(self):
        with patch.dict(
            os.environ,
            {"QA_CORS_ORIGINS": "http://127.0.0.1:3000, https://qa.example.test"},
        ):
            self.assertEqual(
                cors_origins(),
                frozenset({"http://127.0.0.1:3000", "https://qa.example.test"}),
            )

    def test_environment_file_preserves_existing_values_and_reads_quoted_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                "QA_CONFIG_EXISTING=from-file\nQA_CONFIG_QUOTED='two words'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"QA_CONFIG_EXISTING": "from-process"}, clear=False):
                os.environ.pop("QA_CONFIG_QUOTED", None)
                load_environment_file(env_file)
                self.assertEqual(os.environ["QA_CONFIG_EXISTING"], "from-process")
                self.assertEqual(os.environ["QA_CONFIG_QUOTED"], "two words")
                os.environ.pop("QA_CONFIG_QUOTED", None)

    def test_environment_file_rejects_malformed_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text("not-a-valid-entry\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1"):
                load_environment_file(env_file)

    def test_database_path_uses_override_without_touching_database(self):
        with patch.dict(os.environ, {"QA_PLATFORM_DB": "custom-data/qa.sqlite3"}):
            self.assertEqual(
                database_path(),
                DATA_ROOT / "custom-data" / "qa.sqlite3",
            )

    def test_database_connection_defaults_to_sqlite_and_accepts_postgresql_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QA_PLATFORM_DATABASE_URL", None)
            os.environ.pop("QA_PLATFORM_DB", None)
            self.assertEqual(database_connection(), database_path())

            url = "postgresql://qa-user:secret@db.example.test/qa?sslmode=require"
            os.environ["QA_PLATFORM_DATABASE_URL"] = url
            self.assertEqual(database_connection(), url)

    def test_database_connection_rejects_non_postgresql_urls(self):
        with patch.dict(os.environ, {"QA_PLATFORM_DATABASE_URL": "mysql://db/qa"}):
            with self.assertRaisesRegex(ValueError, "QA_PLATFORM_DATABASE_URL"):
                database_connection()

    def test_remote_database_url_requires_tls(self):
        with patch.dict(
            os.environ,
            {"QA_PLATFORM_DATABASE_URL": "postgresql://qa-user:secret@db.example.test/qa"},
        ):
            with self.assertRaisesRegex(ValueError, "sslmode=require"):
                database_connection()

    def test_server_address_defaults_and_rejects_invalid_port(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QA_HOST", None)
            os.environ.pop("QA_PORT", None)
            self.assertEqual(server_address(), ("127.0.0.1", 8001))
            os.environ["QA_PORT"] = "0"
            self.assertEqual(server_address(), ("127.0.0.1", 0))
            os.environ["QA_PORT"] = "not-a-port"
            with self.assertRaisesRegex(ValueError, "QA_PORT"):
                server_address()
            os.environ["QA_PORT"] = "65536"
            with self.assertRaisesRegex(ValueError, "QA_PORT"):
                server_address()


if __name__ == "__main__":
    unittest.main()
