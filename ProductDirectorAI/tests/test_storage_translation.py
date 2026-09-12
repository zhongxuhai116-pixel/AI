"""A03 数据库层：方言改写与连接适配的单元测试（不需要真实 PostgreSQL）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "apps" / "api"))

from productdirector_api import storage  # noqa: E402


class FakeCursor:
    def __init__(self, recorder, statement, params) -> None:
        self._recorder = recorder
        self.statement = statement
        self.params = params
        self.rowcount = 0

    def fetchone(self):
        return {"ok": 1}

    def fetchall(self):
        return [{"ok": 1}]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeRawConnection:
    """模拟 psycopg 连接，用于验证适配器的调用形状与事务语义。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        parent = self

        class Factory:
            def execute(self, statement, params=()):
                parent.calls.append((statement, tuple(params)))
                return FakeCursor(None, statement, params)

            def executemany(self, statement, seq):
                parent.calls.append((statement, tuple(seq)))
                return FakeCursor(None, statement, seq)

            def fetchone(self):
                return {"ok": 1}

            def fetchall(self):
                return [{"ok": 1}]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Factory()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class DialectTests(unittest.TestCase):
    def test_placeholders_become_pyformat(self) -> None:
        self.assertEqual(
            storage.to_postgres_sql("SELECT * FROM jobs WHERE id = ? AND status = ?"),
            "SELECT * FROM jobs WHERE id = %s AND status = %s",
        )

    def test_literal_percent_is_escaped_for_driver_formatting(self) -> None:
        self.assertEqual(
            storage.to_postgres_sql("SELECT id FROM assets WHERE name LIKE ? || '%'"),
            "SELECT id FROM assets WHERE name LIKE %s || '%%'",
        )

    def test_insert_or_ignore_becomes_on_conflict_do_nothing(self) -> None:
        self.assertEqual(
            storage.to_postgres_sql("INSERT OR IGNORE INTO owners(id, name) VALUES (?, ?)"),
            "INSERT INTO owners(id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        )

    def test_insert_or_ignore_keeps_an_existing_conflict_clause(self) -> None:
        statement = storage.to_postgres_sql(
            "INSERT OR IGNORE INTO x(id) VALUES (?) ON CONFLICT (id) DO UPDATE SET id = excluded.id"
        )
        self.assertEqual(statement.count("ON CONFLICT"), 1)
        self.assertIn("DO UPDATE", statement)

    def test_begin_immediate_becomes_plain_begin(self) -> None:
        self.assertEqual(storage.to_postgres_sql("BEGIN IMMEDIATE"), "BEGIN")

    def test_script_split_ignores_empty_statements(self) -> None:
        self.assertEqual(storage.split_script("SELECT 1; ; SELECT 2;\n"), ["SELECT 1", "SELECT 2"])

    def test_url_helpers(self) -> None:
        self.assertTrue(storage.is_postgres("postgresql://u@127.0.0.1:5432/db"))
        self.assertTrue(storage.is_postgres("postgres://u@127.0.0.1:5432/db"))
        self.assertFalse(storage.is_postgres("sqlite:///tmp/x.db"))
        self.assertEqual(storage.sqlite_path_from_url("sqlite:///tmp/x.db"), "/tmp/x.db")
        self.assertEqual(storage.sqlite_path_from_url("sqlite:////abs/x.db"), "//abs/x.db")
        self.assertIsNone(storage.sqlite_path_from_url("postgresql://u@h/db"))


class PostgresAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = FakeRawConnection()
        self.connection = storage.PostgresConnection(self.raw)

    def test_execute_translates_and_returns_sqlite_shaped_cursor(self) -> None:
        cursor = self.connection.execute("SELECT * FROM jobs WHERE id = ?", ("job-1",))
        self.assertEqual(self.raw.calls[0], ("SELECT * FROM jobs WHERE id = %s", ("job-1",)))
        self.assertEqual(cursor.fetchone(), {"ok": 1})
        self.assertEqual(cursor.fetchall(), [{"ok": 1}])

    def test_executescript_runs_each_statement(self) -> None:
        self.connection.executescript("CREATE TABLE a (id TEXT); CREATE TABLE b (id TEXT);")
        self.assertEqual([call[0] for call in self.raw.calls], ["CREATE TABLE a (id TEXT)", "CREATE TABLE b (id TEXT)"])

    def test_context_manager_commits_on_success(self) -> None:
        with self.connection as db:
            db.execute("SELECT 1")
        self.assertEqual(self.raw.commits, 1)
        self.assertEqual(self.raw.rollbacks, 0)

    def test_context_manager_rolls_back_on_error(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.connection as db:
                db.execute("SELECT 1")
                raise RuntimeError("boom")
        self.assertEqual(self.raw.commits, 0)
        self.assertEqual(self.raw.rollbacks, 1)

    def test_table_columns_uses_information_schema_on_postgres(self) -> None:
        class ColumnCursor:
            def fetchall(self):
                return [{"column_name": "id"}, {"column_name": "name"}]

        connection = storage.PostgresConnection(FakeRawConnection())
        connection.execute = lambda sql, params=(): ColumnCursor()  # type: ignore[assignment]
        self.assertEqual(storage.table_columns(connection, "jobs"), ["id", "name"])

    def test_begin_writer_uses_plain_begin_on_postgres(self) -> None:
        storage.begin_writer(self.connection)
        self.assertEqual(self.raw.calls[0][0], "BEGIN")


class DefaultBackendTests(unittest.TestCase):
    def test_default_backend_is_sqlite(self) -> None:
        original = storage.database_url()
        try:
            storage.database_url = lambda: ""  # type: ignore[assignment]
            self.assertFalse(storage.is_postgres())
        finally:
            storage.database_url = lambda: original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
