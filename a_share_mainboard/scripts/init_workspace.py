from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from data.storage.duckdb_client import DuckDBClient
from data.storage.table_bootstrap import bootstrap_core_tables
from infra.config.loader import load_settings
from infra.utils.io import ensure_dir


def main() -> None:
    settings = load_settings(PROJECT_ROOT / "config")

    ensure_dir(PROJECT_ROOT / settings.app.data_root / "raw")
    ensure_dir(PROJECT_ROOT / settings.app.data_root / "ods")
    ensure_dir(PROJECT_ROOT / settings.app.data_root / "mart")
    ensure_dir(PROJECT_ROOT / settings.app.report_root)
    ensure_dir(PROJECT_ROOT / settings.app.log_root)

    db_client = DuckDBClient(PROJECT_ROOT / settings.data.duckdb_path)
    try:
        bootstrap_core_tables(db_client)
    finally:
        db_client.close()

    print({"status": "SUCCESS", "message": "Workspace initialized."})


if __name__ == "__main__":
    main()

