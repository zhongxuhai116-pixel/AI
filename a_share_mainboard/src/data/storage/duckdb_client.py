from __future__ import annotations

from pathlib import Path

import duckdb

from infra.utils.io import ensure_dir


class DuckDBClient:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        ensure_dir(self.db_path.parent)
        self.connection = duckdb.connect(str(self.db_path))

    def execute(self, sql: str, params: tuple | None = None) -> None:
        if params is None:
            self.connection.execute(sql)
        else:
            self.connection.execute(sql, params)

    def fetch_df(self, sql: str, params: tuple | None = None):
        if params is None:
            return self.connection.execute(sql).fetchdf()
        return self.connection.execute(sql, params).fetchdf()

    def close(self) -> None:
        self.connection.close()

