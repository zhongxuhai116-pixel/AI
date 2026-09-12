"""数据库访问层：同一套调用同时支持 SQLite（默认）与 PostgreSQL。

设计取舍：SQLite 路径**原样返回** `sqlite3` 连接，行为与历史实现完全一致；
PostgreSQL 路径用一个薄包装适配 `execute/executemany/executescript` 与
`with connect() as db:` 的事务语义（成功提交、异常回滚），并把少量
SQLite 方言改写成 PostgreSQL 方言。

这样 `main.py` 里几百处 `with connect() as db:` / `db.execute(...)` 不需要改写，
切换后端只由 `PRODUCTDIRECTOR_DATABASE_URL` 决定。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

POSTGRES_PREFIXES = ("postgresql://", "postgres://")
SQLITE_PREFIX = "sqlite:///"


def database_url() -> str:
    return os.getenv("PRODUCTDIRECTOR_DATABASE_URL", "").strip()


def is_postgres(url: str | None = None) -> bool:
    target = database_url() if url is None else url
    return target.startswith(POSTGRES_PREFIXES)


def sqlite_path_from_url(url: str) -> str | None:
    """把 sqlite:///abs/path 还原成文件路径；非 SQLite URL 返回 None。"""
    if not url.startswith(SQLITE_PREFIX):
        return None
    parsed = urlsplit(url)
    path = parsed.path
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        path = f"//{parsed.netloc}{path}"
    return path or None


def to_postgres_sql(sql: str) -> str:
    """把 SQLite 方言改写成 PostgreSQL 方言（占位符、冲突处理、事务）。"""
    statement = sql
    # psycopg 使用 %-格式化，先把字面量 % 转义，再把 ? 换成 %s。
    statement = statement.replace("%", "%%")
    statement = statement.replace("?", "%s")
    if statement.strip().upper().startswith("INSERT OR IGNORE INTO"):
        head, _, tail = statement.partition("INTO")
        statement = f"INSERT{head[len('INSERT OR IGNORE'):]}INTO{tail}"
        if "ON CONFLICT" not in statement.upper():
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
        statement = "BEGIN"
    return statement


def split_script(script: str) -> list[str]:
    """按分号切分 SQL 脚本，忽略空语句。仅用于我们自己的建表脚本。"""
    return [chunk.strip() for chunk in script.split(";") if chunk.strip()]


class PostgresCursor:
    """让 psycopg 游标具备 sqlite3 游标的调用形状。"""

    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def __iter__(self):
        return iter(self._cursor)


class PostgresConnection:
    """sqlite3.Connection 的最小同形适配（PostgreSQL 侧）。"""

    def __init__(self, connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Sequence[Any] = ()) -> PostgresCursor:
        cursor = self._connection.cursor()
        cursor.execute(to_postgres_sql(sql), tuple(params) if params is not None else ())
        return PostgresCursor(cursor)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> PostgresCursor:
        cursor = self._connection.cursor()
        cursor.executemany(to_postgres_sql(sql), [tuple(row) for row in seq])
        return PostgresCursor(cursor)

    def executescript(self, script: str) -> None:
        with self._connection.cursor() as cursor:
            for statement in split_script(script):
                cursor.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        # 与 sqlite3 连接一致：正常退出提交，异常退出回滚。
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        return False


def connect(sqlite_db_path: str | Path) -> Any:
    """按环境变量返回连接：默认 SQLite，配置 PostgreSQL DSN 时返回 PG 适配器。"""
    url = database_url()
    if is_postgres(url):
        import psycopg  # 延迟导入：只有真的用 PostgreSQL 时才需要
        from psycopg.rows import dict_row

        return PostgresConnection(psycopg.connect(url, row_factory=dict_row))
    path = sqlite_path_from_url(url) if url else None
    connection = sqlite3.connect(path or str(sqlite_db_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def table_columns(db, table: str) -> list[str]:
    """跨方言读取列名（SQLite 用 PRAGMA，PostgreSQL 用 information_schema）。"""
    if isinstance(db, PostgresConnection):
        rows = db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [row["column_name"] for row in rows]
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]


def begin_writer(db) -> None:
    """开启写事务：SQLite 需要 IMMEDIATE 抢写锁，PostgreSQL 用普通事务。"""
    if isinstance(db, PostgresConnection):
        db.execute("BEGIN")
        return
    db.execute("BEGIN IMMEDIATE")
