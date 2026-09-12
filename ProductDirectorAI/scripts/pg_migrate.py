#!/usr/bin/env python3
"""把 SQLite 原型库迁移到 PostgreSQL，并做逐表核对。

用途：A03 的隔离演练。它只读取 SQLite 源文件，不会修改源库；
目标库先按 `db/schema.postgres.sql` 建表，再按依赖顺序拷贝数据。
默认使用 ON CONFLICT DO NOTHING，因此可重复执行（幂等）。

用法：
    python scripts/pg_migrate.py --sqlite var/productdirector.db \
        --dsn "postgresql://productdirector@127.0.0.1:5432/productdirector_drill"

依赖：psycopg（见 apps/api/requirements-migrate.txt）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA_FILE = REPO / "db" / "schema.postgres.sql"

# 依赖顺序：被引用的表必须排在前面。
TABLES = [
    "owners",
    "workspaces",
    "projects",
    "assets",
    "product_versions",
    "plans",
    "plan_contracts",
    "jobs",
    "runs",
    "run_jobs",
    "job_attempts",
    "job_events",
    "auth_sessions",
    "provider_credentials",
    "provider_jobs",
]

# SQLite 没有 BOOLEAN/BYTEA，拷贝时按列名转换，避免驱动报类型错误。
BYTEA_COLUMNS = {"encrypted_key"}


def load_schema() -> str:
    return SCHEMA_FILE.read_text(encoding="utf-8")


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def migrate(sqlite_path: Path, dsn: str) -> dict:
    import psycopg  # 延迟导入：只有真正迁移时才需要

    sqlite_connection = sqlite3.connect(sqlite_path)
    sqlite_connection.row_factory = sqlite3.Row
    report: dict = {"sqlite": str(sqlite_path), "tables": {}, "ok": True}

    with psycopg.connect(dsn, autocommit=False) as pg:
        with pg.cursor() as cursor:
            cursor.execute(load_schema())
        for table in TABLES:
            columns = table_columns(sqlite_connection, table)
            if not columns:
                report["tables"][table] = {"source_rows": 0, "copied": 0, "target_rows": 0, "skipped": "表在源库不存在"}
                continue
            rows = sqlite_connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            placeholders = ", ".join(["%s"] * len(columns))
            statement = (
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            )
            payload = []
            for row in rows:
                values = []
                for column in columns:
                    value = row[column]
                    if column in BYTEA_COLUMNS and value is not None:
                        value = psycopg.Binary(value)
                    values.append(value)
                payload.append(tuple(values))
            copied = 0
            with pg.cursor() as cursor:
                for start in range(0, len(payload), 500):
                    chunk = payload[start:start + 500]
                    cursor.executemany(statement, chunk)
                    copied += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        pg.commit()
        with pg.cursor() as cursor:
            for table in TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                target_rows = cursor.fetchone()[0]
                source_rows = (
                    sqlite_connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if table_columns(sqlite_connection, table)
                    else 0
                )
                entry = report["tables"].setdefault(table, {})
                entry.update({"source_rows": source_rows, "target_rows": target_rows})
                entry["match"] = source_rows == target_rows
                if source_rows != target_rows:
                    report["ok"] = False
    sqlite_connection.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite -> PostgreSQL 迁移演练")
    parser.add_argument("--sqlite", required=True, help="SQLite 源库路径")
    parser.add_argument("--dsn", required=True, help="PostgreSQL 目标 DSN")
    args = parser.parse_args()

    report = migrate(Path(args.sqlite), args.dsn)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
